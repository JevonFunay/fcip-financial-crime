"""Column mapping for a labelled dataset: suggestions, then validation through the canonical normalizer.

The user decides which source column feeds which canonical field, which column
is the label and which of its values count as positive. Validation is the same
code the offline pipeline and the live upload use (`normalize_frame`), so a
row that would be quarantined at upload is quarantined here too, with the same
reason. Fields a dataset does not have are filled with an explicit, recorded
neutral value (never a guessed fact), and the notes say so.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.ml.behavioral.standardize import NormalizationError, normalize_frame
from app.services.tabular import FORMAT_PARQUET, TableFileError, detect_format, parquet_to_frame

# Canonical fields the lab can map, in display order. "required" fields must be
# mapped; the rest have a documented fallback or simply stay empty.
FIELDS: tuple[dict, ...] = (
    {"key": "transaction_ref", "label": "ID transaksi", "required": False,
     "help": "Unik per baris. Jika tidak ada, nomor baris dipakai sebagai ID.",
     "aliases": ("transactionref", "transactionid", "txnid", "txnref", "sourcetransactionreference", "transid",
                 "referenceid", "trxid")},
    {"key": "account_number", "label": "Rekening / ID akun", "required": True,
     "help": "Akun pemilik transaksi; riwayat dihitung per akun.",
     "aliases": ("accountnumber", "accountid", "account", "sourceaccountid", "nameorig", "accountno", "customerid",
                 "cardid", "walletid")},
    {"key": "transaction_date", "label": "Tanggal & waktu", "required": True,
     "help": "Dipakai untuk urutan waktu dan pembagian data latih/validasi/uji.",
     "aliases": ("transactiondate", "timestamp", "datetime", "valuedatetime", "txndate", "transactiontime",
                 "transactiondatetime", "date", "time", "createdat")},
    {"key": "amount", "label": "Nominal", "required": True, "help": "Nilai transaksi (positif).",
     "aliases": ("amount", "transactionamount", "amountoriginal", "amt", "value", "nominal")},
    {"key": "direction", "label": "Arah (masuk/keluar)", "required": False,
     "help": "IN/OUT, CREDIT/DEBIT. Jika tidak ada, semua baris dianggap keluar (DEBIT) dan dicatat.",
     "aliases": ("direction", "debitcredit", "drcr", "flow", "arah")},
    {"key": "channel", "label": "Kanal", "required": False,
     "help": "Jika tidak ada, diisi UNKNOWN dan dicatat.", "aliases": ("channel", "paymentchannel", "kanal")},
    {"key": "currency", "label": "Mata uang", "required": False,
     "help": "Kode 3 huruf. Jika tidak ada, mata uang default di bawah dipakai.",
     "aliases": ("currency", "currencyoriginal", "ccy", "currencycode")},
    {"key": "transaction_type", "label": "Jenis transaksi", "required": False, "help": "",
     "aliases": ("transactiontype", "type", "txntype")},
    {"key": "device_ref", "label": "ID perangkat", "required": False, "help": "",
     "aliases": ("deviceid", "device", "sourcedeviceid", "deviceref")},
    {"key": "merchant_ref", "label": "ID merchant", "required": False, "help": "",
     "aliases": ("merchantid", "merchant", "sourcemerchantid", "merchantref")},
    {"key": "ip_address", "label": "Alamat IP", "required": False, "help": "", "aliases": ("ipaddress", "ip")},
    {"key": "location", "label": "Lokasi", "required": False, "help": "", "aliases": ("location", "city", "lokasi")},
    {"key": "counterparty_country", "label": "Negara lawan transaksi", "required": False,
     "help": "Kode negara 2 huruf.", "aliases": ("counterpartycountry", "country")},
    {"key": "source_status", "label": "Status dari sumber", "required": False, "help": "",
     "aliases": ("status", "statusfromsource", "sourcestatus")},
    {"key": "login_attempts", "label": "Jumlah percobaan login", "required": False, "help": "",
     "aliases": ("loginattempts",)},
    {"key": "transaction_duration", "label": "Durasi transaksi", "required": False, "help": "",
     "aliases": ("transactionduration", "duration")},
    {"key": "account_balance", "label": "Saldo tercatat", "required": False, "help": "",
     "aliases": ("accountbalance", "balance")},
)
FIELD_KEYS = tuple(f["key"] for f in FIELDS)
REQUIRED_FIELDS = tuple(f["key"] for f in FIELDS if f["required"])
LABEL_ALIASES = ("isfraud", "fraud", "label", "target", "class", "isfraudulent", "fraudflag", "issuspicious",
                 "suspicious", "issar", "sar", "islaundering", "laundering", "y")
CATEGORY_ALIASES = ("patterncode", "pattern", "category", "typology", "scenario", "fraudtype", "kategori")
POSITIVE_DEFAULTS = {"1", "true", "yes", "y", "t", "fraud", "ya"}
LOW_CARDINALITY = 20
MIN_PER_CLASS = 30
# Upper end of the capstone brief's synthetic dataset (300k–500k transactions).
# Training runs inside the request until a background job runner exists.
MAX_ROWS = 500_000
DIRECTION_SYNONYMS = {"DR": "DEBIT", "D": "DEBIT", "WITHDRAWAL": "DEBIT", "OUTGOING": "DEBIT", "KELUAR": "DEBIT",
                      "CR": "CREDIT", "C": "CREDIT", "DEPOSIT": "CREDIT", "INCOMING": "CREDIT", "MASUK": "CREDIT"}


class LabDataError(ValueError):
    """The file or the mapping cannot be used; the message is shown to the user."""


def _name(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lstrip("﻿").strip().lower())


def read_csv_bytes(raw_bytes: bytes) -> pd.DataFrame:
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise LabDataError("File harus berupa CSV dengan encoding UTF-8, atau file Parquet.") from exc
    if "\x00" in text:
        raise LabDataError("File berisi byte NUL dan bukan CSV yang valid.")
    try:
        frame = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False, skipinitialspace=False)
    except (pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise LabDataError(f"CSV tidak dapat dibaca: {exc}") from exc
    return _checked(frame)


def read_table_bytes(raw_bytes: bytes) -> tuple[pd.DataFrame, str]:
    """A CSV or Parquet upload as text cells, and which format it was (recognised from content)."""
    fmt = detect_format(raw_bytes)
    if fmt == FORMAT_PARQUET:
        try:
            return _checked(parquet_to_frame(raw_bytes)), fmt
        except TableFileError as exc:
            raise LabDataError(str(exc)) from exc
    return read_csv_bytes(raw_bytes), fmt


def _checked(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or not len(frame.columns):
        raise LabDataError("File kosong atau tidak memiliki baris data.")
    if len(frame) > MAX_ROWS:
        raise LabDataError(f"File berisi {len(frame):,} baris; batas lab saat ini {MAX_ROWS:,} baris per file "
                           "(pelatihan berjalan langsung, belum sebagai job di latar belakang).")
    names = [_name(c) for c in frame.columns]
    if frame.columns.duplicated().any() or len(names) != len(set(names)):
        raise LabDataError("Nama kolom ganda membuat pemetaan ambigu.")
    return frame


def _sniff_direction(values: pd.Series) -> bool:
    sample = values[values.ne("")].head(500).str.upper()
    known = {"IN", "OUT", "CREDIT", "DEBIT", *DIRECTION_SYNONYMS}
    return len(sample) > 0 and sample.isin(known).mean() > 0.95


def _sniff_date_format(values: pd.Series) -> str:
    sample = values[values.ne("")].head(200)
    if len(sample) and sample.str.match(r"^\d{1,2}/\d{1,2}/\d{4}").mean() > 0.8:
        return "month_first"
    return "iso"


def suggest(frame: pd.DataFrame) -> dict:
    """Best-effort suggestions; the user confirms or changes every one of them."""
    by_name = {_name(c): c for c in frame.columns}
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for spec in FIELDS:
        for alias in (spec["key"].replace("_", ""), *spec["aliases"]):
            column = by_name.get(alias)
            if column and column not in used:
                if spec["key"] == "transaction_type" and _sniff_direction(frame[column]):
                    continue  # e.g. a "TransactionType" column holding Debit/Credit is a direction
                mapping[spec["key"]] = column
                used.add(column)
                break
    if "direction" not in mapping:
        for column in frame.columns:
            if column not in used and _sniff_direction(frame[column]):
                mapping["direction"] = column
                used.add(column)
                break
    label = next((by_name[a] for a in LABEL_ALIASES if a in by_name and by_name[a] not in used), None)
    category = next((by_name[a] for a in CATEGORY_ALIASES if a in by_name and by_name[a] not in used
                     and by_name[a] != label), None)
    low_cardinality: dict[str, list[str]] = {}
    for column in frame.columns:
        distinct = frame[column].unique()
        if len(distinct) <= LOW_CARDINALITY:
            low_cardinality[column] = sorted(str(v) for v in distinct)
    positives = [v for v in low_cardinality.get(label or "", []) if v.strip().lower() in POSITIVE_DEFAULTS]
    return {
        "fields": mapping,
        "label_column": label,
        "positive_values": positives,
        "category_column": category,
        "date_format": _sniff_date_format(frame[mapping["transaction_date"]]) if "transaction_date" in mapping else "iso",
        "low_cardinality": low_cardinality,
    }


@dataclass
class MappedData:
    frame: pd.DataFrame          # accepted canonical observations, single currency, labelled rows only
    labels: np.ndarray           # 1.0 / 0.0, aligned with frame
    categories: pd.Series | None  # optional reporting column, aligned with frame; never a feature
    report: dict = field(default_factory=dict)


def _field_settings(raw: pd.DataFrame, mapping: dict) -> tuple[dict[str, str], str, str]:
    """The transaction-field part of a mapping, checked against this file."""
    fields: dict[str, str] = {k: v for k, v in (mapping.get("fields") or {}).items() if v}
    unknown = set(fields) - set(FIELD_KEYS)
    if unknown:
        raise LabDataError(f"Field tidak dikenal: {', '.join(sorted(unknown))}")
    missing_columns = sorted({c for c in fields.values() if c not in raw.columns})
    if missing_columns:
        raise LabDataError(f"Kolom tidak ada di file: {', '.join(missing_columns)}")
    missing_fields = [f["label"] for f in FIELDS if f["required"] and f["key"] not in fields]
    if missing_fields:
        raise LabDataError(f"Field wajib belum dipetakan: {', '.join(missing_fields)}")
    used = list(fields.values())
    if len(used) != len(set(used)):
        raise LabDataError("Satu kolom sumber tidak boleh dipetakan ke dua field.")
    date_format = mapping.get("date_format") or "iso"
    if date_format not in {"iso", "month_first"}:
        raise LabDataError("Format tanggal harus iso atau month_first.")
    default_currency = (mapping.get("default_currency") or "XXX").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", default_currency):
        raise LabDataError("Mata uang default harus kode 3 huruf, misalnya IDR atau USD.")
    return fields, date_format, default_currency


def _normalize(raw: pd.DataFrame, fields: dict[str, str], date_format: str,
               default_currency: str) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """Mapped columns through the canonical normalizer: (accepted, quarantine, notes)."""
    notes: list[str] = []
    work = pd.DataFrame(index=raw.index)
    for key, column in fields.items():
        work[key] = raw[column].astype(str)
    if "transaction_ref" not in work:
        work["transaction_ref"] = [f"ROW-{i + 1:07d}" for i in range(len(raw))]
        notes.append("ID transaksi tidak dipetakan: nomor baris dipakai sebagai ID.")
    if "direction" not in work:
        work["direction"] = "DEBIT"
        notes.append("Arah transaksi tidak dipetakan: semua baris diperlakukan sebagai DEBIT (fitur arah menjadi konstan).")
    else:
        upper = work.direction.str.strip().str.upper()
        work["direction"] = upper.map(DIRECTION_SYNONYMS).fillna(upper)
    if "channel" not in work:
        work["channel"] = "UNKNOWN"
        notes.append("Kanal tidak dipetakan: diisi UNKNOWN (fitur kanal menjadi konstan).")
    if "currency" not in work:
        notes.append(f"Mata uang tidak dipetakan: semua nominal dianggap {default_currency}, tanpa konversi kurs.")

    profile = {
        "version": "lab_mapping_v1",
        "default_currency": default_currency,
        "source_timezone": None,
        "currency_provenance": "Mapped by the lab user; absent currency uses the stated default, no FX.",
        "date_format": date_format,
        "columns": {key: {"aliases": [], "required": key in {"transaction_ref", "account_number", "transaction_date",
                                                              "amount", "direction", "channel"}}
                    for key in work.columns},
    }
    try:
        result = normalize_frame(work.reset_index(drop=True), profile)
    except NormalizationError as exc:
        raise LabDataError(f"Validasi gagal: {exc}") from exc
    return result.frame, result.quarantine, notes


def _reasons(quarantine: pd.DataFrame) -> list[dict]:
    if quarantine.empty:
        return []
    counts = quarantine.error_reason.str.split("; ").explode().value_counts()
    return [{"reason": str(r), "rows": int(n)} for r, n in counts.head(8).items()]


def apply_mapping(raw: pd.DataFrame, mapping: dict) -> MappedData:
    """Validate a training mapping against the file. Raises LabDataError for anything the user must fix."""
    fields, date_format, default_currency = _field_settings(raw, mapping)
    label_column = mapping.get("label_column")
    category_column = mapping.get("category_column") or None
    missing_columns = [c for c in (label_column, category_column) if c and c not in raw.columns]
    if missing_columns:
        raise LabDataError(f"Kolom tidak ada di file: {', '.join(missing_columns)}")
    if not label_column:
        raise LabDataError("Pilih kolom label (kunci jawaban) terlebih dahulu.")
    used = list(fields.values())
    if label_column in used or (category_column and category_column in used):
        raise LabDataError("Kolom label dan kolom kategori tidak boleh dipakai sebagai field transaksi (mencegah kebocoran).")
    positive_values = {str(v).strip().lower() for v in mapping.get("positive_values") or [] if str(v).strip()}
    if not positive_values:
        raise LabDataError("Pilih minimal satu nilai label yang dihitung sebagai positif.")

    accepted, quarantine, notes = _normalize(raw, fields, date_format, default_currency)
    positions = accepted.source_ordinal.to_numpy(dtype=np.int64) - 1
    label_raw = raw[label_column].astype(str).str.strip().to_numpy()[positions]
    label_present = label_raw != ""
    labels = np.where(np.isin(np.char.lower(label_raw.astype(str)), list(positive_values)), 1.0, 0.0)
    missing_label = int((~label_present).sum())
    keep = label_present.copy()

    currency_counts = accepted.currency.value_counts()
    currency = str(currency_counts.index[0]) if len(currency_counts) else default_currency
    other_currency = accepted.currency.ne(currency).to_numpy()
    excluded_currency = int((other_currency & keep).sum())
    keep &= ~other_currency
    if excluded_currency:
        notes.append(f"{excluded_currency} baris bermata uang selain {currency} dikeluarkan: satu model hanya untuk satu mata uang (tanpa konversi kurs).")
    if missing_label:
        notes.append(f"{missing_label} baris tanpa nilai label dikeluarkan dari pelatihan dan evaluasi.")

    frame = accepted.loc[keep].reset_index(drop=True)
    y = labels[keep]
    categories = None
    if category_column:
        categories = pd.Series(raw[category_column].astype(str).str.strip().to_numpy()[positions][keep])
    positives = int(y.sum())
    negatives = int(len(y) - positives)
    report = {
        "total_rows": int(len(raw)),
        "accepted": int(len(accepted)),
        "quarantined": int(len(quarantine)),
        "quarantine_reasons": _reasons(quarantine),
        "usable_rows": int(len(frame)),
        "missing_label": missing_label,
        "excluded_other_currency": excluded_currency,
        "positives": positives,
        "negatives": negatives,
        "positive_rate": float(positives / len(y)) if len(y) else 0.0,
        "currency": currency,
        "date_from": frame.transaction_date.min().isoformat() if len(frame) else None,
        "date_to": frame.transaction_date.max().isoformat() if len(frame) else None,
        "accounts": int(frame.account_number.nunique()) if len(frame) else 0,
        "timestamp_precision": {str(k): int(v) for k, v in frame.timestamp_precision.value_counts().items()},
        "category_counts": ({str(k): int(v) for k, v in categories[y == 1].replace("", "(kosong)").value_counts().head(20).items()}
                            if categories is not None else None),
        "notes": notes,
        "ready": positives >= MIN_PER_CLASS and negatives >= MIN_PER_CLASS,
    }
    if not report["ready"]:
        report["notes"].append(f"Perlu minimal {MIN_PER_CLASS} baris positif dan {MIN_PER_CLASS} negatif untuk melatih model.")
    return MappedData(frame=frame, labels=y, categories=categories, report=report)


@dataclass
class MonitoringData:
    frame: pd.DataFrame   # accepted canonical observations, as ingestion's canonical CSV expects
    report: dict


def map_for_monitoring(raw: pd.DataFrame, mapping: dict) -> MonitoringData:
    """A new file in a training dataset's layout, mapped for monitoring.

    Only the transaction fields are used. A label or category column in the
    file is ignored: live data is never scored with its answers in sight.
    Rows that fail validation are reported here and quarantined again, with
    the same reasons, when ingestion stores the batch.
    """
    fields, date_format, default_currency = _field_settings(raw, mapping)
    accepted, quarantine, notes = _normalize(raw, fields, date_format, default_currency)
    ignored = [c for c in (mapping.get("label_column"), mapping.get("category_column")) if c and c in raw.columns]
    if ignored:
        notes.append(f"Kolom {', '.join(ignored)} diabaikan: data yang dipantau tidak memakai kunci jawaban.")
    return MonitoringData(frame=accepted, report={
        "total_rows": int(len(raw)),
        "accepted": int(len(accepted)),
        "quarantined": int(len(quarantine)),
        "quarantine_reasons": _reasons(quarantine),
        "currencies": {str(k): int(v) for k, v in accepted.currency.value_counts().items()},
        "accounts": int(accepted.account_number.nunique()) if len(accepted) else 0,
        "notes": notes,
    })
