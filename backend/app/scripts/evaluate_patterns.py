"""Compare the rule patterns' alerts with the generator's ground-truth labels (TRD §10.6, SM-03, SM-05).

Reads `labels.csv` of a raw dataset and the alerts already in the database, so run it after loading and
ingesting that dataset and after detection has run (`python -m app.scripts.run_detection`, or `--run`
here). For each pattern it reports how many INJECTED_POSITIVE entities have an alert of that pattern,
how many EDGE_CASE (look-alike) entities do, and whether any CONTROL_CLEAN entity has any alert.

The labels are the generator's own, so this measures that the templates match the FRD definitions on
synthetic scenarios; it says nothing about detection performance on real data (L-01).

    python -m app.scripts.evaluate_patterns --raw-dir sample_data/raw/small
    python -m app.scripts.evaluate_patterns --raw-dir sample_data/raw/small --run
"""

import argparse
import csv
import time
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select

from app.database import SessionLocal
from app.models.alert import Alert
from app.models.customer import Customer
from app.models.merchant import Merchant
from app.services.detection.registry import run_all_rule_patterns


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--raw-dir", type=Path, required=True, help="dataset directory containing labels.csv")
    parser.add_argument("--run", action="store_true", help="run every rule pattern first (creates alerts)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.run:
            started = time.perf_counter()
            summaries = run_all_rule_patterns(db, actor=None)
            print(f"ran {len(summaries)} pattern(s) in {time.perf_counter() - started:.1f}s")

        refs = {c.id: c.customer_ref for c in db.scalars(select(Customer))}
        owner_of_merchant = {m.merchant_ref: refs[m.customer_id] for m in db.scalars(select(Merchant))}
        alerted: dict[str, set[str]] = defaultdict(set)  # "P01" -> customer refs with an alert of that pattern
        counts: dict[str, int] = defaultdict(int)
        for alert in db.scalars(select(Alert)):
            code = alert.pattern_code[:3]
            counts[code] += 1
            alerted[code].add(refs[alert.customer_id])
            alerted[code].update((alert.detection_details or {}).get("ENTITY_IDS", []))  # P09 lists every entity
        any_alert = set().union(*alerted.values()) if alerted else set()

        def has(code: str, entity: str) -> bool:
            return entity in alerted.get(code, set()) or owner_of_merchant.get(entity) in alerted.get(code, set())

        result: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
        control = []
        for label in csv.DictReader(open(args.raw_dir / "labels.csv", encoding="utf-8")):
            code, kind, entity = label["pattern_code"], label["label_type"], label["entity_source_id"]
            if kind == "CONTROL_CLEAN":
                control.append(entity)
            elif code.startswith("P") and kind in ("INJECTED_POSITIVE", "EDGE_CASE"):
                cell = result[code][kind]
                cell[1] += 1
                cell[0] += has(code, entity)

        print(f"\n{'pattern':8} {'positives caught':>18} {'look-alikes alerted':>22} {'alerts':>8}")
        for code in sorted(result):
            pos, edge = result[code]["INJECTED_POSITIVE"], result[code]["EDGE_CASE"]
            note = "" if counts.get(code) else "  (no alerts: pattern not built or nothing fired)"
            print(f"{code:8} {pos[0]:>10} / {pos[1]:<6} {edge[0]:>14} / {edge[1]:<5} {counts.get(code, 0):>8}{note}")
        flagged = [e for e in control if e in any_alert or owner_of_merchant.get(e) in any_alert]
        print(f"\ncontrol cohort: {len(flagged)} of {len(control)} CONTROL_CLEAN entities have an alert from any pattern")
        return 0 if not flagged else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
