"""Shared helpers for the model lab tests."""

import csv
import io
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from app.ml.lab.mapping import suggest

WIB = timezone(timedelta(hours=7))


def make_dataset(rows: int = 3000, *, seed: int = 7, defects: bool = True) -> bytes:
    """A small labelled dataset with a learnable signal, in a bank-like column layout."""
    rng = np.random.default_rng(seed)
    start = datetime(2026, 4, 1, tzinfo=WIB)
    records = []
    for i in range(rows):
        account = f"ACC-{rng.integers(1, 150):04d}"
        positive = rng.random() < 0.05
        amount = float(np.round(rng.lognormal(11, 0.6) * (25 if positive else 1), 2))
        moment = start + timedelta(days=float(rng.uniform(0, 150)))
        records.append({
            "TxnID": f"T{i:06d}", "AccountID": account, "Timestamp": moment.isoformat(timespec="seconds"),
            "Amount": f"{amount:.2f}", "Direction": "OUT" if positive or rng.random() < 0.5 else "IN",
            "Channel": "WEB" if positive else rng.choice(["APP", "APP", "WEB"]),
            "DeviceID": f"DEV-NEW-{i}" if positive else f"DEV-{account}", "Currency": "IDR",
            "is_fraud": "yes" if positive else "no", "pattern": "X1" if positive else "",
        })
    if defects:
        records.append({**records[0], "TxnID": "T-BAD-DATE", "Timestamp": "2026-13-45"})
        records.append({**records[1]})  # duplicate transaction id: quarantined
        records.append({**records[2], "TxnID": "T-NO-LABEL", "is_fraud": ""})
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(records[0]))
    writer.writeheader()
    writer.writerows(records)
    return out.getvalue().encode()


def mapping_for(raw: pd.DataFrame) -> dict:
    s = suggest(raw)
    return {"fields": s["fields"], "label_column": s["label_column"], "positive_values": s["positive_values"],
            "category_column": s["category_column"], "date_format": s["date_format"]}
