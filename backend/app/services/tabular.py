"""Read an uploaded table — CSV or Parquet — into the same text cells.

Validation everywhere in FCIP works on the text a CSV would contain, so a
Parquet file is converted cell by cell into exactly that text: no value is
reinterpreted, rounded or given a time zone it did not have. Missing values
become empty cells, as in a CSV. The format is recognised from the file's
content (Parquet files start and end with `PAR1`), not from its name.

Conversion rules: booleans → `true`/`false`; integers as written; floats in
plain positional notation (never `1e+16`); decimals exactly; timestamps in ISO
8601, with their offset only when the file stores a time zone; dates as
`YYYY-MM-DD` (so they keep day precision); bytes as UTF-8 text. Columns that
pandas writes for its own row index (`__index_level_0__`) are not data and are
dropped.
"""

from __future__ import annotations

import csv
import datetime as dt
import decimal
import io
import json
import math
import re

import numpy as np
import pandas as pd

FORMAT_CSV = "csv"
FORMAT_PARQUET = "parquet"
PARQUET_MAGIC = b"PAR1"
_PANDAS_INDEX = re.compile(r"__index_level_\d+__")


class TableFileError(ValueError):
    """The file cannot be read as a table; the message is shown to the user."""


def detect_format(raw_bytes: bytes) -> str:
    if len(raw_bytes) >= 8 and raw_bytes[:4] == PARQUET_MAGIC and raw_bytes[-4:] == PARQUET_MAGIC:
        return FORMAT_PARQUET
    return FORMAT_CSV


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (bool, np.bool_)):
        return "true" if value else "false"
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if isinstance(value, (float, np.floating)):
        value = float(value)
        if math.isnan(value):
            return ""
        return np.format_float_positional(value, trim="-") if math.isfinite(value) else str(value)
    if isinstance(value, decimal.Decimal):
        return format(value, "f")
    if isinstance(value, pd.Timestamp):
        return "" if pd.isna(value) else value.isoformat()
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, default=str, ensure_ascii=False)
    return str(value)


def parquet_to_frame(raw_bytes: bytes) -> pd.DataFrame:
    import pyarrow as pa
    import pyarrow.parquet as pq

    try:
        table = pq.read_table(io.BytesIO(raw_bytes))
    except (pa.ArrowException, OSError, ValueError) as exc:
        raise TableFileError(f"File Parquet tidak dapat dibaca: {exc}") from exc
    names = [name for name in table.column_names if not _PANDAS_INDEX.fullmatch(str(name))]
    if not names:
        raise TableFileError("File Parquet tidak memiliki kolom data.")
    if len(names) != len(set(names)):
        raise TableFileError("Nama kolom ganda membuat pemetaan ambigu.")
    columns = {}
    for name in names:
        try:
            values = table.column(name).to_pylist()
        except (pa.ArrowException, ValueError, OverflowError) as exc:
            raise TableFileError(f"Kolom {name} tidak dapat dibaca: {exc}") from exc
        columns[str(name)] = [_text(v) for v in values]
    return pd.DataFrame(columns, dtype=object)


def parquet_to_csv_text(raw_bytes: bytes) -> str:
    """The same rows as CSV text, for code paths that read CSV."""
    frame = parquet_to_frame(raw_bytes)
    out = io.StringIO()
    frame.to_csv(out, index=False, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    return out.getvalue()
