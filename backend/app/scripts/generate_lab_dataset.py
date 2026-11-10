"""Generate a separate, versioned coverage study for the model lab.

The legacy enterprise generator 1.4.0 and its datasets are not changed. This
new study uses the same single-file upload layout as make_lab_sample. Its
labels are declared simulated interventions, not adjudicated financial crime.
Legitimate lookalikes overlap the interventions, including exact observable
counterfactuals; perfect intention discrimination is not promised.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from app.ml.lab.mapping import apply_mapping, read_csv_bytes, suggest
from app.ml.lab.trainer import chronological_split
from app.scripts.make_lab_sample import OUTPUT_COLUMNS

VERSION = "lab_behavioral_coverage_v1"
PATTERNS = ("ATO", "P04", "P05")
CHANNELS = ("TRANSFER", "QRIS", "PAYMENT", "AGENT")
DEFAULT_ROWS = 250_000
DEFAULT_DAYS = 720
DEFAULT_START = date(2024, 1, 1)
MIN_ROWS = 20_000
MAX_ROWS = 500_000
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
TRAIN_SHARE = 0.6
HISTORY_ROW_RANGE = (32, 64)
EXACT_CONTROL_TARGET_SHARE = 0.25
JAKARTA = timezone(timedelta(hours=7))
UPLOAD_COLUMNS = (*OUTPUT_COLUMNS, "is_fraud", "pattern_code")
LINEAGE_COLUMNS = ("transaction_ref", "scenario_id", "pattern", "role", "magnitude",
                   "case_label", "pair_id", "exact_counterfactual")


class CoverageError(ValueError):
    """The fixed generated study does not have the requested coverage."""


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _json(path: Path, value) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def _mapping(raw: pd.DataFrame) -> dict:
    proposed = suggest(raw)
    result = {key: proposed[key] for key in
              ("fields", "label_column", "positive_values", "category_column", "date_format")}
    result["default_currency"] = "XXX"
    if result["label_column"] != "is_fraud" or result["positive_values"] != ["1"]:
        raise CoverageError("The existing lab does not recognize the unchanged label layout")
    return result


def validate_coverage(raw: pd.DataFrame, lineage: pd.DataFrame) -> dict:
    """Audit the existing normalizer and chronological partition without repair.

    No label changes, row shuffling, label-informed boundary selection or retry
    occurs here. Coverage absence rejects the draw. The fixed episode calendar
    is a declared study design, not a discovered fraud prevalence.
    """
    mapping = _mapping(raw)
    mapped = apply_mapping(raw, mapping)
    if mapped.report["quarantined"] or mapped.report["missing_label"]:
        raise CoverageError("The new clean study must not lose generated rows during normalization")
    if mapped.report["usable_rows"] != len(raw) or mapped.report["currency"] != "IDR":
        raise CoverageError("Unexpected row or currency loss in the lab contract")
    parts = chronological_split(mapped.frame, TRAIN_SHARE)
    index = lineage.set_index("transaction_ref")
    aligned = index.loc[mapped.frame.transaction_ref].reset_index(drop=True)
    cutoff = float(mapped.frame.loc[parts == "train", "amount"].astype(float).quantile(.25))
    amounts = mapped.frame.amount.to_numpy(dtype=float)
    splits = {}
    for name in ("train", "validation", "test"):
        mask = parts == name
        pattern_summary = {}
        for pattern in PATTERNS:
            own = aligned.pattern.eq(pattern).to_numpy() & aligned.role.eq("event").to_numpy()
            positive = mask & own & (mapped.labels == 1)
            benign = mask & own & (mapped.labels == 0)
            small = amounts <= cutoff
            item = {"positive_rows": int(positive.sum()), "benign_lookalike_rows": int(benign.sum()),
                    "small_positive_rows": int((positive & small).sum()),
                    "small_benign_rows": int((benign & small).sum())}
            if any(value == 0 for value in item.values()):
                raise CoverageError(f"{name} lacks required {pattern} positive/benign/low-value coverage: {item}")
            pattern_summary[pattern] = item
        dates = mapped.frame.loc[mask, "transaction_date"]
        splits[name] = {"rows": int(mask.sum()), "date_from": dates.min().isoformat(),
                        "date_to": dates.max().isoformat(), "patterns": pattern_summary}
    if len(raw) >= 200_000 and splits["train"]["rows"] < 100_000:
        raise CoverageError("The large source must supply at least 100,000 actual TRAIN rows")
    scenario_rows = aligned.loc[aligned.scenario_id.ne("")].copy()
    scenario_rows["partition"] = parts[aligned.scenario_id.ne("").to_numpy()]
    whole = scenario_rows.groupby("scenario_id").partition.nunique()
    event = scenario_rows.loc[scenario_rows.role.eq("event")].groupby("scenario_id").partition.nunique()
    return {"normalization": mapped.report, "mapping": mapping,
            "split_policy": "unchanged lab chronological split, train_share=0.6; no label-informed partition",
            "small_amount_cutoff_train_q25_IDR": cutoff, "splits": splits,
            "coverage_metadata_only": "The q25 above describes this file's hypothetical TRAIN partition. Evaluating a new monitoring draw must use the earlier training source's frozen vocabulary/cutoff, never refit it from this metadata.",
            "scenario_partition_diagnostic": {
                "scenarios_with_history_or_event_in_multiple_partitions": int((whole > 1).sum()),
                "scenarios_with_event_rows_in_multiple_partitions": int((event > 1).sum()),
                "independent_scenario_holdout_claim": False,
                "interpretation": "Chronological future observations can share earlier account history; whole-timestamp splitting does not promise whole-scenario isolation.",
            },
            "coverage_assertions_passed": True}


class _Study:
    def __init__(self, rows: int, seed: int, start: date, days: int):
        if not MIN_ROWS <= rows <= MAX_ROWS:
            raise ValueError(f"rows must be between {MIN_ROWS} and {MAX_ROWS}")
        if days != DEFAULT_DAYS:
            raise ValueError("This version fixes a 720-day calendar; create a new version for a different design")
        self.rows, self.seed, self.days = rows, seed, days
        self.start = datetime.combine(start, datetime.min.time(), tzinfo=JAKARTA)
        self.rng = np.random.default_rng(seed)
        self.salt = f"{seed % 65536:04x}"
        self.records: list[dict] = []
        self.lineage: list[dict] = []
        self.scenarios: list[dict] = []
        self.account_tokens: set[int] = set()
        self.case_tokens: set[int] = set()
        self.pairs = 0
        self.exact_pairs = 0

    def account(self) -> tuple[str, str, str]:
        token = int(self.rng.integers(0, 2 ** 32))
        while token in self.account_tokens:
            token = int(self.rng.integers(0, 2 ** 32))
        self.account_tokens.add(token)
        return f"A{self.salt}{token:08x}", f"D{token:08x}", f"D{token ^ 0xa593124f:08x}"

    def case(self) -> str:
        token = int(self.rng.integers(0, 2 ** 32))
        while token in self.case_tokens:
            token = int(self.rng.integers(0, 2 ** 32))
        self.case_tokens.add(token)
        return f"S{self.salt}{token:08x}"

    def row(self, account: str, device: str, moment: datetime, amount: float,
            *, debit: bool, positive: bool = False, pattern: str = "", scenario: str = "",
            role: str = "background", magnitude: str = "", case_label: str = "background",
            pair: str = "", exact: bool = False, channel: str | None = None,
            merchant: str | None = None, ip: str | None = None) -> None:
        if not self.start <= moment < self.start + timedelta(days=self.days):
            raise CoverageError("Every generated time must stay inside its declared calendar")
        self.records.append({
            "source_transaction_reference": "", "source_account_id": account,
            "direction": "DEBIT" if debit else "CREDIT", "amount_original": f"{max(1, amount):.2f}",
            "currency_original": "IDR", "value_datetime": moment.isoformat(timespec="seconds"),
            "channel": channel or str(self.rng.choice(CHANNELS)),
            "transaction_type": "PAY" if debit else "IN", "counterparty_country": "ID",
            "source_merchant_id": merchant or f"M{int(self.rng.integers(1, 512)):03x}",
            "source_device_id": device, "ip_address": ip or f"192.0.2.{int(self.rng.integers(1, 255))}",
            "status_from_source": "POSTED", "is_fraud": int(positive),
            "pattern_code": pattern if positive else "",
        })
        self.lineage.append({"transaction_ref": "", "scenario_id": scenario, "pattern": pattern,
                             "role": role, "magnitude": magnitude, "case_label": case_label,
                             "pair_id": pair, "exact_counterfactual": int(exact)})

    def _episode(self, pattern: str, low: bool, day: int, exact: bool) -> None:
        pair = self.case()
        positive_case, control_case = self.case(), self.case()
        positive_account, positive_base, positive_new = self.account()
        control_account, control_base, control_new = self.account()
        hour = int(self.rng.integers(0, 24))
        event_start = self.start + timedelta(days=day, hours=hour)
        # Counts, spacing and spans vary per pair independently of its two labels.
        # P05 has a genuine observed >=100-day gap; other patterns have active history.
        history_count = int(self.rng.integers(HISTORY_ROW_RANGE[0], HISTORY_ROW_RANGE[1] + 1))
        oldest = float(self.rng.uniform(150, 175) if pattern == "P05" else self.rng.uniform(80, 140))
        latest = float(self.rng.uniform(100, 120) if pattern == "P05" else self.rng.uniform(1, 7))
        history_ages = np.array([oldest, *self.rng.uniform(latest, oldest, history_count - 2), latest])
        history_ages = np.sort(history_ages)[::-1]
        history = []
        scale = float(self.rng.lognormal(11.5, .7))
        for age in history_ages:
            moment = (event_start - timedelta(days=float(age), seconds=int(self.rng.integers(0, 3600)))).replace(microsecond=0)
            history.append((moment, float(self.rng.lognormal(np.log(scale), .8)), bool(self.rng.random() < .7),
                            str(self.rng.choice(CHANNELS)),
                            f"M{int(self.rng.integers(1, 512)):03x}", f"192.0.2.{int(self.rng.integers(1, 255))}"))
        for account, base, scenario, case_label in (
            (positive_account, positive_base, positive_case, "simulated_intervention"),
            (control_account, control_base, control_case, "benign_lookalike"),
        ):
            for moment, amount, debit, channel, merchant, ip in history:
                self.row(account, base, moment, amount, debit=debit, pattern=pattern, scenario=scenario,
                         role="history", magnitude="low" if low else "other", case_label=case_label,
                         pair=pair, exact=exact, channel=channel, merchant=merchant, ip=ip)

        count = int(self.rng.integers(16, 25))
        negative_count = count if exact else int(self.rng.integers(1, 5 if pattern == "P05" else 15))
        new_device = pattern == "ATO" and self.rng.random() < .85
        template = []
        for index in range(count):
            moment = event_start + timedelta(seconds=index * 90)
            amount = float(self.rng.uniform(1000, 25000) if low else self.rng.lognormal(12.7, .8))
            template.append((moment, amount, bool(self.rng.random() < .95),
                             str(self.rng.choice(CHANNELS)),
                             f"M{int(self.rng.integers(1, 512)):03x}", f"192.0.2.{int(self.rng.integers(1, 255))}"))
        for index, (moment, amount, debit, channel, merchant, ip) in enumerate(template):
            self.row(positive_account, positive_new if new_device else positive_base, moment, amount,
                     debit=debit, positive=True, pattern=pattern, scenario=positive_case, role="event",
                     magnitude="low" if low else "other", case_label="simulated_intervention",
                     pair=pair, exact=exact, channel=channel, merchant=merchant, ip=ip)
            if index < negative_count:
                # Exact controls preserve observable features modulo neutral identity substitution.
                # Other controls have ordinary intensity, while amounts/time/channel supports overlap.
                self.row(control_account, control_new if new_device else control_base, moment, amount,
                         debit=debit, pattern=pattern, scenario=control_case, role="event",
                         magnitude="low" if low else "other", case_label="benign_lookalike",
                         pair=pair, exact=exact, channel=channel, merchant=merchant, ip=ip)
        self.scenarios.extend([
            {"scenario_id": scenario, "pair_id": pair, "pattern": pattern, "case_label": label,
             "magnitude": "low" if low else "other", "event_start": event_start.isoformat(),
             "event_rows": rows, "history_rows": history_count,
             "prior_observed_gap_days": float((event_start - max(item[0] for item in history)).total_seconds() / 86400),
             "exact_counterfactual": exact,
             "interpretation": ("P05 episode satisfies the >=5 transaction count branch after >=90 days; "
                                "not a claim that every row independently triggers the rule" if pattern == "P05" else
                                "Declared synthetic intervention or legitimate lookalike; not verified crime")}
            for scenario, label, rows in ((positive_case, "simulated_intervention", count),
                                          (control_case, "benign_lookalike", negative_count))
        ])
        self.pairs += 1
        self.exact_pairs += int(exact)
        if pattern == "P05" and negative_count < 5:
            self.scenarios[-1]["interpretation"] = "Legitimate low-intensity reopening after inactivity; a reference-negative scenario assumption"

    def generate(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        repetitions = max(1, self.rows // 125_000)
        for cycle, day in enumerate(range(180, 691, 30)):
            for pattern_index, pattern in enumerate(PATTERNS):
                for magnitude in range(2):
                    for repetition in range(repetitions):
                        exact = (cycle + pattern_index + magnitude + repetition) % 4 == 0
                        self._episode(pattern, magnitude == 0, day, exact)
        remaining = self.rows - len(self.records)
        if remaining <= 0:
            raise CoverageError("Requested volume cannot contain this version's fixed coverage design")
        background_accounts = [self.account() for _ in range(max(200, self.rows // 40))]
        scales = self.rng.lognormal(12, .7, len(background_accounts))
        open_days = self.rng.integers(0, self.days - 60, len(background_accounts))
        change_days = np.minimum(self.days - 1, open_days + self.rng.integers(15, 181, len(background_accounts)))
        activity = self.rng.lognormal(0, 1, len(background_accounts))
        probabilities = activity / activity.sum()
        chosen_accounts = self.rng.choice(len(background_accounts), size=remaining, p=probabilities)
        for chosen in chosen_accounts:
            account_index = int(chosen)
            account, base, changed = background_accounts[account_index]
            seconds = int(self.rng.integers(int(open_days[account_index]) * 86400, self.days * 86400))
            moment = self.start + timedelta(seconds=seconds)
            device = changed if seconds >= int(change_days[account_index]) * 86400 else base
            self.row(account, device, moment, float(self.rng.lognormal(np.log(scales[account_index]), 1.05)),
                     debit=bool(self.rng.random() < .7))
        references = self.rng.permutation(self.rows)
        for record, lineage, serial in zip(self.records, self.lineage, references):
            reference = f"T{self.salt}{int(serial):07x}"
            record["source_transaction_reference"] = lineage["transaction_ref"] = reference
        raw, lineage = pd.DataFrame(self.records, columns=UPLOAD_COLUMNS), pd.DataFrame(self.lineage, columns=LINEAGE_COLUMNS)
        order = raw.sort_values(["value_datetime", "source_transaction_reference"], kind="stable").index
        return raw.loc[order].reset_index(drop=True), lineage.loc[order].reset_index(drop=True)


def generate_dataset(out: Path, *, rows: int = DEFAULT_ROWS, seed: int = 2026110403,
                     start: date = DEFAULT_START, days: int = DEFAULT_DAYS) -> dict:
    """Publish one new deterministic study; refuse reuse and fail missing coverage."""
    out = out.resolve()
    if out.exists():
        raise FileExistsError("Choose a new output directory; previous datasets are immutable")
    study = _Study(rows, seed, start, days)
    raw, lineage = study.generate()
    # Round-trip through the exact upload reader, so normalizer checks see CSV cells.
    csv_text = raw.to_csv(index=False, lineterminator="\n")
    payload = csv_text.encode("utf-8")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise CoverageError("Generated upload exceeds the existing 50 MiB API limit")
    coverage = validate_coverage(read_csv_bytes(payload), lineage)
    out.mkdir(parents=True, exist_ok=False)
    with (out / "transactions.csv").open("xb") as handle:
        handle.write(payload)
    lineage.to_csv(out / "lineage.csv", index=False, lineterminator="\n")
    _json(out / "scenarios.json", study.scenarios)
    _json(out / "coverage.json", coverage)
    exact_event = lineage.exact_counterfactual.eq(1) & lineage.role.eq("event")
    positives = int(raw.is_fraud.eq(1).sum())
    exact_negative_rows = int((exact_event & raw.is_fraud.eq(0)).sum())
    report = {
        "version": VERSION, "seed": seed, "rows": rows, "currency": "IDR", "synthetic": True,
        "period_start": study.start.isoformat(), "period_end_exclusive": (study.start + timedelta(days=days)).isoformat(),
        "days": days, "upload_bytes": len(payload), "max_upload_bytes": MAX_UPLOAD_BYTES,
        "schema": list(UPLOAD_COLUMNS), "legacy_generator_changed": False,
        "design": {"history_row_range_per_pair": list(HISTORY_ROW_RANGE),
                   "history_spacing": "irregular per pair, shared between intervention and benign counterfactual",
                   "ATO_P04_history_age_range_days": {"oldest": [80, 140], "latest": [1, 7]},
                   "P05_history_age_range_days": {"oldest": [150, 175], "latest": [100, 120]},
                   "background_activity_lognormal_sigma": 1.0,
                   "background_start_days_range": [0, days - 60],
                   "label_independent_channel_support": list(CHANNELS),
                   "episode_days": list(range(180, 691, 30)), "episode_repetitions": max(1, rows // 125_000),
                   "positive_event_count_range": [16, 24], "P05_history_gap_min_days": 100,
                   "low_ticket_range_IDR": [1000, 25000],
                   "exact_counterfactual_target_share": EXACT_CONTROL_TARGET_SHARE,
                   "actual_episode_pairs": study.pairs, "actual_exact_pairs": study.exact_pairs,
                   "actual_exact_pair_share": study.exact_pairs / study.pairs,
                   "all_design_numbers_are_research_choices": True},
        "counterfactual_diagnostic": {
            "positive_event_rows": positives,
            "exact_counterfactual_negative_event_rows": exact_negative_rows,
            "conservative_precision_ceiling_at_full_recall": positives / (positives + exact_negative_rows),
            "interpretation": "At 100% recall a deterministic current-feature scorer also flags these identical negative event rows. This conservative bound applies to full recall only, not every operating point; it ignores additional ambiguous partial lookalikes.",
        },
        "label_policy": "is_fraud=1 on simulated intervention event rows for ATO/P04/P05 only; histories, ordinary traffic and benign lookalikes=0 by simulation assumption",
        "limitations": [
            "Labels describe synthetic scenario intent, not adjudicated crime or production prevalence.",
            "Legitimate lookalikes include exact observable counterfactuals; no model can recover unobserved intent perfectly from these fields.",
            "Nonexact lookalikes share each event's initial observable rows until intensity diverges. A latent intervention label does not make its first transaction instantly distinguishable.",
            "P05 low tickets qualify its whole-episode count branch, not the 25M amount branch; labels are not per-row rule outputs.",
            "Fixed repeated calendar cohorts guarantee study coverage but are not observed bank activity or independent real customers.",
            "Generated precise +07:00 timestamps and all other values are simulated; no missing source facts are reconstructed.",
            "No AML laundering target is supplied; this is a separate transaction-level Fraud coverage study.",
        ],
        "coverage": coverage,
        "files": {name: {"sha256": sha256(out / name), "bytes": (out / name).stat().st_size}
                  for name in ("transactions.csv", "lineage.csv", "scenarios.json", "coverage.json")},
        "generator_code_sha256": sha256(Path(__file__)),
    }
    _json(out / "manifest.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS)
    parser.add_argument("--seed", type=int, default=2026110403)
    parser.add_argument("--start-date", type=date.fromisoformat, default=DEFAULT_START)
    args = parser.parse_args()
    report = generate_dataset(args.out, rows=args.rows, seed=args.seed, start=args.start_date)
    print(json.dumps({"out": str(args.out), "rows": report["rows"], "upload_bytes": report["upload_bytes"],
                      "coverage": report["coverage"]["splits"]}, indent=2))


if __name__ == "__main__":
    main()
