"""Feature groups the lab user can switch on or off.

The features themselves are the existing point-in-time FRAUD observation set
(`behavioral_v1`, app/ml/behavioral/features.py): history comes only from rows
strictly before each transaction, and every reference value (medians, channel
frequencies, the small-amount cut) is fitted on the training period only.
The lab only lets the user choose which groups enter the model.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.ml.behavioral.features import FEATURE_DEFINITIONS, FEATURE_SET_VERSION, build_features, feature_names, fit_vocabulary

UNIT = "FRAUD"

FEATURE_GROUPS: tuple[dict, ...] = (
    {"key": "nominal", "label": "Nominal & arah", "default": True,
     "description": "Besar nominal, nominal dibanding median data latih, dan arah dana.",
     "features": ("AMOUNT", "LOG_AMOUNT", "AMOUNT_OVER_TRAIN_MEDIAN", "IS_DEBIT")},
    {"key": "riwayat", "label": "Riwayat rekening (7 & 30 hari)", "default": True,
     "description": "Jumlah dan nilai transaksi sebelumnya di akun yang sama, serta jeda sejak transaksi terakhir.",
     "features": ("HISTORY_DAYS", "PRIOR_HISTORY_COUNT", "NO_BASELINE", "PRIOR_COUNT_R7D", "PRIOR_VALUE_R7D",
                  "PRIOR_COUNT_R30D", "PRIOR_VALUE_R30D", "PRIOR_DEBIT_VALUE_R7D", "PRIOR_SMALL_COUNT_R7D",
                  "DAYS_SINCE_PREV_OBSERVATION")},
    {"key": "penyimpangan", "label": "Penyimpangan dari kebiasaan", "default": True,
     "description": "Nominal dibanding rata-rata 90 hari akun itu sendiri (butuh riwayat cukup).",
     "features": ("AMOUNT_OVER_PRIOR_MEAN", "AMOUNT_Z_PRIOR", "BALANCE_OVER_PRIOR_MEAN")},
    {"key": "kecepatan", "label": "Kecepatan (1 jam & 24 jam)", "default": True,
     "description": "Berapa banyak transaksi dan dana keluar dalam 1 jam dan 24 jam terakhir.",
     "features": ("COUNT_R1H", "COUNT_R24H", "DEBIT_VALUE_R24H")},
    {"key": "waktu", "label": "Jam transaksi", "default": True,
     "description": "Jam dalam sehari (hanya jika data menyertakan jam dan zona waktu).",
     "features": ("UTC_HOUR_SIN", "UTC_HOUR_COS", "DAY_ONLY_TIMESTAMP")},
    {"key": "kanal", "label": "Kanal", "default": True,
     "description": "Seberapa umum kanal transaksi di data latih.",
     "features": ("CHANNEL_TRAIN_FREQUENCY", "CHANNEL_UNSEEN")},
    {"key": "kebaruan", "label": "Perangkat, IP & merchant baru", "default": True,
     "description": "Apakah perangkat, IP, lokasi atau merchant belum pernah dipakai akun ini sebelumnya.",
     "features": ("DEVICE_PRESENT", "IP_PRESENT", "LOCATION_PRESENT", "DEVICE_FIRST_USE", "IP_FIRST_USE",
                  "LOCATION_FIRST_USE", "MERCHANT_FIRST_USE")},
    {"key": "sesi", "label": "Data sesi dari sumber", "default": True,
     "description": "Percobaan login, durasi, saldo tercatat (hanya jika dataset menyediakannya).",
     "features": ("LOGIN_ATTEMPTS", "TRANSACTION_DURATION", "ACCOUNT_BALANCE")},
)
GROUP_KEYS = tuple(g["key"] for g in FEATURE_GROUPS)

# Every feature of the unit belongs to exactly one group (checked by a test).
assert sorted(f for g in FEATURE_GROUPS for f in g["features"]) == sorted(feature_names(UNIT))


def definition(code: str) -> str:
    return FEATURE_DEFINITIONS.get((UNIT, code), "")


def selected_features(groups: list[str]) -> list[str]:
    chosen = set(groups)
    unknown = chosen - set(GROUP_KEYS)
    if unknown:
        raise ValueError(f"Unknown feature group(s): {', '.join(sorted(unknown))}")
    return [f for g in FEATURE_GROUPS if g["key"] in chosen for f in g["features"]]


def build_matrix(frame: pd.DataFrame, train_mask: np.ndarray) -> tuple[pd.DataFrame, dict]:
    """All FRAUD features for every row of `frame`, in `frame`'s order, with the
    vocabulary fitted on the training rows only."""
    vocabulary = fit_vocabulary(frame.loc[train_mask])
    features = build_features(frame, UNIT, vocabulary)
    order = pd.Series(np.arange(len(frame)), index=frame.transaction_ref.to_numpy())
    features = features.set_index(order.loc[features.transaction_ref].to_numpy()).sort_index()
    return features[feature_names(UNIT)], vocabulary


__all__ = ["FEATURE_GROUPS", "FEATURE_SET_VERSION", "GROUP_KEYS", "UNIT", "build_matrix", "definition",
           "selected_features"]
