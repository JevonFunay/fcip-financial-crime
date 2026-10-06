"""Run detection over all ingested transactions: every ACTIVE rule, then every
ACTIVE model version (TRD A22), as the Overview page's button does.

Run via: docker compose exec backend python -m app.scripts.run_detection [--rules-only]
Safe to re-run: alerts and scores already written for the same evidence are
reported, not duplicated.
"""

import argparse

from app.database import SessionLocal
from app.services.detection.p02_structuring import run_p02_structuring
from app.services.ml_scoring import run_ml_scoring


def run(rules_only: bool = False) -> None:
    db = SessionLocal()
    try:
        summary = run_p02_structuring(db)
        ml = None if rules_only else run_ml_scoring(db)
    finally:
        db.close()

    print(f"{summary.pattern_code} run {summary.detection_run_id}")
    rules = ", ".join(f"{r.rule_ref} v{r.version}" for r in summary.rules_evaluated)
    print(f"rule versions evaluated: {rules or 'none (no ACTIVE rule version)'}")
    print(f"alerts created: {summary.alerts_created}, already existing: {summary.alerts_already_existing}")
    for alert in summary.alerts:
        status = "NEW" if alert.created else "existing"
        print(
            f"  [{status}] {alert.rule_ref} v{alert.rule_version} {alert.customer_ref}: {alert.txn_count_in_band} txns, "
            f"aggregate {alert.aggregate_amount:,.0f}, window {alert.window_start.date()} -> {alert.window_end.date()} "
            f"(alert {alert.alert_id})"
        )
    if ml is None:
        return
    print(f"\nML scoring run {ml.detection_run_id} ({ml.seconds}s)")
    if not ml.models:
        print("  no ACTIVE model version: register one with python -m app.scripts.register_models")
    for r in ml.models:
        print(f"  {r.model_version} ({r.unit}): {r.units_scored:,} scored, {r.units_flagged:,} at or above "
              f"{r.threshold:.3f}, {r.scores_stored:,} new score(s) stored")
    if ml.ml_rule_versions_evaluated:
        print(f"  ML_SCORE {', '.join(ml.ml_rule_versions_evaluated)}: {ml.alerts_created} alert(s) created, "
              f"{ml.alerts_already_existing} already existing")
    else:
        print("  ML_SCORE rule is not ACTIVE: scores are shown as signals, no alert is raised from a score (CF-05)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run every ACTIVE rule and model version.")
    parser.add_argument("--rules-only", action="store_true")
    run(parser.parse_args().rules_only)
