"""Reproducible source-conditioned simulation, with evaluation answers separate.

The source is unlabelled. Stress labels describe our simulated interventions,
never observed crime. All generated times are UTC simulations, not recovered
source intraday observations. Amounts remain USD; no AML legal threshold or
counterparty network is invented. Recipe probabilities are simulation choices,
not estimates of population prevalence.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

from .standardize import CANONICAL_COLUMNS, normalize_csv

GENERATOR_VERSION = "behavioral_source_conditioned_v1.0.0"
SPLIT_SEED = 20261014
SPLITS = ("train", "validation", "test")
SOURCE_COLUMNS = (
    "TransactionID", "AccountID", "TransactionAmount", "TransactionDate", "TransactionType",
    "Location", "DeviceID", "IP Address", "MerchantID", "Channel", "CustomerAge",
    "CustomerOccupation", "TransactionDuration", "LoginAttempts", "AccountBalance",
)
STRESS_RECIPES = (
    "card_testing_burst", "session_change", "dormant_reactivation", "micro_splitting",
    "pass_through_proxy",
)
LOOKALIKE_RECIPES = ("new_phone", "travel", "shopping", "repeated_small_payments")
LABEL_COLUMNS = (
    "transaction_ref", "account_number", "label", "label_type", "scenario", "scenario_id", "amount_band",
)
LINEAGE_COLUMNS = (
    "transaction_ref", "account_number", "source_account_id", "source_transaction_id", "source_ordinal",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def source_partitions(source: Path, split_seed: int = SPLIT_SEED) -> dict[str, list[str]]:
    """Split original account identities before any bootstrap or augmentation."""
    accounts = pd.read_csv(source, usecols=["AccountID"], dtype="string", encoding="utf-8-sig")
    ids = sorted(accounts.AccountID.dropna().unique().tolist())
    if len(ids) < 3:
        raise ValueError("Source needs at least three distinct accounts for disjoint partitions")
    ids = np.random.default_rng(split_seed).permutation(ids).tolist()
    train_end = min(len(ids) - 2, max(1, int(len(ids) * 0.70)))
    validation_end = min(len(ids) - 1, max(train_end + 1, int(len(ids) * 0.85)))
    return {"train": sorted(ids[:train_end]), "validation": sorted(ids[train_end:validation_end]),
            "test": sorted(ids[validation_end:])}


def _fit_profile(source: Path, frame: pd.DataFrame, partitions: dict) -> dict:
    training = frame[frame.account_number.isin(partitions["train"])]
    quantiles = {str(q): float(training.amount.quantile(q)) for q in (0.1, 0.25, 0.4, 0.5, 0.75, 0.9, 0.99)}
    return {
        "generator_version": GENERATOR_VERSION, "source_sha256": _sha256(source),
        "split_seed": SPLIT_SEED, "source_training_account_ids": partitions["train"],
        "source_training_rows": len(training), "currency": "USD",
        "amount_quantiles": quantiles,
        "channel_frequencies": training.channel.value_counts(normalize=True).sort_index().to_dict(),
        "numeric_reference": {c: {"median": float(training[c].median()),
                                    "p99": float(training[c].quantile(0.99))}
                              for c in ("login_attempts", "transaction_duration", "account_balance")},
        "location_vocabulary": sorted(training.location.dropna().unique().tolist()),
        "fit_policy": "Global statistics use original source training accounts only, before augmentation.",
    }


def _identifier(seed: int, split: str, kind: str, index: int) -> str:
    # Neither prefix nor hash exposes a scenario/cohort; split prevents collisions.
    return hashlib.sha256(f"{seed}:{split}:{kind}:{index}".encode()).hexdigest()[:24]


def _ip(index: int) -> str:
    # RFC 5737 documentation ranges. Sharing is intentional, as in ordinary NAT.
    network = ("192.0.2", "198.51.100", "203.0.113")[(index // 254) % 3]
    return f"{network}.{index % 254 + 1}"


def _window(split: str, reference_date: date) -> tuple[datetime, datetime]:
    end = datetime.combine(reference_date + timedelta(days=1), time(), tzinfo=timezone.utc)
    end -= timedelta(days=180 * (2 - SPLITS.index(split)))
    return end - timedelta(days=180), end


def _account_recipes(count: int, rng: np.random.Generator) -> dict[int, str]:
    choices = rng.permutation(count)
    stress_n = min(count, max(1, int(round(count * 0.12))))
    benign_n = min(count - stress_n, int(round(count * 0.24)))
    recipes = {}
    for i in range(stress_n):
        recipes[int(choices[i])] = STRESS_RECIPES[i % len(STRESS_RECIPES)]
    for i in range(benign_n):
        recipes[int(choices[stress_n + i])] = LOOKALIKE_RECIPES[i % len(LOOKALIKE_RECIPES)]
    return recipes


def _profile_observation(observations: pd.DataFrame, rng: np.random.Generator) -> pd.Series:
    # The pair comes from one real source observation and is frozen thereafter.
    # Prefer an age/occupation pairing with a plausible life stage when present;
    # these cutoffs are explicitly simulation choices, not model features/rules.
    age, occupation = observations.customer_age, observations.customer_occupation.str.lower()
    plausible = ((occupation.eq("student") & age.le(30)) | (occupation.eq("retired") & age.ge(55))
                 | (occupation.eq("doctor") & age.ge(25)) | (occupation.eq("engineer") & age.ge(22)))
    candidates = observations.loc[plausible] if plausible.any() else observations
    return candidates.iloc[int(rng.integers(len(candidates)))]


def _observations_for_account(
    observations: pd.DataFrame, count: int, rng: np.random.Generator, recipe: str | None,
    profile: dict, locations: list[str], start: datetime, account_id: str, device_id: str,
    ip_index: int,
) -> tuple[pd.DataFrame, np.ndarray, np.ndarray, np.ndarray]:
    """Bootstrap complete source rows jointly; intervene only on named events."""
    picked = rng.integers(0, len(observations), size=count)
    result = observations.iloc[picked].copy().reset_index(drop=True)
    stable = _profile_observation(observations, rng)
    result["account_number"] = account_id
    result["customer_age"] = stable.customer_age
    result["customer_occupation"] = stable.customer_occupation
    result["device_ref"] = device_id
    result["location"] = stable.location
    result["ip_address"] = _ip(ip_index)
    # Preserve the joint source amount/channel/direction/login/duration/balance
    # observation, with mild continuous variation and one account ticket scale.
    ticket_scale = rng.lognormal(0, 0.18)
    result["amount"] = np.maximum(0.01, np.round(result.amount.to_numpy() * ticket_scale
                                               * rng.lognormal(0, 0.10, count), 2))
    result["account_balance"] = np.round(result.account_balance.to_numpy() * rng.lognormal(0, 0.06, count), 2)
    result["transaction_duration"] = np.maximum(1, np.round(result.transaction_duration.to_numpy()
                                                           * rng.lognormal(0, 0.07, count))).astype(int)
    # Week/day modulation and ordinary busy periods prevent clock uniformity or
    # a burst alone from identifying an injected stress recipe perfectly.
    day = rng.integers(0, 180, count)
    second = np.clip(rng.normal(14 * 3600, 4 * 3600, count), 0, 86399).astype(int)
    seconds = day * 86400 + second
    if count >= 12 and rng.random() < 0.30:
        busy = rng.choice(count, min(count // 5, int(rng.integers(6, 24))), replace=False)
        seconds[busy] = int(rng.integers(30, 165)) * 86400 + rng.integers(8 * 3600, 20 * 3600, len(busy))
    # Ordinary context variation occurs in every cohort, independent of recipe.
    change = rng.random(count) < 0.035
    result.loc[change, "device_ref"] = device_id + "b"
    travel = rng.random(count) < 0.025
    if locations:
        result.loc[travel, "location"] = rng.choice(locations, int(travel.sum()))
    result.loc[change | travel, "ip_address"] = _ip(ip_index + 271)
    event = np.zeros(count, dtype=bool)
    labels = np.zeros(count, dtype=int)
    if recipe and count >= 8:
        n_event = min(count // 3, int(rng.integers(9, 34)))
        indices = rng.choice(count, n_event, replace=False)
        event[indices] = True
        labels[indices] = int(recipe in STRESS_RECIPES)
        event_day = int(rng.integers(105, 172))
        anchor = event_day * 86400 + int(rng.integers(8 * 3600, 20 * 3600))
        low = float(np.ceil(profile["amount_quantiles"]["0.1"] * 100) / 100)
        high = float(np.floor(profile["amount_quantiles"]["0.4"] * 100) / 100)
        high = max(low, high)
        if recipe in {"card_testing_burst", "micro_splitting", "repeated_small_payments", "shopping", "pass_through_proxy"}:
            pivot = rng.uniform(low, high)
            amounts = np.clip(pivot * rng.lognormal(0, 0.18, n_event), low, high)
            result.loc[indices, "amount"] = np.round(amounts, 2)
            # Broad, overlapping timing distributions across both label types.
            span = int(rng.integers(10 * 60, 3 * 86400))
            if recipe in {"card_testing_burst", "shopping"}:
                span = int(rng.integers(5 * 60, 8 * 3600))
            seconds[indices] = anchor + np.sort(rng.integers(0, span, n_event))
            result.loc[indices, "direction"] = "DEBIT"
        else:
            seconds[indices] = anchor + np.sort(rng.integers(0, 3 * 86400, n_event))
        if recipe in {"session_change", "new_phone", "travel", "card_testing_burst"}:
            result.loc[indices, "device_ref"] = device_id + "c"
            result.loc[indices, "ip_address"] = _ip(ip_index + 419)
        if recipe in {"session_change", "travel"} and locations:
            alternatives = [location for location in locations if location != stable.location]
            if alternatives:
                result.loc[indices, "location"] = rng.choice(alternatives)
        if recipe in {"session_change", "card_testing_burst", "new_phone", "shopping"}:
            # Neither attempt count nor duration is a deterministic answer key.
            raised = indices[rng.random(n_event) < (0.65 if labels.any() else 0.40)]
            result.loc[raised, "login_attempts"] = rng.integers(1, 6, len(raised))
            result.loc[indices, "channel"] = rng.choice(["ONLINE", "ATM", "BRANCH"], n_event,
                                                        p=[0.70, 0.15, 0.15])
        if recipe == "dormant_reactivation":
            # The source has no account-opening date: this is observed silence.
            gap_days = int(rng.integers(35, 65))
            ordinary = ~event
            seconds[ordinary] %= max(1, (event_day - gap_days) * 86400)
        if recipe == "pass_through_proxy":
            # Alternating direction on one account is only a behavioural proxy.
            # No link between two counterparties can be recovered from source.
            result.loc[indices, "direction"] = np.where(np.arange(n_event) % 2, "DEBIT", "CREDIT")
            base = float(rng.uniform(low, high))
            result.loc[indices, "amount"] = np.round(np.clip(base * rng.uniform(0.88, 1.12, n_event), low, high), 2)
    seconds = np.minimum(seconds, 180 * 86400 - 1)
    result["transaction_date"] = [start + timedelta(seconds=int(s)) for s in seconds]
    result["currency"] = "USD"
    result["timestamp_precision"] = "second"
    result["source_timezone_known"] = True
    for field in ("counterparty_ref", "counterparty_country", "source_status", "description", "transaction_type"):
        result[field] = ""
    return result, event, labels, picked


def generate(
    source: Path, out: Path, split: Literal["train", "validation", "test"], rows: int, seed: int,
    fit_profile: Path | None = None, reference_date: date = date(2026, 10, 15),
) -> dict:
    """Write an exact-sized split; refuse overwrites and heldout-statistic fits."""
    source, out = Path(source), Path(out)
    if split not in SPLITS:
        raise ValueError(f"Unknown split {split!r}; choose {SPLITS}")
    if isinstance(rows, bool) or not isinstance(rows, int) or rows < 1:
        raise ValueError("rows must be a positive integer")
    if out.exists():
        raise FileExistsError(f"Refusing to overwrite existing output directory: {out}")
    if split != "train" and fit_profile is None:
        raise ValueError("Heldout generation requires the saved training fit_profile.json")
    if split == "train" and fit_profile is not None:
        raise ValueError("Training computes its own source-training-only fit profile")
    partitions = source_partitions(source)
    normalized = normalize_csv(source)
    if normalized.report["quarantined"]:
        raise ValueError("Generator source must pass the explicit source schema without quarantined rows")
    frame = normalized.frame.copy()
    # Canonical normalization preserves Decimal money for ingestion. Simulation
    # uses floating arithmetic internally and emits fixed two-decimal strings.
    for column in ("amount", "account_balance", "customer_age", "transaction_duration", "login_attempts"):
        frame[column] = frame[column].astype(float)
    source_hash = _sha256(source)
    if split == "train":
        fitted = _fit_profile(source, frame, partitions)
    else:
        fitted = json.loads(Path(fit_profile).read_text(encoding="utf-8"))
        expected = {"generator_version": GENERATOR_VERSION, "source_sha256": source_hash,
                    "source_training_account_ids": partitions["train"], "split_seed": SPLIT_SEED,
                    "currency": "USD"}
        if any(fitted.get(key) != value for key, value in expected.items()):
            raise ValueError("Fit profile does not match this source, training partition or generator")
    available = frame[frame.account_number.isin(partitions[split])].copy()
    groups = {str(key): group.reset_index(drop=True) for key, group in available.groupby("account_number", sort=True)}
    rng = np.random.default_rng(seed)
    # ~250 observations/account supports a 30-observation/30-day baseline while
    # activity weights retain broad, source-conditioned frequency variation.
    account_count = max(1, rows // 250)
    parent_cycle = rng.permutation(sorted(groups)).tolist()
    parents = [parent_cycle[i % len(parent_cycle)] for i in range(account_count)]
    weights = np.array([len(groups[parent]) for parent in parents], dtype=float) * rng.lognormal(0, 0.65, account_count)
    reserve = min(60, rows // account_count)
    counts = rng.multinomial(rows - reserve * account_count, weights / weights.sum()) + reserve
    recipes = _account_recipes(account_count, rng)
    start, end = _window(split, reference_date)
    # Heldout locations and bootstrap observations are genuinely heldout. Global
    # quantiles remain frozen to train; no heldout rows affect fitted statistics.
    locations = sorted(available.location.dropna().unique().tolist())
    out.mkdir(parents=True, exist_ok=False)
    if split == "train":
        _write_json(out / "fit_profile.json", fitted)
        fit_hash = _sha256(out / "fit_profile.json")
    else:
        # An exact copy makes each dataset independently auditable without refits.
        (out / "fit_profile.json").write_bytes(Path(fit_profile).read_bytes())
        fit_hash = _sha256(out / "fit_profile.json")
    totals: dict[str, int] = {"rows": 0, "accounts": account_count, "stress_rows": 0, "lookalike_rows": 0}
    scenario_counts: dict[str, int] = {}
    amount_counts = {"below_train_p10": 0, "train_p10_p40": 0, "above_train_p40": 0}
    paths = [out / name for name in ("transactions.csv", "source_shape.csv", "ground_truth.csv", "lineage.csv")]
    handles = []
    try:
        for path in paths:
            handles.append(path.open("w", encoding="utf-8", newline=""))
        writers = [csv.writer(handle, lineterminator="\n") for handle in handles]
        for writer, header in zip(writers, (CANONICAL_COLUMNS, SOURCE_COLUMNS, LABEL_COLUMNS, LINEAGE_COLUMNS)):
            writer.writerow(header)
        for a, (parent, count) in enumerate(zip(parents, counts)):
            account_id = "A" + _identifier(seed, split, "account", a)
            device_id = "D" + _identifier(seed, split, "device", a)
            recipe = recipes.get(a)
            result, event, labels, picked = _observations_for_account(
                groups[parent], int(count), rng, recipe, fitted, locations, start,
                account_id, device_id, int(rng.integers(0, 762)),
            )
            order = np.argsort(result.transaction_date.to_numpy(), kind="stable")
            records = result.to_dict("records")
            originals = groups[parent][["transaction_ref", "source_ordinal"]].to_dict("records")
            for j in order:
                row = records[int(j)]
                reference = "T" + _identifier(seed, split, "transaction", totals["rows"])
                row["transaction_ref"] = reference
                timestamp = row["transaction_date"].isoformat().replace("+00:00", "Z")
                row["transaction_date"] = timestamp
                # Formatting money to cents is part of the canonical input contract.
                row["amount"] = f"{row['amount']:.2f}"
                row["account_balance"] = f"{row['account_balance']:.2f}"
                row["customer_age"] = int(row["customer_age"])
                row["login_attempts"] = int(row["login_attempts"])
                writers[0].writerow([row[c] for c in CANONICAL_COLUMNS])
                companion_date = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).strftime("%m/%d/%Y %H:%M:%S+00:00")
                writers[1].writerow([
                    reference, account_id, row["amount"], companion_date, row["direction"].title(),
                    row["location"], row["device_ref"], row["ip_address"], row["merchant_ref"],
                    row["channel"].title(), row["customer_age"], row["customer_occupation"],
                    row["transaction_duration"], row["login_attempts"], row["account_balance"],
                ])
                low, high = fitted["amount_quantiles"]["0.1"], fitted["amount_quantiles"]["0.4"]
                amount = float(row["amount"])
                band = "below_train_p10" if amount < low else "train_p10_p40" if amount <= high else "above_train_p40"
                scenario = str(recipe) if event[j] else "ordinary"
                label_type = "synthetic_stress" if labels[j] else "synthetic_lookalike" if event[j] else "synthetic_background"
                scenario_id = "S" + _identifier(seed, split, "scenario", a) if event[j] else ""
                writers[2].writerow([reference, account_id, int(labels[j]), label_type, scenario, scenario_id, band])
                original = originals[int(picked[j])]
                writers[3].writerow([reference, account_id, parent, original["transaction_ref"], int(original["source_ordinal"])])
                totals["rows"] += 1
                totals["stress_rows"] += int(labels[j])
                totals["lookalike_rows"] += int(event[j] and not labels[j])
                scenario_counts[scenario] = scenario_counts.get(scenario, 0) + 1
                amount_counts[band] += 1
    finally:
        for handle in handles:
            handle.close()
    if totals["rows"] != rows:
        raise RuntimeError("Exact output row-count invariant failed")
    manifest = {
        "generator_version": GENERATOR_VERSION, "profile": "fcip_canonical_v1", "split": split,
        "seed": seed, "reference_date": reference_date.isoformat(), "currency": "USD",
        "synthetic_declaration": True, "requested_rows": rows, "counts": totals,
        "source_sha256": source_hash, "source_rows": len(frame), "source_columns": 15,
        "source_partitions": partitions, "source_partition_seed": SPLIT_SEED,
        "source_parent_account_ids": sorted(set(parents)), "fit_profile_sha256": fit_hash,
        "window": {"start_inclusive": start.isoformat(), "end_exclusive": end.isoformat(), "days": 180},
        "scenario_counts": scenario_counts, "amount_band_counts": amount_counts,
        "files": {path.name: {"sha256": _sha256(path), "rows": rows} for path in paths},
        "timestamp_policy": "All generated exact UTC timestamps are simulations; original day-only dates do not reveal intraday order or timezone.",
        "conditioning_policy": "Each account bootstraps complete observations from its actual source parent in its pre-augmentation partition. Stable age/occupation comes from one observed pair; home/device are simulated account context.",
        "label_policy": "Source has no fraud labels. Sidecar 0/1 labels identify simulated stress interventions versus generated background/lookalikes only; never model training input or confirmed crime.",
        "network_policy": "No source counterparty graph, ownership, country, source status or running balance is inferred. Pass-through is an alternating-direction account proxy only.",
        "simulation_parameters": {"target_observations_per_account": 250, "reserved_observations_per_account": reserve,
                                  "stress_account_fraction": 0.12, "lookalike_account_fraction": 0.24,
                                  "ordinary_context_change_probability": 0.035,
                                  "ordinary_travel_probability": 0.025,
                                  "ordinary_busy_period_probability": 0.30,
                                  "source_training_amount_band": ["p10", "p40"],
                                  "age_pair_preferences": {"Student_max": 30, "Retired_min": 55, "Doctor_min": 25, "Engineer_min": 22},
                                  "provenance": "Generator design choices, not FRD/TRD thresholds or empirical crime prevalence."},
        "limitations": ["Simulated stress labels evaluate recipe detection, not real-world fraud accuracy.",
                        "Same-parent account clones remain dependent within each split; source parents are disjoint across splits.",
                        "Original demographics are inconsistent across events; a generated stable pair is an explicit simulation.",
                        "Balance is a source-conditioned snapshot, not a reconstructed ledger."],
    }
    _write_json(out / "manifest.json", manifest)
    if _sha256(source) != source_hash:
        raise RuntimeError("Source changed during generation")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--split", choices=SPLITS, required=True)
    parser.add_argument("--rows", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--fit-profile", type=Path)
    parser.add_argument("--reference-date", type=date.fromisoformat, default=date(2026, 10, 15))
    args = parser.parse_args()
    print(json.dumps(generate(**vars(args)), sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
