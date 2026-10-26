"""Explicit local dataset reset authorised by Jevon on 14 October 2026.

Keeps authentication, sessions, audit history, rules and immutable model
versions. Never drops databases, changes triggers or uses TRUNCATE CASCADE.
The reviewed count/identity snapshot is checked again inside the transaction.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import uuid
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.config import Settings

DATA_TABLES = ("evidence", "alert_transaction", "case", "alert", "ml_score", "transaction", "processing_log",
               "quarantine_item", "ingestion_batch", "account", "merchant", "device", "customer")
KNOWN_DEMOS = {"fcip", "fcip_demo_s20261007"}
KEPT_TABLES = ("users", "sessions", "audit_log", "model_version", "rule", "rule_version", "simulation_result")


def _allowed(name: str) -> bool:
    return name in KNOWN_DEMOS or bool(re.fullmatch(r"fcip_[a-zA-Z0-9_]+_test", name)) or name == "fcip_test"


def _snapshot(conn) -> dict:
    tables = set(inspect(conn).get_table_names(schema="public"))
    data = {name: conn.execute(text(f'SELECT count(*) FROM public."{name}"')).scalar_one()
            for name in DATA_TABLES if name in tables}
    data_hashes = {}
    for name in data:
        digest = hashlib.sha256()
        rows = conn.execute(text(f'SELECT row_to_json(t)::text FROM public."{name}" t ORDER BY 1')
                            .execution_options(stream_results=True))
        for row in rows.scalars():
            digest.update(row.encode())
            digest.update(b"\n")
        data_hashes[name] = digest.hexdigest()
    kept = {}
    for name in KEPT_TABLES:
        if name in tables:
            # A digest proves these records have not been silently changed;
            # no credentials, tokens, model text or audit contents are printed.
            rows = conn.execute(text(f'SELECT row_to_json(t)::text FROM public."{name}" t ORDER BY 1')).scalars()
            digest = hashlib.sha256()
            count = 0
            for row in rows:
                digest.update(row.encode())
                digest.update(b"\n")
                count += 1
            kept[name] = {"rows": count, "sha256": digest.hexdigest()}
    return {"dataset_rows": data, "dataset_sha256": data_hashes, "preserved": kept}


def reset_database(name: str, *, expected: dict | None = None) -> dict:
    if not _allowed(name):
        raise ValueError("Only named FCIP demos and FCIP test databases may be reset")
    url = make_url(Settings().database_url).set(database=name)
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL lock_timeout = '5s'"))
            conn.execute(text("SET LOCAL statement_timeout = '120s'"))
            if expected is not None:
                tables = set(inspect(conn).get_table_names(schema="public"))
                targets = sorted(set(DATA_TABLES) & tables)
                kept = sorted(set(KEPT_TABLES) & tables)
                if targets:
                    conn.execute(text("LOCK TABLE " + ", ".join(f'public."{n}"' for n in targets)
                                      + " IN ACCESS EXCLUSIVE MODE"))
                if kept:
                    conn.execute(text("LOCK TABLE " + ", ".join(f'public."{n}"' for n in kept)
                                      + " IN SHARE ROW EXCLUSIVE MODE"))
            before = _snapshot(conn)
            if expected is None:
                return before
            if before != expected:
                raise ValueError(f"{name}: data changed after the reviewed reset snapshot")
            names = list(before["dataset_rows"])
            if names:
                conn.execute(text("TRUNCATE TABLE " + ", ".join(f'public."{n}"' for n in names) + " RESTART IDENTITY"))
            after = _snapshot(conn)
            if before["preserved"] != after["preserved"] or any(after["dataset_rows"].values()):
                raise ValueError("Dataset reset failed its preservation check; transaction rolled back")
            # Audit is deliberately appended, never deleted. Old object IDs
            # remain historical facts even though their dummy rows are removed.
            if "audit_log" in before["preserved"]:
                conn.execute(text('''INSERT INTO audit_log
                    (id, correlation_id, object_type, object_id, from_state, to_state, reason)
                    VALUES (:id, :correlation, 'DATASET_RESET', :object, 'POPULATED', 'CLEARED', :reason)'''),
                    {"id": uuid.uuid4(), "correlation": uuid.uuid4(), "object": uuid.uuid4(),
                     "reason": "User-authorised replacement of old dummy datasets (14 October 2026); users, sessions, rules, models and prior audit retained."})
            return {"database": name, "before": before, "after": after,
                    "audit_event_appended": "audit_log" in before["preserved"]}
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", action="append", required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.execute:
        output = args.plan.with_name(args.plan.stem + ".executed.json")
        if output.exists():
            raise SystemExit("Refusing to overwrite a reset report")
        plan = json.loads(args.plan.read_text())
        if plan.get("databases") != args.database:
            raise SystemExit("Requested databases differ from the explicit plan")
        results = {name: reset_database(name, expected=plan["snapshots"][name]) for name in args.database}
    else:
        if args.plan.exists():
            raise SystemExit("Refusing to overwrite a reset plan")
        results = {"databases": args.database, "snapshots": {name: reset_database(name) for name in args.database}}
        output = args.plan
    if output.exists():
        raise SystemExit("Refusing to overwrite a reset report")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps({"report": str(output), "databases": args.database, "executed": args.execute}))


if __name__ == "__main__":
    main()
