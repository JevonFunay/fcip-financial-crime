"""Source-cadence account simulation, preserving clock observability limits.

This new generator never edits v1 or its artifacts. Account-heldout partitions
share source calendar support: they are NOT chronological holdouts. Bootstrap
day gaps span each actual source parent's observed calendar, while complete
parent observations condition amounts and context jointly. Generated event
dates are simulations; source date-only precision and unknown timezone remain
unknown. Synthetic stress labels are evaluation assumptions, not crime facts.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from . import generate as previous
from .standardize import CANONICAL_COLUMNS, normalize_csv

GENERATOR_VERSION = "behavioral_source_cadence_v2.0.0"
SPLIT_SEED = previous.SPLIT_SEED
SPLITS = previous.SPLITS
SOURCE_COLUMNS = previous.SOURCE_COLUMNS
STRESS_RECIPES = previous.STRESS_RECIPES
LOOKALIKE_RECIPES = (*previous.LOOKALIKE_RECIPES, "legitimate_dormant_reactivation")
LABEL_COLUMNS = (*previous.LABEL_COLUMNS, "intraday_observable", "observation_scope")
LINEAGE_COLUMNS = previous.LINEAGE_COLUMNS
source_partitions = previous.source_partitions


def _id(seed: int, split: str, kind: str, index: int) -> str:
    return previous._identifier(seed, split, f"{GENERATOR_VERSION}:{kind}", index)


def _positive_day_gaps(observations: pd.DataFrame) -> np.ndarray:
    days = np.sort(observations.transaction_date.dt.floor("D").astype("int64").to_numpy())
    gaps = np.diff(days) / 86_400_000_000_000
    return gaps[gaps > 0]


def _fit_profile(source: Path, frame: pd.DataFrame, partitions: dict) -> dict:
    profile = previous._fit_profile(source, frame, partitions)
    training = frame[frame.account_number.isin(partitions["train"])]
    groups = list(training.groupby("account_number", sort=True))
    positive = np.concatenate([_positive_day_gaps(group) for _, group in groups])
    counts = training.groupby("account_number").size()
    profile.update({
        "generator_version": GENERATOR_VERSION,
        "calendar_policy": "Per-parent actual source calendar support; overlapping account-heldout calendars, not chronological holdout.",
        "cadence_reference": {
            "mean_observations_per_account": float(counts.mean()),
            "account_count_quantiles": {str(q): float(counts.quantile(q)) for q in (.1, .5, .9)},
            "positive_day_gap_quantiles": {str(q): float(np.quantile(positive, q)) if len(positive) else None
                                           for q in (.1, .5, .9, .95)},
        },
        "source_precision_frequencies": training.timestamp_precision.value_counts(normalize=True).sort_index().to_dict(),
        "source_timezone_unknown_share": float((~training.source_timezone_known).mean()),
        "fit_policy": "All global references use original source TRAIN accounts only. Each heldout account conditions locally on its actual heldout source parent, including its cadence and precision.",
    })
    return profile


def _recipes(count: int, rng: np.random.Generator) -> dict[int, str]:
    accounts = rng.permutation(count)
    stress = min(count, max(1, round(count * .12)))
    benign = min(count - stress, round(count * .24))
    return {**{int(accounts[i]): STRESS_RECIPES[i % len(STRESS_RECIPES)] for i in range(stress)},
            **{int(accounts[stress + i]): LOOKALIKE_RECIPES[i % len(LOOKALIKE_RECIPES)] for i in range(benign)}}


def _cadence_days(parent: pd.DataFrame, count: int, rng: np.random.Generator) -> tuple[np.ndarray, dict]:
    observed = np.sort(parent.transaction_date.dt.floor("D").astype("int64").to_numpy())
    start = pd.Timestamp(int(observed[0]), tz="UTC")
    span = int((observed[-1] - observed[0]) // 86_400_000_000_000)
    source_gaps = np.diff(observed) / 86_400_000_000_000
    if count == 1 or span == 0:
        days = np.zeros(count, dtype=int)
    else:
        # Bootstrap empirical calendar gaps, including observed same-day ties.
        # Rescaling retains this parent's actual multi-year support, rather
        # than compressing its source observations into a dense180-day clock.
        gaps = rng.choice(source_gaps, count - 1, replace=True) if len(source_gaps) else np.ones(count - 1)
        if not gaps.sum():
            gaps = np.ones(count - 1)
        days = np.r_[0, np.rint(np.cumsum(gaps) / gaps.sum() * span)].astype(int)
    shared_hiatus = False
    positive = source_gaps[source_gaps > 0]
    if count >= 12 and span > 10 and len(positive) and rng.random() < .20:
        # This mechanism is selected independently of every scenario/label.
        # Ordinary/benign/stress accounts all share source-derived long gaps.
        upper = positive[positive >= np.median(positive)]
        gap = min(int(rng.choice(upper)), max(1, span // 3))
        if gap > 0 and span - gap > 2:
            anchor = int(rng.integers(max(1, span // 5), max(2, (span - gap) * 3 // 4)))
            compact = np.rint(days / max(1, span) * (span - gap)).astype(int)
            days = compact + np.where(compact >= anchor, gap, 0)
            shared_hiatus = True
    return days, {"start": start, "span_days": span, "positive_gaps": positive,
                  "shared_hiatus": shared_hiatus}


def _event_block(days: np.ndarray, positive_gaps: np.ndarray, recipe: str,
                 rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    count = len(days)
    # Preserve ordinary prehistory and follow-up for every injected recipe.
    if count < 8 or days[-1] - days[0] < 4:
        return np.array([], dtype=int), np.array([], dtype=int)
    n = min(max(2, count // 8), int(rng.integers(4, 15)), count - 4)
    lower = max(2, count // 5)
    upper = min(count - n - 2, max(lower, count * 3 // 4))
    if upper < lower:
        lower, upper = 2, count - n - 2
    if upper < lower:
        return np.array([], dtype=int), np.array([], dtype=int)
    candidates = np.arange(lower, upper + 1)
    if recipe in {"dormant_reactivation", "legitimate_dormant_reactivation"}:
        gaps = days[candidates] - days[candidates - 1]
        # Choose an existing source-cadence hiatus. Stress and its legitimate
        # control use exactly the same selection/timing distribution.
        substantial = candidates[gaps >= np.median(gaps)]
        pivot = int(rng.choice(substantial if len(substantial) else candidates))
    else:
        pivot = int(rng.choice(candidates))
    indices = np.arange(pivot, pivot + n)
    small_gaps = positive_gaps[positive_gaps <= np.median(positive_gaps)] if len(positive_gaps) else np.array([1.])
    span = max(0, int(rng.choice(small_gaps)))
    if recipe in {"card_testing_burst", "shopping"}:
        span = min(span, 2)
    # A cluster may be visible only at day resolution. Its truth remains in
    # evaluation even when the source cannot establish intraday ordering.
    latest = int(days[-2]) - 1
    if latest <= int(days[1]):
        return np.array([], dtype=int), np.array([], dtype=int)
    anchor = min(max(int(days[pivot]), int(days[1]) + 1), latest)
    span = min(span, max(0, latest - anchor))
    event_days = anchor + np.sort(rng.integers(0, span + 1, n))
    return indices, event_days


def _account(parent: pd.DataFrame, count: int, rng: np.random.Generator, recipe: str | None,
             fitted: dict, seed: int, split: str, account_index: int) -> tuple[pd.DataFrame, np.ndarray, np.ndarray, np.ndarray, dict]:
    picked = rng.integers(0, len(parent), count)
    result = parent.iloc[picked].copy().reset_index(drop=True)
    stable = previous._profile_observation(parent, rng)
    account = "A" + _id(seed, split, "account", account_index)
    result["account_number"] = account
    result["customer_age"] = stable.customer_age
    result["customer_occupation"] = stable.customer_occupation
    result["amount"] = np.maximum(.01, np.round(result.amount.to_numpy() * rng.lognormal(0, .06)
                                               * rng.lognormal(0, .05, count), 2))
    result["account_balance"] = np.round(result.account_balance.to_numpy() * rng.lognormal(0, .025, count), 2)
    # Preserve each source parent's context distribution, with neutral stable
    # identifiers per simulated account. No context is a scenario marker.
    devices = {value: "D" + _id(seed, split, f"device:{account_index}:{value}", 0) if value else ""
               for value in result.device_ref.fillna("").unique()}
    ips = {value: previous._ip(int(_id(seed, split, f"ip:{account_index}:{value}", 0)[:8], 16)) if value else ""
           for value in result.ip_address.fillna("").unique()}
    result["device_ref"] = result.device_ref.fillna("").map(devices)
    result["ip_address"] = result.ip_address.fillna("").map(ips)
    days, cadence = _cadence_days(parent, count, rng)
    event = np.zeros(count, dtype=bool)
    labels = np.zeros(count, dtype=int)
    if recipe:
        indices, injected_days = _event_block(days, cadence["positive_gaps"], recipe, rng)
        event[indices] = True
        labels[indices] = int(recipe in STRESS_RECIPES)
        days[indices] = injected_days
        if len(indices):
            low = float(np.ceil(fitted["amount_quantiles"]["0.1"] * 100) / 100)
            high = max(low, float(np.floor(fitted["amount_quantiles"]["0.4"] * 100) / 100))
            if recipe in {"card_testing_burst", "micro_splitting", "pass_through_proxy", "shopping", "repeated_small_payments"}:
                pivot = rng.uniform(low, high)
                result.loc[indices, "amount"] = np.round(np.clip(pivot * rng.lognormal(0, .18, len(indices)), low, high), 2)
                result.loc[indices, "direction"] = "DEBIT"
            if recipe in {"session_change", "new_phone", "travel", "card_testing_burst", "shopping"}:
                result.loc[indices, "device_ref"] = "D" + _id(seed, split, "changed_device", account_index)
                result.loc[indices, "ip_address"] = previous._ip(int(_id(seed, split, "changed_ip", account_index)[:8], 16))
            if recipe in {"session_change", "travel"}:
                alternatives = parent.location.dropna().unique()
                if len(alternatives):
                    result.loc[indices, "location"] = rng.choice(alternatives)
            if recipe in {"session_change", "card_testing_burst", "new_phone", "shopping"}:
                # The same broad login perturbation applies to benign and
                # stress events; attempts alone are not an injected answer key.
                changed = indices[rng.random(len(indices)) < .50]
                source_attempts = parent.login_attempts.dropna().to_numpy(dtype=int)
                if len(changed) and len(source_attempts):
                    result.loc[changed, "login_attempts"] = rng.choice(source_attempts, len(changed))
            if recipe == "pass_through_proxy":
                result.loc[indices, "direction"] = np.where(np.arange(len(indices)) % 2, "DEBIT", "CREDIT")
    # Preserve the bootstrap observation's precision and original wall-clock
    # minute, but not its original date. No latent intraday schedule is exposed
    # as an observed time on a date-only row, and no timezone is inferred.
    supplied_clock = parent.iloc[picked].transaction_date
    source_second = supplied_clock.dt.hour.to_numpy() * 3600 + supplied_clock.dt.minute.to_numpy() * 60 + supplied_clock.dt.second.to_numpy()
    dates = cadence["start"] + pd.to_timedelta(days, unit="D")
    minute_known = result.timestamp_precision.ne("day").to_numpy()
    dates += pd.to_timedelta(np.where(minute_known, source_second, 0), unit="s")
    result["transaction_date"] = dates
    result["source_timezone_known"] = False
    result["currency"] = "USD"
    for name in ("counterparty_ref", "counterparty_country", "source_status", "description", "transaction_type"):
        result[name] = ""
    return result, event, labels, picked, cadence


def generate(source: Path, out: Path, split: str, rows: int, seed: int,
             fit_profile: Path | None = None, reference_date: date = date(2026, 10, 16)) -> dict:
    """Write an exact-size new account-heldout simulation without overwrites."""
    source, out = Path(source), Path(out)
    if split not in SPLITS:
        raise ValueError(f"split must be one of {SPLITS}")
    if isinstance(rows, bool) or not isinstance(rows, int) or rows < 1:
        raise ValueError("rows must be a positive integer")
    if out.exists():
        raise FileExistsError(f"Refusing to overwrite {out}")
    if split != "train" and fit_profile is None:
        raise ValueError("Heldout generation requires frozen training fit_profile.json")
    if split == "train" and fit_profile is not None:
        raise ValueError("Training computes its own source-TRAIN-only profile")
    partitions = source_partitions(source)
    normalized = normalize_csv(source)
    if len(normalized.quarantine):
        raise ValueError("Source must normalize without quarantined observations")
    frame = normalized.frame.copy()
    for column in ("amount", "account_balance", "customer_age", "transaction_duration", "login_attempts"):
        frame[column] = frame[column].astype(float)
    if frame.transaction_date.max().date() > reference_date:
        raise ValueError("reference_date precedes observed source support; no future observations are silently truncated")
    digest = previous._sha256(source)
    if split == "train":
        fitted = _fit_profile(source, frame, partitions)
    else:
        fitted = json.loads(Path(fit_profile).read_text())
        required = {"generator_version": GENERATOR_VERSION, "source_sha256": digest,
                    "source_training_account_ids": partitions["train"], "split_seed": SPLIT_SEED, "currency": "USD"}
        if any(fitted.get(key) != value for key, value in required.items()):
            raise ValueError("Frozen fit profile does not match source, training partition or generator v2")
    available = frame[frame.account_number.isin(partitions[split])]
    groups = {str(account): group.sort_values(["transaction_date", "transaction_ref"], kind="stable").reset_index(drop=True)
              for account, group in available.groupby("account_number", sort=True)}
    rng = np.random.default_rng(seed)
    typical_count = fitted["cadence_reference"]["mean_observations_per_account"]
    account_count = min(rows, max(1, int(round(rows / typical_count))))
    cycle = rng.permutation(sorted(groups)).tolist()
    parents = [cycle[i % len(cycle)] for i in range(account_count)]
    weights = np.array([len(groups[parent]) for parent in parents]) * rng.lognormal(0, .10, account_count)
    allocation = (rows - account_count) * weights / weights.sum()
    counts = 1 + np.floor(allocation).astype(int)
    remainder = rows - int(counts.sum())
    counts[np.argsort(-(allocation - np.floor(allocation)), kind="stable")[:remainder]] += 1
    recipes = _recipes(account_count, rng)
    out.mkdir(parents=True, exist_ok=False)
    if split == "train":
        previous._write_json(out / "fit_profile.json", fitted)
    else:
        (out / "fit_profile.json").write_bytes(Path(fit_profile).read_bytes())
    files = [out / name for name in ("transactions.csv", "source_shape.csv", "ground_truth.csv", "lineage.csv")]
    totals = {"rows": 0, "accounts": account_count, "stress_rows": 0, "lookalike_rows": 0,
              "shared_hiatus_accounts": 0, "stress_accounts_with_followup": 0, "lookalike_accounts_with_followup": 0,
              "recipe_accounts_without_event_support": 0}
    scenarios, precision_counts, observability = {}, {}, {}
    spans, account_gaps, generated_start, generated_end = [], [], None, None
    handles = []
    try:
        handles = [path.open("w", encoding="utf-8", newline="") for path in files]
        writers = [csv.writer(handle, lineterminator="\n") for handle in handles]
        for writer, columns in zip(writers, (CANONICAL_COLUMNS, SOURCE_COLUMNS, LABEL_COLUMNS, LINEAGE_COLUMNS)):
            writer.writerow(columns)
        for i, (parent, count) in enumerate(zip(parents, counts)):
            recipe = recipes.get(i)
            result, event, labels, picked, cadence = _account(groups[parent], int(count), rng, recipe, fitted, seed, split, i)
            account = result.account_number.iloc[0]
            totals["shared_hiatus_accounts"] += int(cadence["shared_hiatus"])
            totals["recipe_accounts_without_event_support"] += int(bool(recipe) and not event.any())
            spans.append(cadence["span_days"])
            ordered_times = result.transaction_date.sort_values()
            account_gaps.extend((ordered_times.diff().dt.total_seconds().dropna() / 86400).tolist())
            generated_start = min(generated_start, ordered_times.iloc[0]) if generated_start is not None else ordered_times.iloc[0]
            generated_end = max(generated_end, ordered_times.iloc[-1]) if generated_end is not None else ordered_times.iloc[-1]
            if event.any():
                followup = bool(result.loc[~event, "transaction_date"].max() > result.loc[event, "transaction_date"].max())
                before = bool(result.loc[~event, "transaction_date"].min() < result.loc[event, "transaction_date"].min())
                if not followup or not before:
                    raise RuntimeError("Every event recipe must retain ordinary prehistory and follow-up")
                totals["stress_accounts_with_followup" if labels.any() else "lookalike_accounts_with_followup"] += 1
            originals = groups[parent][["transaction_ref", "source_ordinal"]].to_dict("records")
            records = result.to_dict("records")
            order = np.argsort(result.transaction_date.to_numpy(), kind="stable")
            for j in order:
                row = records[int(j)]
                reference = "T" + _id(seed, split, "transaction", totals["rows"])
                row["transaction_ref"] = reference
                precision = row["timestamp_precision"]
                when = row["transaction_date"]
                canonical_format = "%Y-%m-%d" if precision == "day" else "%Y-%m-%dT%H:%M:%S" if precision == "second" else "%Y-%m-%dT%H:%M"
                row["transaction_date"] = when.strftime(canonical_format)
                row["amount"], row["account_balance"] = f"{row['amount']:.2f}", f"{row['account_balance']:.2f}"
                row["customer_age"], row["login_attempts"] = int(row["customer_age"]), int(row["login_attempts"])
                writers[0].writerow([row[column] for column in CANONICAL_COLUMNS])
                source_format = "%m/%d/%Y" if precision == "day" else "%m/%d/%Y %H:%M:%S" if precision == "second" else "%m/%d/%Y %H:%M"
                companion = when.strftime(source_format)
                writers[1].writerow([reference, account, row["amount"], companion, row["direction"].title(),
                                    row["location"], row["device_ref"], row["ip_address"], row["merchant_ref"],
                                    row["channel"].title(), row["customer_age"], row["customer_occupation"],
                                    row["transaction_duration"], row["login_attempts"], row["account_balance"]])
                low, high = fitted["amount_quantiles"]["0.1"], fitted["amount_quantiles"]["0.4"]
                amount = float(row["amount"])
                band = "below_train_p10" if amount < low else "train_p10_p40" if amount <= high else "above_train_p40"
                scenario = recipe if event[j] else "ordinary"
                label_type = "synthetic_stress" if labels[j] else "synthetic_lookalike" if event[j] else "synthetic_background"
                scenario_id = "S" + _id(seed, split, "scenario", i) if event[j] else ""
                intraday = precision != "day"
                writers[2].writerow([reference, account, int(labels[j]), label_type, scenario, scenario_id, band,
                                     intraday, "minute_wall_clock_simulation" if intraday else "day_only_simulation"])
                original = originals[int(picked[j])]
                writers[3].writerow([reference, account, parent, original["transaction_ref"], int(original["source_ordinal"])])
                totals["rows"] += 1
                totals["stress_rows"] += int(labels[j])
                totals["lookalike_rows"] += int(event[j] and not labels[j])
                scenarios[scenario] = scenarios.get(scenario, 0) + 1
                precision_counts[precision] = precision_counts.get(precision, 0) + 1
                if event[j]:
                    key = f"{scenario}:{'intraday_supplied' if intraday else 'intraday_unobservable'}"
                    observability[key] = observability.get(key, 0) + 1
    finally:
        for handle in handles:
            handle.close()
    if totals["rows"] != rows:
        raise RuntimeError("Exact output size invariant failed")
    manifest = {
        "generator_version": GENERATOR_VERSION, "profile": "fcip_canonical_v1", "split": split,
        "seed": seed, "reference_date": reference_date.isoformat(), "currency": "USD", "synthetic_declaration": True,
        "requested_rows": rows, "counts": totals, "source_sha256": digest, "source_rows": len(frame), "source_columns": 15,
        "source_partitions": partitions, "source_partition_seed": SPLIT_SEED, "source_parent_account_ids": sorted(set(parents)),
        "fit_profile_sha256": previous._sha256(out / "fit_profile.json"),
        "files": {path.name: {"sha256": previous._sha256(path), "rows": rows} for path in files},
        "scenario_counts": scenarios, "timestamp_precision_counts": precision_counts,
        "event_observability_counts": observability, "source_timezone_unknown": rows,
        "split_policy": "Account-group holdout on actual source parents before augmentation; calendar support overlaps. NOT a chronological holdout.",
        "window": {"start_inclusive": generated_start.isoformat(), "last_observed": generated_end.isoformat(),
                   "calendar_support": "Actual source-parent calendar support, not fixed180-day windows.", "chronological_holdout": False},
        "cadence_summary": {"median_parent_span_days": float(np.median(spans)),
                            "median_generated_gap_days": float(np.median(account_gaps)) if account_gaps else None,
                            "p95_generated_gap_days": float(np.quantile(account_gaps, .95)) if account_gaps else None},
        "timestamp_policy": "All generated event dates are simulations within actual parent calendar support. Each bootstrapped source observation retains its day/minute precision; source timezone remains unknown. Day-only rows have no recovered intraday order; all stress rows remain in evaluation.",
        "conditioning_policy": "Complete observations and empirical day gaps come from each account's actual source parent in its pre-augmentation partition; global references use TRAIN only. Generated age/occupation is one stable observed pair; source context frequency variation is retained with neutral device/IP IDs.",
        "label_policy": "Sidecar0/1 labels describe synthetic interventions/background/lookalikes only, not verified legitimacy or crime. No labels or recipe/provenance fields enter transactions.csv or unsupervised fitting.",
        "network_policy": "No source counterparty graph, ownership or country is inferred. Pass-through remains an alternating-direction account proxy; balance is a recorded snapshot.",
        "simulation_parameters": {"target_observations_per_account": typical_count, "account_count_log_sd": .10,
                                  "stress_account_fraction": .12, "lookalike_account_fraction": .24,
                                  "shared_hiatus_probability_independent_of_label": .20,
                                  "event_count_rule": "min(max(2, account_count//8), uniform_integer4..14, account_count-4)",
                                  "calendar_gap_rule": "Bootstrap parent empirical calendar gaps then span-match that parent's actual observed support; shared hiatus size is sampled from its upper-half positive source gaps.",
                                  "dormancy_rule": "Stress and legitimate control select existing interior cadence gaps identically, then retain ordinary follow-up.",
                                  "provenance": "Explicit simulation design choices, not FRD/TRD crime thresholds or empirical prevalence."},
        "limitations": ["Account partitions are independent by source parent but not chronologically disjoint.",
                        "Generated clones are dependent augmentations, not new independent real evidence.",
                        "Source publisher, augmentation recipe and verified crime targets are unavailable.",
                        "Date-only stress cannot establish intraday ordering; supplied minute clocks still lack timezone.",
                        "Synthetic stress and benign recipes intentionally overlap; recipe labels are not real financial-crime ground truth."],
    }
    previous._write_json(out / "manifest.json", manifest)
    if previous._sha256(source) != digest:
        raise RuntimeError("Source changed during generation")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--split", choices=SPLITS, required=True)
    parser.add_argument("--rows", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--fit-profile", type=Path)
    parser.add_argument("--reference-date", type=date.fromisoformat, default=date(2026, 10, 16))
    print(json.dumps(generate(**vars(parser.parse_args())), sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
