"""Explicit CSV profiles, not guesses about banking semantics.

The same canonical row validation is used offline and at upload. Unknown
currency is ISO 4217 XXX, never IDR; the Kaggle profile's USD was confirmed by
the user. Naive dates carry UTC solely for storage and retain their precision
and unknown source timezone. Demographics are event metadata, not customer
master facts. Unknown and target columns are preserved only as provenance.
"""

from __future__ import annotations

import csv
import hashlib
import ipaddress
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROFILE_ROOT = Path(__file__).resolve().parents[1] / "profiles"
TEXT_COLUMNS = (
    "transaction_ref", "account_number", "currency", "direction", "channel", "device_ref",
    "merchant_ref", "location", "ip_address", "customer_occupation", "counterparty_ref",
    "source_status", "description", "transaction_type", "counterparty_country",
)
NUMERIC_COLUMNS = ("amount", "login_attempts", "transaction_duration", "account_balance", "customer_age")
CANONICAL_COLUMNS = (
    "transaction_ref", "account_number", "transaction_date", "amount", "currency", "direction", "channel",
    "device_ref", "merchant_ref", "location", "ip_address", "login_attempts", "transaction_duration",
    "account_balance", "customer_age", "customer_occupation", "timestamp_precision", "source_timezone_known",
    "counterparty_ref", "source_status", "description", "transaction_type", "counterparty_country",
)
REQUIRED = ("transaction_ref", "account_number", "transaction_date", "amount", "direction", "channel")
PROVENANCE_COLUMNS = ("source_ordinal", "source_original", "schema_profile")
_OFFSET = re.compile(r"(?:Z|[+-]\d{2}:?\d{2})$", re.I)
_MONEY = re.compile(r"[0-9]+(?:\.[0-9]{1,2})?")


class NormalizationError(ValueError):
    """The file contract is ambiguous, missing, or structurally unreadable."""


@dataclass
class NormalizationResult:
    frame: pd.DataFrame
    report: dict[str, Any]
    quarantine: pd.DataFrame


def _name(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lstrip("\ufeff").strip().lower())


def load_profile(profile: str | Path | dict = "kaggle_bank_v1") -> dict:
    if isinstance(profile, dict):
        data = json.loads(json.dumps(profile))
    elif str(profile) in {"canonical", "fcip", "fcip_canonical_v1"}:
        data = {
            "version": "fcip_canonical_v1", "default_currency": "XXX", "source_timezone": None,
            "currency_provenance": "Explicit currency is retained; absent currency is unknown (XXX), not converted.",
            "date_format": "iso", "columns": {c: {"aliases": [], "required": c in REQUIRED}
                                                  for c in CANONICAL_COLUMNS},
        }
    else:
        path = Path(profile)
        if not path.is_file():
            path = PROFILE_ROOT / f"{profile}.json"
        if not path.is_file():
            raise NormalizationError(f"Unknown schema profile: {profile}")
        data = json.loads(path.read_text(encoding="utf-8"))
    if not data.get("version") or not isinstance(data.get("columns"), dict):
        raise NormalizationError("A profile needs a version and explicit column mapping")
    if set(data["columns"]) - set(CANONICAL_COLUMNS):
        raise NormalizationError("Profile may map only allow-listed canonical observation columns")
    return data


def _strings(series: pd.Series) -> pd.Series:
    return series.astype("string").fillna("").str.strip()


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (np.integer, np.floating)):
        value = value.item()
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if value is None or (not isinstance(value, (dict, list)) and pd.isna(value)):
        return None
    return value


