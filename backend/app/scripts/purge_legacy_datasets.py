"""Review and remove only the old, explicitly named local dataset files.

The user authorised this replacement on 14 October 2026. This utility never
connects to a database and never deletes model artefacts, source code, the new
Kaggle input, authentication/audit data or private configuration backups.
Plan first; execution refuses any changed file, added file or symlink.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


LEGACY_PATHS = (
    "backend/sample_data/Tiny",
    "backend/sample_data/Small",
    "backend/sample_data/Full",
    "backend/sample_data/transactions_sample.csv",
    "backend/sample_data/transactions_bulk.csv",
    "backend/sample_data/raw/custom-100000",
    "backend/sample_data/raw/full",
    "backend/sample_data/raw/small",
    "backend/sample_data/raw/tiny",
    "backend/sample_data/raw/manual_full_s20261007_web",
    "backend/ml_data/train_full_s20260923",
    "backend/ml_data/test_full_s20261001",
    "backend/ml_data/manual_full_s20261007",
)
PROTECTED_PATHS = (
    "backend/sample_data/raw/archive.zip",
    "backend/sample_data/raw/bank_transactions_data_2_augmented_clean_2.csv",
    "backend/ml_models",
    "backend/app/reference",
    "backend/local_backups",
)


class PurgeRefused(ValueError):
    pass


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _safe_files(root: Path, relative: str) -> list[Path]:
    path = root / relative
    for part in (path, *path.parents):
        if part == root.parent:
            break
        if part.is_symlink():
            raise PurgeRefused(f"symlink is not an authorised dataset: {part.relative_to(root)}")
    if not path.exists():
        return []
    candidates = sorted(path.rglob("*")) if path.is_dir() else [path]
    files = []
    for candidate in candidates:
        if candidate.is_symlink():
            raise PurgeRefused(f"symlink in dataset: {candidate.relative_to(root)}")
        if candidate.is_file():
            files.append(candidate)
        elif not candidate.is_dir():
            raise PurgeRefused(f"not a regular file or directory: {candidate.relative_to(root)}")
    return files


def _entries(root: Path, relative: str) -> list[dict]:
    return [{"path": str(p.relative_to(root)), "size_bytes": p.stat().st_size, "sha256": _digest(p)}
            for p in _safe_files(root, relative)]


def _plan_hash(plan: dict) -> str:
    payload = {k: v for k, v in plan.items() if k != "plan_sha256"}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_plan(repo_root: Path) -> dict:
    root = repo_root.resolve(strict=True)
    targets = []
    for relative in LEGACY_PATHS:
        files = _entries(root, relative)
        targets.append({"path": relative, "exists": (root / relative).exists(), "files": files,
                        "file_count": len(files), "size_bytes": sum(f["size_bytes"] for f in files)})
    protected = [entry for relative in PROTECTED_PATHS for entry in _entries(root, relative)]
    plan = {
        "schema_version": 1, "repo_root": str(root), "operation": "purge_explicit_legacy_dataset_files",
        "authorisation": "User requested replacement and deletion of old datasets, 2026-10-14",
        "database_touched": False, "targets": targets, "protected_files": protected,
        "file_count": sum(t["file_count"] for t in targets),
        "size_bytes": sum(t["size_bytes"] for t in targets),
    }
    plan["plan_sha256"] = _plan_hash(plan)
    return plan


def write_plan(repo_root: Path, manifest_path: Path) -> dict:
    plan = build_plan(repo_root)
    manifest_path = manifest_path.resolve()
    root = Path(plan["repo_root"])
    if any(manifest_path == root / p or (root / p) in manifest_path.parents for p in LEGACY_PATHS):
        raise PurgeRefused("the purge manifest must be outside every legacy dataset target")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("x", encoding="utf-8") as handle:
        json.dump(plan, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return plan


def execute_plan(manifest_path: Path, *, expected_plan_sha256: str) -> dict:
    plan = json.loads(manifest_path.read_text(encoding="utf-8"))
    if plan.get("schema_version") != 1 or plan.get("operation") != "purge_explicit_legacy_dataset_files":
        raise PurgeRefused("unknown purge plan")
    if _plan_hash(plan) != expected_plan_sha256 or plan.get("plan_sha256") != expected_plan_sha256:
        raise PurgeRefused("the reviewed plan checksum does not match")
    if tuple(t.get("path") for t in plan["targets"]) != LEGACY_PATHS:
        raise PurgeRefused("the plan does not contain exactly the fixed dataset allowlist")
    root = Path(plan["repo_root"]).resolve(strict=True)
    # Complete preflight before the first unlink: no partial deletion on a
    # changed source, a newly added file, or an edited/tampered plan.
    for target in plan["targets"]:
        if (root / target["path"]).exists() != target["exists"] or _entries(root, target["path"]) != target["files"]:
            raise PurgeRefused(f"dataset changed after planning: {target['path']}")
    for entry in plan["protected_files"]:
        path = root / entry["path"]
        if path.is_symlink() or not path.is_file() or path.stat().st_size != entry["size_bytes"] or _digest(path) != entry["sha256"]:
            raise PurgeRefused(f"protected file changed after planning: {entry['path']}")
    result_path = manifest_path.with_name(manifest_path.stem + ".executed.json")
    if result_path.exists():
        raise PurgeRefused("an execution report already exists; this plan is single-use")
    deleted = []
    for target in plan["targets"]:
        path = root / target["path"]
        for entry in target["files"]:
            (root / entry["path"]).unlink()
            deleted.append(entry)
        if path.is_dir():
            for child in sorted((p for p in path.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
                child.rmdir()
            path.rmdir()
    report = {"schema_version": 1, "plan_sha256": expected_plan_sha256, "database_touched": False,
              "deleted_files": deleted, "deleted_file_count": len(deleted),
              "deleted_size_bytes": sum(f["size_bytes"] for f in deleted),
              "protected_files_verified": len(plan["protected_files"])}
    with result_path.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--execute", action="store_true", help="execute the existing reviewed manifest")
    parser.add_argument("--confirm-sha256", help="exact plan_sha256 printed by the planning command")
    args = parser.parse_args()
    try:
        if args.execute:
            if not args.confirm_sha256:
                parser.error("--execute requires --confirm-sha256")
            result = execute_plan(args.manifest, expected_plan_sha256=args.confirm_sha256)
        else:
            result = write_plan(args.repo_root, args.manifest)
        print(json.dumps({k: v for k, v in result.items() if k not in {"targets", "protected_files", "deleted_files"}}, indent=2))
    except (PurgeRefused, FileExistsError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
