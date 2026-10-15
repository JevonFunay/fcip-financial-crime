import json
from pathlib import Path

import pytest

from app.scripts.purge_legacy_datasets import PurgeRefused, execute_plan, write_plan


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for relative in ["backend/sample_data/Tiny/transactions.csv", "backend/ml_data/train_full_s20260923/features/x.csv",
                     "backend/sample_data/raw/bank_transactions_data_2_augmented_clean_2.csv",
                     "backend/sample_data/raw/archive.zip", "backend/ml_models/model.txt",
                     "backend/app/reference/config.json", "backend/local_backups/env.before",
                     "backend/app/ml/source.py", "backend/ml_data/new_behavioral/transactions.csv"]:
        p = root / relative
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(relative, encoding="utf-8")
    return root


def test_only_reviewed_legacy_paths_are_deleted_and_protected_files_survive(tmp_path):
    root = _repo(tmp_path)
    manifest = tmp_path / "plan.json"
    plan = write_plan(root, manifest)
    protected = {e["path"]: (root / e["path"]).read_bytes() for e in plan["protected_files"]}
    report = execute_plan(manifest, expected_plan_sha256=plan["plan_sha256"])
    assert report["deleted_file_count"] == 2
    assert report["database_touched"] is False
    assert not (root / "backend/sample_data/Tiny").exists()
    assert not (root / "backend/ml_data/train_full_s20260923").exists()
    assert (root / "backend/app/ml/source.py").exists()
    assert (root / "backend/ml_data/new_behavioral/transactions.csv").exists()
    assert protected == {relative: (root / relative).read_bytes() for relative in protected}
    assert json.loads((tmp_path / "plan.executed.json").read_text()) == report


@pytest.mark.parametrize("change", ["edit", "add", "protected"])
def test_any_change_refuses_before_deleting_a_single_file(tmp_path, change):
    root = _repo(tmp_path)
    manifest = tmp_path / "plan.json"
    plan = write_plan(root, manifest)
    relative = {"edit": "backend/sample_data/Tiny/transactions.csv", "add": "backend/sample_data/Tiny/extra.csv",
                "protected": "backend/ml_models/model.txt"}[change]
    (root / relative).write_text("changed")
    with pytest.raises(PurgeRefused, match="changed after planning"):
        execute_plan(manifest, expected_plan_sha256=plan["plan_sha256"])
    assert (root / "backend/sample_data/Tiny/transactions.csv").exists()
    assert (root / "backend/ml_data/train_full_s20260923/features/x.csv").exists()


def test_symlinks_and_unreviewed_plan_checksums_are_refused(tmp_path):
    root = _repo(tmp_path)
    manifest = tmp_path / "plan.json"
    plan = write_plan(root, manifest)
    with pytest.raises(PurgeRefused, match="checksum"):
        execute_plan(manifest, expected_plan_sha256="0" * 64)
    (root / "backend/sample_data/Small").symlink_to(root / "backend/ml_models", target_is_directory=True)
    with pytest.raises(PurgeRefused, match="symlink"):
        write_plan(root, tmp_path / "second.json")
    assert (root / "backend/ml_models/model.txt").exists()


def test_plan_cannot_overwrite_a_manifest_or_live_inside_its_target(tmp_path):
    root = _repo(tmp_path)
    manifest = tmp_path / "plan.json"
    write_plan(root, manifest)
    before = manifest.read_bytes()
    with pytest.raises(FileExistsError):
        write_plan(root, manifest)
    assert manifest.read_bytes() == before
    with pytest.raises(PurgeRefused, match="outside"):
        write_plan(root, root / "backend/sample_data/Tiny/plan.json")