def normalize_frame(frame: pd.DataFrame, profile: str | Path | dict = "kaggle_bank_v1") -> NormalizationResult:
    spec = load_profile(profile)
    raw = frame.copy().reset_index(drop=True)
    if raw.columns.duplicated().any():
        raise NormalizationError("Duplicate CSV column names are ambiguous")
    names = [_name(c) for c in raw.columns]
    if len(names) != len(set(names)):
        raise NormalizationError("Column names collide after whitespace/BOM/case normalisation")
    actual = dict(zip(names, raw.columns))
    mapping: dict[str, str] = {}
    for canonical, config in spec["columns"].items():
        candidates = {_name(canonical), *(_name(a) for a in config.get("aliases", []))}
        matches = [actual[a] for a in candidates if a in actual]
        if len(matches) > 1:
            raise NormalizationError(f"Ambiguous aliases for {canonical}: {', '.join(matches)}")
        if matches:
            mapping[canonical] = matches[0]
        elif config.get("required"):
            raise NormalizationError(f"Missing required column: {canonical}")
    if len(set(mapping.values())) != len(mapping):
        raise NormalizationError("One input column cannot map to multiple canonical fields")
    missing = [name for name in REQUIRED if name not in mapping]
    if missing:
        raise NormalizationError(f"Missing required mappings: {', '.join(missing)}")

    out = pd.DataFrame(index=raw.index)
    for col in CANONICAL_COLUMNS:
        out[col] = raw[mapping[col]] if col in mapping else pd.Series("", index=raw.index, dtype="string")
    reasons: list[list[str]] = [[] for _ in range(len(raw))]

    def reject(mask, reason: str) -> None:
        for i in np.flatnonzero(np.asarray(mask, dtype=bool)):
            reasons[i].append(reason)

    for col in TEXT_COLUMNS:
        out[col] = _strings(out[col])
    for col in REQUIRED:
        reject(_strings(out[col]).eq(""), f"{col} is required")
    if "currency" not in mapping:
        out["currency"] = spec.get("default_currency", "XXX")
    else:
        out["currency"] = out.currency.mask(out.currency.eq(""), "XXX")
    out["currency"] = out.currency.str.upper()
    reject(~out.currency.str.fullmatch(r"[A-Z]{3}"), "currency must be a 3-letter code or XXX")
    out["direction"] = out.direction.str.upper().replace({"IN": "CREDIT", "OUT": "DEBIT"})
    reject(~out.direction.isin(["CREDIT", "DEBIT"]), "direction must be CREDIT or DEBIT")
    out["channel"] = out.channel.str.upper()
    out["source_status"] = out.source_status.str.upper()
    out["transaction_type"] = out.transaction_type.str.upper()
    out["counterparty_country"] = out.counterparty_country.str.upper()
    country_present = out.counterparty_country.ne("")
    reject(country_present & ~out.counterparty_country.str.fullmatch(r"[A-Z]{2}"),
           "counterparty_country must be an explicit 2-letter code")

    for col in NUMERIC_COLUMNS:
        text = _strings(out[col])
        present = text.ne("")
        numeric = pd.to_numeric(text, errors="coerce").astype("float64")
        reject(present & ~np.isfinite(numeric), f"{col} must be a finite number")
        if col == "amount":
            exact = text.map(lambda value: Decimal(value) if _MONEY.fullmatch(value) else None)
            reject(exact.map(lambda value: value is None or value <= 0 or value >= 10**16),
                   "amount must be positive, below 1e16, with at most 2 decimal places")
        elif col in {"login_attempts", "transaction_duration", "customer_age"}:
            reject(present & (numeric < 0), f"{col} must be non-negative")
            if col in {"login_attempts", "customer_age"}:
                reject(present & numeric.notna() & numeric.mod(1).ne(0), f"{col} must be an integer")
            if col == "login_attempts":
                reject(present & (numeric > 2**31 - 1), "login_attempts exceeds the integer storage range")
        # Monetary observations keep their exact source cents through the
        # canonical CSV and NUMERIC(18,2) ingestion. Features explicitly cast
        # them to float only at the numerical-model boundary.
        out[col] = exact if col == "amount" else numeric

    dates = _strings(out.transaction_date)
    if spec.get("date_format") == "month_first":
        shape = dates.str.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?")
        # pandas' mixed parser otherwise falls back to day-first for 13/04,
        # silently violating this profile's explicit US month/day contract.
        parts = dates.str.extract(r"^(\d{1,2})/(\d{1,2})/").apply(pd.to_numeric, errors="coerce")
        shape &= parts[0].between(1, 12) & parts[1].between(1, 31)
    else:
        shape = dates.str.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?")
    parsed = pd.to_datetime(dates.where(shape), format="mixed", dayfirst=False, errors="coerce", utc=True)
    reject(parsed.isna(), "transaction_date is invalid for the schema profile")
    inferred_precision = pd.Series(np.where(dates.str.contains(r"\d{1,2}:\d{2}:\d{2}", regex=True), "second",
                                            np.where(dates.str.contains(r"\d{1,2}:\d{2}", regex=True), "minute", "day")),
                                   index=raw.index, dtype="string")
    precision = inferred_precision.copy()
    if "timestamp_precision" in mapping:
        explicit = _strings(out.timestamp_precision).str.lower()
        precision = explicit.mask(explicit.eq(""), precision)
        rank = {"day": 0, "minute": 1, "second": 2}
        reject(precision.map(rank) > inferred_precision.map(rank),
               "timestamp_precision cannot invent finer precision than the supplied date")
    reject(~precision.isin(["day", "minute", "second"]), "timestamp_precision must be day, minute or second")
    # No fabricated intraday information on a day-only observation.
    parsed = parsed.where(precision.ne("day"), parsed.dt.floor("D"))
    parsed = parsed.where(precision.ne("minute"), parsed.dt.floor("min"))
    parsed = parsed.where(precision.ne("second"), parsed.dt.floor("s"))
    known = dates.str.contains(_OFFSET, regex=True)
    if "source_timezone_known" in mapping:
        explicit = _strings(out.source_timezone_known).str.lower()
        reject(~explicit.isin(["", "true", "false", "1", "0"]), "source_timezone_known must be boolean")
        known = explicit.map({"true": True, "1": True, "false": False, "0": False}).fillna(known).astype(bool)
    out["transaction_date"] = parsed
    out["timestamp_precision"] = precision
    out["source_timezone_known"] = known.astype(bool)

    for i, value in enumerate(out.ip_address):
        if value:
            try:
                ipaddress.ip_address(value)
            except ValueError:
                reasons[i].append("ip_address must be a syntactically valid IP address; no country is inferred")

    originals = []
    raw_records = raw.to_dict("records")
    for record in raw_records:
        prior = record.get("source_original")
        if isinstance(prior, str) and prior.strip().startswith("{"):
            try:
                prior = json.loads(prior)
            except ValueError:
                prior = None
        originals.append(_json_value(prior) if isinstance(prior, dict)
                         else {str(k): _json_value(v) for k, v in record.items() if k not in PROVENANCE_COLUMNS})
    out["source_original"] = originals
    out["source_ordinal"] = np.arange(1, len(raw) + 1)
    if "source_ordinal" in raw:
        ordinal_text = _strings(raw.source_ordinal)
        ordinals = pd.Series([int(value) if re.fullmatch(r"[1-9][0-9]*", value) else None
                              for value in ordinal_text], index=raw.index, dtype=object)
        reject(ordinals.map(lambda value: value is None or value >= 2**63) | ordinals.duplicated(),
               "source_ordinal must be a unique positive integer")
        # Quarantine keeps even invalid/oversized provenance verbatim, rather
        # than rounding an integer through a floating-point intermediate.
        out["source_ordinal"] = raw.source_ordinal
    # First fully valid row wins, including validation of supplied provenance.
    # A bad ordinal on an earlier row cannot suppress a later valid reference.
    valid_before_duplicates = np.array([not r for r in reasons], dtype=bool)
    duplicates = out.loc[valid_before_duplicates, "transaction_ref"].duplicated(keep="first")
    reject(out.index.isin(duplicates.index[duplicates]), "duplicate transaction_ref within source")
    out["schema_profile"] = spec["version"]
    valid = np.array([not r for r in reasons], dtype=bool)
    accepted = out.loc[valid].copy().reset_index(drop=True)
    accepted["source_ordinal"] = accepted.source_ordinal.astype("int64")
    for col in TEXT_COLUMNS:
        accepted[col] = accepted[col].astype(object).where(accepted[col].ne(""), None)
    quarantine = pd.DataFrame({"source_ordinal": out.loc[~valid, "source_ordinal"].tolist(),
                               "source_original": out.loc[~valid, "source_original"].tolist(),
                               "error_reason": ["; ".join(reasons[i]) for i in np.flatnonzero(~valid)]})
    used = set(mapping.values()) | set(PROVENANCE_COLUMNS)
    report = {
        "schema_profile": spec["version"], "profile_sha256": hashlib.sha256(
            json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "total_rows": len(raw), "accepted": len(accepted), "quarantined": len(quarantine),
        "column_mapping": mapping, "excluded_columns": [str(c) for c in raw.columns if c not in used],
        "currency_provenance": spec.get("currency_provenance"),
        "currency_counts": accepted.currency.value_counts(dropna=False).to_dict(),
        "timestamp_precision_counts": accepted.timestamp_precision.value_counts().to_dict(),
        "source_timezone_unknown": int((~accepted.source_timezone_known).sum()),
        "storage_timezone": "UTC", "timezone_policy": "Unknown source wall-clock values use UTC only as a storage carrier.",
        "labels_used": False,
    }
    return NormalizationResult(accepted, report, quarantine)


def normalize_csv(path: str | Path, profile: str | Path | dict = "kaggle_bank_v1") -> NormalizationResult:
    path = Path(path)
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, [])
            # pandas can otherwise infer an index and silently shift fields
            # when a data row has one more value than its header.
            for row_number, values in enumerate(reader, start=2):
                if len(values) != len(header):
                    raise NormalizationError(f"CSV row {row_number} has {len(values)} fields; expected {len(header)}")
        if not header:
            raise NormalizationError("CSV is empty or has no header")
        if len({_name(c) for c in header}) != len(header):
            raise NormalizationError("Duplicate CSV headers are ambiguous")
        frame = pd.read_csv(path, dtype="string", keep_default_na=False, encoding="utf-8-sig", skip_blank_lines=False)
    except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeError, csv.Error) as exc:
        raise NormalizationError(f"Unreadable CSV: {exc}") from exc
    result = normalize_frame(frame, profile)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    result.report.update({"source_path": str(path), "source_sha256": digest.hexdigest()})
    return result
