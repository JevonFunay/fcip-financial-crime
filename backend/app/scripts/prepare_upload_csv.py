"""Prepare raw synthetic data for the existing browser CSV upload.

    python -m app.scripts.prepare_upload_csv --raw-dir ml_data/manual_full_s20261007 \
        --out-dir ml_data/manual_full_s20261007_upload

This is a file adapter, not ingestion or training. It uses the existing bridge
unchanged, preserves its row order and defects, and repeats the header in
byte-bounded parts. All parts must be uploaded in order before detection runs:
stored model scores are immutable, so a partial-history run is not a final run.
Nothing is published until the parts have been verified against the projection.
An existing destination is refused rather than overwritten.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from itertools import zip_longest
from pathlib import Path
import tempfile

from app.routers.ingestion import MAX_UPLOAD_BYTES
from app.scripts.load_raw_dataset import APP_COLUMNS, DatasetRejected, check_manifest, project_transactions


class UploadPreparationError(Exception):
    """No safe, complete upload directory could be prepared."""


def _csv_bytes(row: list[str] | tuple[str, ...]) -> bytes:
    buffer = io.StringIO(newline="")
    csv.writer(buffer, lineterminator="\n").writerow(row)
    return buffer.getvalue().encode("utf-8")


def _sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _split(projection: Path, destination: Path, max_bytes: int) -> list[dict]:
    """Split complete CSV records; quoted newlines never become boundaries."""
    header = _csv_bytes(APP_COLUMNS)
    if max_bytes < len(header):
        raise UploadPreparationError("the upload byte limit is smaller than the CSV header")
    parts = []
    handle = None
    current = None

    def open_part() -> None:
        nonlocal handle, current
        name = f"transactions_{len(parts) + 1:03d}.csv"
        handle = (destination / name).open("xb")
        handle.write(header)
        current = {"name": name, "record_count": 0, "size_bytes": len(header)}

    def close_part() -> None:
        nonlocal handle
        if handle is not None:
            handle.close()
            handle = None
            current["sha256"] = _sha256(destination / current["name"])
            parts.append(current)

    try:
        with projection.open(encoding="utf-8", newline="") as source:
            reader = csv.reader(source)
            if next(reader, None) != list(APP_COLUMNS):
                raise UploadPreparationError("the bridge projection has an unexpected header")
            open_part()
            for row_number, row in enumerate(reader, start=2):
                payload = _csv_bytes(row)
                if len(header) + len(payload) > max_bytes:
                    raise UploadPreparationError(
                        f"CSV record {row_number} cannot fit in one upload: header plus record is "
                        f"{len(header) + len(payload):,} bytes, limit {max_bytes:,}"
                    )
                if current["size_bytes"] + len(payload) > max_bytes:
                    close_part()
                    open_part()
                handle.write(payload)
                current["record_count"] += 1
                current["size_bytes"] += len(payload)
        close_part()
    finally:
        if handle is not None:
            handle.close()
    return parts


def _verify(projection: Path, destination: Path, parts: list[dict], expected_rows: int) -> str:
    """Compare every parsed row and the CSV bytes with the complete projection."""
    digest = hashlib.sha256(_csv_bytes(APP_COLUMNS))
    count = 0

    def part_rows():
        for part in parts:
            path = destination / part["name"]
            if path.stat().st_size != part["size_bytes"] or _sha256(path) != part["sha256"]:
                raise UploadPreparationError(f"part verification failed: {part['name']}")
            part_count = 0
            with path.open(encoding="utf-8", newline="") as handle:
                reader = csv.reader(handle)
                if next(reader, None) != list(APP_COLUMNS):
                    raise UploadPreparationError(f"unexpected header: {part['name']}")
                for row in reader:
                    part_count += 1
                    yield row
            if part_count != part["record_count"]:
                raise UploadPreparationError(f"record count mismatch: {part['name']}")

    with projection.open(encoding="utf-8", newline="") as handle:
        source = csv.reader(handle)
        if next(source, None) != list(APP_COLUMNS):
            raise UploadPreparationError("the bridge projection has an unexpected header")
        for original, reassembled in zip_longest(source, part_rows()):
            if original != reassembled:
                raise UploadPreparationError(f"reassembled CSV differs at data record {count + 1}")
            count += 1
            digest.update(_csv_bytes(reassembled))
    if count != expected_rows:
        raise UploadPreparationError(f"expected {expected_rows:,} projected rows, verified {count:,}")
    result = digest.hexdigest()
    if result != _sha256(projection):
        raise UploadPreparationError("reassembled normalized CSV checksum differs from the bridge projection")
    return result


def prepare_upload_csv(raw_dir: Path, out_dir: Path, *, max_bytes: int = MAX_UPLOAD_BYTES) -> dict:
    """Publish a new directory atomically; no source file or database is changed.

    A smaller max_bytes is useful for testing record boundaries. The CLI always
    uses the existing API cap, and no caller may prepare parts above that cap.
    """
    if not 0 < max_bytes <= MAX_UPLOAD_BYTES:
        raise UploadPreparationError(f"max_bytes must be between 1 and the API cap ({MAX_UPLOAD_BYTES:,})")
    if out_dir.exists() or out_dir.is_symlink():
        raise UploadPreparationError(f"destination already exists; refusing to overwrite: {out_dir}")
    manifest, warnings = check_manifest(raw_dir)
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{out_dir.name}-", dir=out_dir.parent) as temporary:
        staging = Path(temporary)
        projection = staging / "projection.csv"
        stats = project_transactions(raw_dir, projection)
        publish = staging / "upload"
        publish.mkdir()
        parts = _split(projection, publish, max_bytes)
        reassembled_sha256 = _verify(projection, publish, parts, stats["written"])
        report = {
            "schema_version": 1,
            "synthetic_declaration": True,
            "seed": manifest.get("seed"),
            "generator_version": manifest.get("generator_version"),
            "profile": manifest.get("profile"),
            # The generator's reference date is its manifest business_date.
            "reference_date": manifest["business_date"],
            "business_date": manifest["business_date"],
            "source_system_code": manifest["source_system_code"],
            "contract_version": manifest.get("contract_version"),
            "source_manifest_sha256": _sha256(raw_dir / "manifest.json"),
            "raw_transaction_count": stats["total"],
            "projected_transaction_count": stats["written"],
            "non_idr_rows_dropped": stats["non_idr"],
            "max_upload_bytes": max_bytes,
            "projected_sha256": _sha256(projection),
            "reassembled_sha256": reassembled_sha256,
            "reassembly_verified": True,
            "warnings": warnings,
            "parts": parts,
            "upload_instructions": "Upload every part in the listed order before running detection. "
                                   "Expected records for each batch equals that part's record_count.",
        }
        (publish / "upload_manifest.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        if out_dir.exists() or out_dir.is_symlink():
            raise UploadPreparationError(f"destination appeared during preparation: {out_dir}")
        publish.rename(out_dir)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare unchanged bridge CSV parts for the browser upload.")
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True, help="new output directory; existing paths are refused")
    args = parser.parse_args()
    try:
        report = prepare_upload_csv(args.raw_dir, args.out_dir)
    except (DatasetRejected, UploadPreparationError, OSError, ValueError, csv.Error) as exc:
        raise SystemExit(f"REFUSED: {exc}") from exc
    print(f"Prepared {report['projected_transaction_count']:,} rows from seed {report['seed']} into {args.out_dir}")
    for part in report["parts"]:
        print(f"  {part['name']}: {part['record_count']:,} rows, {part['size_bytes']:,} bytes")
    print("Reassembled CSV verified against the unchanged bridge projection.")
    print(report["upload_instructions"])


if __name__ == "__main__":
    main()
