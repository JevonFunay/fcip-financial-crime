"""Audit-motivated relative-behaviour view; no labels or feature fitting.

The prior VALIDATION error audit found missed low-ticket velocity and AML
reactivation, while unconditional new-context/login observations generated many
false flags. This fixed candidate removes unconditional amount, balance,
context/login and duration inputs. It retains causal own-history comparisons
and adds an AML gap normalized by that same prior observed calendar cadence.
This is an experiment, not a claim that removed inputs have no real value.
"""

from __future__ import annotations

import pandas as pd

from . import features as original
from .research_features import _references, feature_definitions as portable_definitions

VERSION = "relative_behavior_research_v1"
META = ["account_number", "transaction_ref", "as_of"]
GAP = "GAP_W_OVER_LAST30_PRIOR_MEAN_GAP"
_NAMES = {
    "AML": ["COUNT_W_OVER_LAST30_PRIOR_RATE", "VALUE_W_OVER_LAST30_PRIOR_RATE",
            "TICKET_W_OVER_LAST30_PRIOR_MEAN", "DEBIT_SHARE_W", "SMALL_AMOUNT_SHARE_W",
            "SMALL_PAYMENT_RELATIVE_ACCELERATION_W", "BALANCE_W_OVER_LAST30_PRIOR_MEAN", GAP],
    "FRAUD": ["IS_DEBIT", "AMOUNT_OVER_LAST30_PRIOR_MEAN", "AMOUNT_Z_LAST30_PRIOR",
              "BALANCE_OVER_LAST30_PRIOR_MEAN", "GAP_OVER_LAST30_PRIOR_MEAN_GAP",
              "COUNT_R7D_OVER_LAST30_PRIOR_RATE", "VALUE_R7D_OVER_LAST30_PRIOR_RATE",
              "SMALL_PAYMENT_RELATIVE_ACCELERATION"],
}


def feature_names(unit: str) -> list[str]:
    return list(_NAMES[str(unit).upper()])


def feature_definitions(unit: str) -> dict[str, str]:
    definitions = portable_definitions(unit, "portable")
    definitions[GAP] = (
        "Calendar gap from the latest strictly prior availability bound to this account-week's start, "
        "multiplied by the prior minimum-30 observation rate (n-1 over actual day span), including "
        "all oldest-boundary ties. Requires 30 prior observations over at least 30 days. "
        "Date-only observations become available at day end; this does not recover physical inactivity.")
    return {name: definitions[name] for name in feature_names(unit)}


def build_relative(observations: pd.DataFrame, unit: str, vocabulary: dict, portable: pd.DataFrame) -> pd.DataFrame:
    unit = str(unit).upper()
    names = feature_names(unit)
    selected = [name for name in names if name != GAP]
    result = portable[[*META, *selected]].copy()
    if unit == "AML":
        frame = original._prepared(observations)
        query = portable[["account_number"]].copy()
        query["_cut"] = (portable.as_of - pd.Timedelta(days=7) + pd.Timedelta(seconds=1)).astype("int64")
        reference = _references(frame, query, vocabulary)
        result[GAP] = reference.gap * reference.rate
    return result[[*META, *names]]
