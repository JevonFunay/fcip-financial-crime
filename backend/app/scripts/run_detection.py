"""Manually trigger P02 Structuring detection over all ingested transactions.

Run via: docker compose exec backend python -m app.scripts.run_detection
Safe to re-run: alerts already raised for the same evidence are reported, not duplicated.
"""

from app.database import SessionLocal
from app.services.detection.p02_structuring import run_p02_structuring


def run() -> None:
    db = SessionLocal()
    try:
        summary = run_p02_structuring(db)
    finally:
        db.close()

    print(f"{summary.pattern_code} run {summary.detection_run_id}")
    print(f"alerts created: {summary.alerts_created}, already existing: {summary.alerts_already_existing}")
    for alert in summary.alerts:
        status = "NEW" if alert.created else "existing"
        print(
            f"  [{status}] {alert.customer_ref}: {alert.txn_count_in_band} txns, "
            f"aggregate {alert.aggregate_amount:,.0f}, window {alert.window_start.date()} -> {alert.window_end.date()} "
            f"(alert {alert.alert_id})"
        )


if __name__ == "__main__":
    run()
