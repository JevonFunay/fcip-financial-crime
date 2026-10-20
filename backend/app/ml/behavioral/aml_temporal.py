"""Full-edge chronological AMLSim research; simulator targets are evaluation only.

This is known-account future evaluation on a previously inspected public
synthetic source, not a new independent crime holdout. Historical modules and
benchmarks remain immutable. All accounts and directed edges are retained.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import aml_network_benchmark as old
from .model import fit_model, predict_model

VERSION = "aml_temporal_full_edges_v1"
CANDIDATES = (("graph256", "graph", 256), ("graph2048", "graph", 2048), ("peer2048", "peer", 2048))
EXTRA = ["NEW_IN_PEER_SHARE", "NEW_OUT_PEER_SHARE", "IN_OUT_DAY_OVERLAP", "PRIOR_ACTIVITY_LOG_RATIO"]
PEER_RAW = ["DIRECTION_COUNT_BALANCE", "FAN_IN_RATIO", "FAN_OUT_RATIO", "RECIPROCAL_PEER_SHARE", "SELF_TRANSACTION_SHARE"]
PEER_BASE = [*old.FEATURES, *EXTRA]
PEER_FEATURES = [f"PEER_DEVIATION_{name}" for name in PEER_BASE] + PEER_RAW
CAVEATS = [
    "Previously inspected published AMLSim simulation; SAR indicators are simulator labels, not verified financial crime.",
    "Full-edge chronological evaluation primarily measures future behavior of known accounts, not unseen-account generalization.",
    "The external archive has no separately stated data licence; the reference repository is Apache-2.0, which does not establish archive licensing.",
    "Account-window scores are available only at window end; endpoint transaction support is not real-time detection.",
    "Complete patterns and observed boundary fragments have separate denominators; a detected fragment is not complete-pattern recall.",
    "Three models fit without labels; validation-based threshold/model selection is label-informed and explicitly not unsupervised evaluation.",
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def temporal_partition(frame: pd.DataFrame, *, window_days: int, train_windows: int,
                       validation_windows: int) -> tuple[dict[str, pd.DataFrame], dict]:
    if min(window_days, train_windows, validation_windows) < 1:
        raise ValueError("Positive declared chronology parameters required")
    start = frame.timestamp.min().floor("D")
    work = frame.copy()
    work["window"] = ((work.timestamp - start).dt.total_seconds() // (window_days * 86400)).astype(int)
    windows = int(work.window.max()) + 1
    boundary = train_windows + validation_windows
    if boundary >= windows:
        raise ValueError("Chronology requires a nonempty final period")
    masks = {"train": work.window.lt(train_windows),
             "validation": work.window.ge(train_windows) & work.window.lt(boundary),
             "final": work.window.ge(boundary)}
    parts = {name: work.loc[mask].copy() for name, mask in masks.items()}
    if any(part.empty for part in parts.values()):
        raise ValueError("Declared chronology has an empty split")
    train_accounts = set(parts["train"].sender) | set(parts["train"].receiver)
    metadata = {"policy": "Full supplied directed network; label-blind nonoverlapping time windows; no account-edge partitioning",
                "base_date": start.isoformat(), "window_days": window_days, "windows": windows,
                "train_windows": list(range(train_windows)), "validation_windows": list(range(train_windows, boundary)),
                "final_windows": list(range(boundary, windows)), "source_transactions": len(frame),
                "excluded_transactions": 0, "excluded_cross_group_edges": 0, "excluded_warmup_transactions": 0,
                "partial_last_window": bool(work.timestamp.max() < start + pd.Timedelta(days=windows * window_days) - pd.Timedelta(days=1)),
                "unit": "one account per non-overlapping observation window; inactive account windows absent",
                "evaluation_scope": "known-account future chronology; new account diagnostics separately reported", "splits": {}}
    for name, part in parts.items():
        accounts = set(part.sender) | set(part.receiver)
        metadata["splits"][name] = {"transactions": len(part), "accounts": len(accounts),
                                         "known_train_accounts": len(accounts & train_accounts),
                                         "unseen_train_accounts": len(accounts - train_accounts),
                                         "first_timestamp": part.timestamp.min().isoformat(),
                                         "last_timestamp": part.timestamp.max().isoformat(),
                                         "period_start": (start + pd.Timedelta(days=int(part.window.min()) * window_days)).isoformat(),
                                         "period_end_exclusive": (start + pd.Timedelta(days=(int(part.window.max()) + 1) * window_days)).isoformat()}
    if sum(len(part) for part in parts.values()) != len(frame):
        raise ValueError("Full-edge reconciliation failed")
    return parts, metadata


def build_features(frame: pd.DataFrame, *, base_date: str, window_days: int) -> pd.DataFrame:
    """Current complete-window topology plus strictly earlier-window context."""
    features = old.build_graph_features(frame, base_date=base_date, window_days=window_days)
    work = frame.copy()
    work["window"] = ((work.timestamp - pd.Timestamp(base_date)).dt.total_seconds() // (window_days * 86400)).astype(int)
    incoming, outgoing, activity_sum, history_count = {}, {}, {}, {}
    values = []
    for window, observations in work.groupby("window", sort=True):
        current = features.loc[features.window.eq(window)]
        in_sets = observations.groupby("receiver").sender.agg(set).to_dict()
        out_sets = observations.groupby("sender").receiver.agg(set).to_dict()
        in_days = observations.assign(day=observations.timestamp.dt.floor("D")).groupby("receiver").day.agg(set).to_dict()
        out_days = observations.assign(day=observations.timestamp.dt.floor("D")).groupby("sender").day.agg(set).to_dict()
        for row in current.itertuples(index=False):
            account = row.account
            ins, outs = in_sets.get(account, set()), out_sets.get(account, set())
            before_in, before_out = incoming.get(account, set()), outgoing.get(account, set())
            days_in, days_out = in_days.get(account, set()), out_days.get(account, set())
            total = row.IN_COUNT + row.OUT_COUNT
            previous = activity_sum.get(account, 0) / history_count[account] if account in history_count else None
            values.append({"account": account, "window": int(window),
                           "NEW_IN_PEER_SHARE": len(ins - before_in) / len(ins) if ins else np.nan,
                           "NEW_OUT_PEER_SHARE": len(outs - before_out) / len(outs) if outs else np.nan,
                           "IN_OUT_DAY_OVERLAP": len(days_in & days_out) / len(days_in | days_out),
                           "PRIOR_ACTIVITY_LOG_RATIO": float(np.log1p(total) - np.log1p(previous)) if previous is not None else np.nan})
        # Update only after every account in this window has been described.
        for row in current.itertuples(index=False):
            account = row.account
            incoming.setdefault(account, set()).update(in_sets.get(account, set()))
            outgoing.setdefault(account, set()).update(out_sets.get(account, set()))
            activity_sum[account] = activity_sum.get(account, 0) + row.IN_COUNT + row.OUT_COUNT
            history_count[account] = history_count.get(account, 0) + 1
    return features.merge(pd.DataFrame(values), on=["account", "window"], validate="one_to_one")


def fit_peer_profile(train: pd.DataFrame) -> dict:
    activity = train.IN_COUNT.to_numpy() + train.OUT_COUNT.to_numpy()
    boundaries = sorted(set(float(v) for v in np.quantile(activity, [.25, .5, .75])))
    groups = np.searchsorted(boundaries, activity, side="right")
    profile = {"policy": "TRAIN-only activity quartiles; feature median and IQR by activity group; no labels or raw IDs",
               "boundaries": boundaries, "features": PEER_BASE, "groups": {}}
    for group in range(len(boundaries) + 1):
        subset = train.loc[groups == group, PEER_BASE]
        values = {}
        for name in PEER_BASE:
            array = subset[name].to_numpy(dtype=float)
            array = array[np.isfinite(array)]
            median = float(np.median(array)) if len(array) else 0.
            iqr = float(np.quantile(array, .75) - np.quantile(array, .25)) if len(array) else 0.
            # A zero spread is common for graph motifs; a fixed unit prevents division by zero.
            values[name] = {"median": median, "scale": max(iqr, 1.)}
        profile["groups"][str(group)] = values
    return profile


def peer_view(frame: pd.DataFrame, profile: dict) -> pd.DataFrame:
    groups = np.searchsorted(profile["boundaries"], frame.IN_COUNT.to_numpy() + frame.OUT_COUNT.to_numpy(), side="right")
    result = frame[["account", "window", "as_of", *PEER_RAW]].copy()
    for name in PEER_BASE:
        medians = np.array([profile["groups"][str(int(g))][name]["median"] for g in groups])
        scales = np.array([profile["groups"][str(int(g))][name]["scale"] for g in groups])
        result[f"PEER_DEVIATION_{name}"] = (frame[name].to_numpy(dtype=float) - medians) / scales
    return result


def _read_selected(raw: Path, member: str, ids: set[str], columns: list[str]) -> pd.DataFrame:
    """Filter raw rows by neutral ID BEFORE parsing target/amount fields.

    Pinned AMLSim identifier prefixes are unquoted and have no embedded commas.
    No final target is parsed when requesting only validation identifiers. The
    source compressed bytes are still hashed in full for integrity.
    """
    selected = []
    with gzip.open(raw / member, "rt", encoding="utf-8", newline="") as handle:
        header = handle.readline()
        names = next(csv.reader([header]))
        id_index = names.index("tran_id")
        for line in handle:
            prefix = line.split(",", id_index + 1)[:id_index + 1]
            if any('"' in part for part in prefix):
                raise ValueError("Pinned neutral identifier prefix unexpectedly quoted")
            if prefix[id_index] in ids:
                selected.append(line)
    return pd.read_csv(io.StringIO(header + "".join(selected)), usecols=columns, dtype={"tran_id": "string", "alert_id": "string"})


def read_labels(raw: Path, ids: set[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    truth = _read_selected(raw, old.TRANSACTIONS, ids, ["tran_id", "is_sar", "alert_id", "base_amt"])
    truth["label"] = old._bool(truth.is_sar)
    patterns = _read_selected(raw, old.PATTERNS, ids, ["tran_id", "is_sar", "alert_id", "alert_type"])
    patterns = patterns.loc[old._bool(patterns.is_sar)].copy()
    if set(truth.tran_id) != ids:
        raise ValueError("Every permitted observation must have one evaluation label")
    return truth, patterns


def account_truth(observations: pd.DataFrame, features: pd.DataFrame, truth: pd.DataFrame,
                  patterns: pd.DataFrame, *, low_cutoff: float) -> pd.DataFrame:
    aligned = observations.merge(truth, left_on="transaction_id", right_on="tran_id", validate="one_to_one")
    positive = aligned.loc[aligned.label]
    recipe = positive.merge(patterns[["tran_id", "alert_type"]].drop_duplicates(), on="tran_id", how="left", validate="one_to_many")
    rows = pd.concat([aligned[["sender", "window", "label", "base_amt"]].rename(columns={"sender": "account"}),
                      aligned[["receiver", "window", "label", "base_amt"]].rename(columns={"receiver": "account"})])
    rows["low_positive"] = rows.label & rows.base_amt.le(low_cutoff)
    rows["low_any"] = rows.base_amt.le(low_cutoff)
    aggregate = rows.groupby(["account", "window"], as_index=False).agg(label=("label", "max"), low_positive=("low_positive", "max"), low_any=("low_any", "max"))
    result = features[["account", "window"]].merge(aggregate, on=["account", "window"], validate="one_to_one")
    for name in sorted(patterns.alert_type.unique()):
        pairs = pd.concat([recipe.loc[recipe.alert_type.eq(name), ["sender", "window"]].rename(columns={"sender": "account"}),
                           recipe.loc[recipe.alert_type.eq(name), ["receiver", "window"]].rename(columns={"receiver": "account"})]).drop_duplicates()
        pairs[f"recipe:{name}"] = True
        result = result.merge(pairs, on=["account", "window"], how="left", validate="one_to_one")
        result[f"recipe:{name}"] = result[f"recipe:{name}"].eq(True)
    return result


def assess_units(labels: pd.DataFrame, scores: np.ndarray, threshold: float) -> dict:
    result = old._metrics(labels.label, scores, threshold)
    flags = np.asarray(scores) >= threshold
    result["low_amount_positive_tp"] = int((flags & labels.low_positive.to_numpy()).sum())
    result["low_amount_positive_units"] = int(labels.low_positive.sum())
    result["recipe_tp"] = {name.removeprefix("recipe:"): int((flags & labels[name].to_numpy()).sum()) for name in labels if name.startswith("recipe:")}
    result["recipe_positive_units"] = {name.removeprefix("recipe:"): int(labels[name].sum()) for name in labels if name.startswith("recipe:")}
    return result


def observed_pattern_hits(observations: pd.DataFrame, features: pd.DataFrame, patterns: pd.DataFrame,
                          scores: np.ndarray, threshold: float) -> dict:
    """Pattern/fragment support within this permitted split, never whole-source completeness."""
    score_rows = features[["account", "window"]].assign(score=scores)
    merged = observations.merge(score_rows.rename(columns={"account": "sender", "score": "sender_score"}), on=["sender", "window"], validate="many_to_one")
    merged = merged.merge(score_rows.rename(columns={"account": "receiver", "score": "receiver_score"}), on=["receiver", "window"], validate="many_to_one")
    merged["flag"] = merged[["sender_score", "receiver_score"]].max(axis=1).ge(threshold)
    tagged = patterns.merge(merged[["transaction_id", "flag"]], left_on="tran_id", right_on="transaction_id", validate="many_to_one")
    hits = tagged.groupby(["alert_type", "alert_id"], as_index=False).flag.max()
    return {"pattern_or_fragment_tp": {str(kind): int(group.flag.sum()) for kind, group in hits.groupby("alert_type")},
            "pattern_or_fragment_positive_counts": {str(kind): len(group) for kind, group in hits.groupby("alert_type")},
            "completeness_scope": "Within-split observed pattern/fragment coverage; exact whole-source completeness computed after final freeze only"}


def choose(validation: dict[str, dict], *, reference: str = "graph256__auto") -> dict:
    baseline = validation[reference]
    eligible, rejected = [], {}
    for name, values in validation.items():
        reasons = []
        if values["tp"] < baseline["tp"]:
            reasons.append("overall_true_positive_loss")
        if values["low_amount_positive_tp"] < baseline["low_amount_positive_tp"]:
            reasons.append("low_amount_true_positive_loss")
        if any(values["recipe_tp"].get(recipe, 0) < count for recipe, count in baseline["recipe_tp"].items()):
            reasons.append("recipe_true_positive_loss")
        if any(values.get("pattern_or_fragment_tp", {}).get(recipe, 0) < count
               for recipe, count in baseline.get("pattern_or_fragment_tp", {}).items()):
            reasons.append("observed_pattern_or_fragment_loss")
        if reasons:
            rejected[name] = reasons
        else:
            eligible.append(name)
    # Same precision favors reference, then fewer flags. No coverage-losing winner.
    rank = lambda name: (-(validation[name]["precision"] or 0.), 0 if name == reference else 1, validation[name]["fp"], name)
    winner = min(eligible, key=rank)
    return {"reference": reference, "selected": winner, "eligible": eligible, "rejected": rejected,
            "coverage_floor": 1., "guards": ["overall TP >= reference", "low-amount positive TP >= reference", "every observed recipe TP >= reference", "every observed pattern/fragment type detected count >= reference"],
            "selection_uses_validation_labels": True, "fit_uses_targets": False}


def pattern_evaluation(observations: pd.DataFrame, scores: pd.DataFrame, pattern_rows: pd.DataFrame,
                       all_pattern_rows: pd.DataFrame, threshold: float) -> dict:
    """Complete whole-split patterns vs boundary/window fragments, explicitly."""
    merged = observations.merge(scores.rename(columns={"account": "sender", "score": "sender_score"}), on=["sender", "window"], validate="many_to_one")
    merged = merged.merge(scores.rename(columns={"account": "receiver", "score": "receiver_score"}), on=["receiver", "window"], validate="many_to_one")
    flags = merged.set_index("transaction_id")[["sender_score", "receiver_score"]].max(axis=1).ge(threshold)
    heldout_ids = set(observations.transaction_id)
    whole = all_pattern_rows.groupby(["alert_type", "alert_id"]).tran_id.agg(set)
    result = {}
    for kind, groups in whole.groupby(level=0):
        observed = complete = detected = complete_detected = cross_window = 0
        for ids in groups:
            retained = ids & heldout_ids
            if not retained:
                continue
            observed += 1
            hit = bool(flags.reindex(list(retained)).any())
            detected += int(hit)
            if ids <= heldout_ids:
                complete += 1
                complete_detected += int(hit)
                cross_window += int(observations.loc[observations.transaction_id.isin(ids), "window"].nunique() > 1)
        result[kind] = {"published_patterns": len(groups), "observed_patterns_or_fragments": observed,
                        "complete_patterns": complete, "boundary_fragments": observed - complete,
                        "detected_complete_patterns": complete_detected,
                        "complete_pattern_recall": complete_detected / complete if complete else None,
                        "detected_observed_patterns_or_fragments": detected,
                        "observed_fragment_recall": detected / observed if observed else None,
                        "complete_patterns_spanning_multiple_feature_windows": cross_window}
    return result


def run(raw: Path, out: Path, *, seed: int = 20261020, estimators: int = 100,
        window_days: int = 30, train_windows: int = 12, validation_windows: int = 6) -> dict:
    if out.exists():
        raise FileExistsError("Choose a new output; temporal evidence is immutable")
    provenance_path = raw / "provenance.json"
    provenance = json.loads(provenance_path.read_text())
    expected = {str((raw / name).resolve()): checksum for name, checksum in provenance["files"].items()}
    code_paths = [provenance_path, Path(__file__), Path(old.__file__), Path(__file__).with_name("model.py")]
    expected.update({str(path.resolve()): old.sha256(path) for path in code_paths})
    old.verify_hashes(expected)
    observations = old.load_transactions(raw)
    currency = pd.read_csv(raw / old.ACCOUNTS, usecols=["acct_rptng_crncy"], dtype="string")
    if set(currency.acct_rptng_crncy) != {"USD"}:
        raise ValueError("Pinned source must explicitly record USD accounts")
    splits, partition = temporal_partition(observations, window_days=window_days, train_windows=train_windows, validation_windows=validation_windows)
    protocol = {"version": VERSION, "state": "DRAFT", "created_at": now(), "source": provenance, "partition": partition,
                "seed": seed, "estimators": estimators, "candidates": CANDIDATES,
                "thresholds": {"auto": .5, "train_q90": .9, "train_q95": .95, "train_q99": .99},
                "graph_features": old.FEATURES, "peer_features": PEER_FEATURES,
                "peer_profile": "TRAIN-only activity quartiles; label-free median/IQR normalization",
                "fit_uses_targets": False, "selection_uses_validation_labels": True,
                "selection": "Max precision with >=100% reference TP overall/low amount/every observed recipe and observed pattern-fragment type; tie reference; no final retuning",
                "low_amount": "TRAIN transaction-amount q25; evaluation only, never predictor",
                "target_access": "Neutral-ID raw-row filtering before parsing target fields; final targets only after frozen selection",
                "known_public_source": True, "no_database_registration_or_promotion": True,
                "excluded_predictors": ["raw IDs", "amount", "is_sar", "alert_id", "alert_type", "prior_sar_count", "provided GPML features"],
                "software": {"numpy": np.__version__, "pandas": pd.__version__, "sklearn": __import__("sklearn").__version__}, "limitations": CAVEATS}
    out.mkdir(parents=True)
    old.write_json(out / "protocol.json", protocol)
    expected[str((out / "protocol.json").resolve())] = old.sha256(out / "protocol.json")
    print("Building all-edge chronological graph features", flush=True)
    features = build_features(observations, base_date=partition["base_date"], window_days=window_days)
    feature_parts = {name: features.loc[features.window.isin(part.window.unique())].reset_index(drop=True) for name, part in splits.items()}
    train = feature_parts["train"]
    profile = fit_peer_profile(train)
    old.write_json(out / "peer_profile.json", profile)
    expected[str((out / "peer_profile.json").resolve())] = old.sha256(out / "peer_profile.json")
    train_amounts = _read_selected(raw, old.TRANSACTIONS, set(splits["train"].transaction_id), ["tran_id", "base_amt"])
    low_cutoff = float(train_amounts.base_amt.quantile(.25))
    models, all_scores, options = {}, {}, {}
    for name, view, max_samples in CANDIDATES:
        print(f"Fitting {name}, rows={len(train)}, no labels", flush=True)
        frame = peer_view(train, profile) if view == "peer" else train
        names = PEER_FEATURES if view == "peer" else old.FEATURES
        model_text, train_scores = fit_model(frame, names, seed=seed, estimators=estimators, max_samples=max_samples)
        models[name] = model_text
        thresholds = {"auto": .5, **{f"train_q{int(q * 100)}": float(np.quantile(train_scores, q)) for q in (.9, .95, .99)}}
        model_path = out / f"{name}.json"
        model_path.write_text(model_text)
        card = {"state": "DRAFT", "view": view, "max_samples": max_samples, "thresholds": thresholds,
                "fit_uses_targets": False, "features": names, "train_account_windows": len(train), "low_amount_cutoff_evaluation_only": low_cutoff}
        card_path = out / f"{name}_card.json"
        old.write_json(card_path, card)
        expected.update({str(path.resolve()): old.sha256(path) for path in [model_path, card_path]})
        all_scores[name] = {}
        for split, part in feature_parts.items():
            scoring_frame = peer_view(part, profile) if view == "peer" else part
            all_scores[name][split] = train_scores if split == "train" else predict_model(model_text, scoring_frame)
        for key, threshold in thresholds.items():
            options[f"{name}__{key}"] = {"model": name, "threshold": threshold}
    old.verify_hashes(expected)
    old.write_json(out / "fitted.json", {"fitted_at": now(), "files": dict(expected), "validation_targets_not_yet_parsed": True})
    expected[str((out / "fitted.json").resolve())] = old.sha256(out / "fitted.json")
    print("All candidates fitted; parsing VALIDATION targets only", flush=True)
    truth, patterns = read_labels(raw, set(splits["validation"].transaction_id))
    validation_labels = account_truth(splits["validation"], feature_parts["validation"], truth, patterns, low_cutoff=low_cutoff)
    validation = {}
    for name, option in options.items():
        scores = all_scores[option["model"]]["validation"]
        validation[name] = assess_units(validation_labels, scores, option["threshold"])
        validation[name].update(observed_pattern_hits(splits["validation"], feature_parts["validation"], patterns, scores, option["threshold"]))
    selection = choose(validation)
    selection["options"] = options
    old.write_json(out / "validation.json", validation)
    old.write_json(out / "selection.json", selection)
    for path in [out / "validation.json", out / "selection.json"]:
        expected[str(path.resolve())] = old.sha256(path)
    old.verify_hashes(expected)
    frozen = {"frozen_at": now(), "files": dict(expected), "final_targets_not_yet_parsed": True}
    old.write_json(out / "frozen.json", frozen)
    frozen_hash = old.sha256(out / "frozen.json")
    print(f"Frozen selection={selection['selected']}; parsing FINAL targets once", flush=True)
    final_started = now()
    final_truth, final_patterns = read_labels(raw, set(splits["final"].transaction_id))
    # All pattern rows are evaluation metadata only and are first permitted after final freeze.
    _, all_patterns = read_labels(raw, set(observations.transaction_id))
    final_labels = account_truth(splits["final"], feature_parts["final"], final_truth, final_patterns, low_cutoff=low_cutoff)
    train_accounts = set(train.account)
    final_results = {}
    for option_name in dict.fromkeys([selection["reference"], selection["selected"]]):
        option = options[option_name]
        scores = all_scores[option["model"]]["final"]
        metrics = assess_units(final_labels, scores, option["threshold"])
        endpoint_scores = feature_parts["final"][["account", "window"]].assign(score=scores)
        patterns_report = pattern_evaluation(splits["final"], endpoint_scores, final_patterns, all_patterns, option["threshold"])
        diagnostics = {}
        for label, mask in {"known_train_accounts": final_labels.account.isin(train_accounts).to_numpy(),
                            "unseen_train_accounts": ~final_labels.account.isin(train_accounts).to_numpy()}.items():
            diagnostics[label] = old._metrics(final_labels.loc[mask, "label"], scores[mask], option["threshold"])
        final_results[option_name] = {"account_window": metrics, "patterns": patterns_report, "account_scope_diagnostics": diagnostics}
    old.verify_hashes(frozen["files"])
    old.verify_hashes({str((out / "frozen.json").resolve()): frozen_hash})
    for name, scores_by_split in all_scores.items():
        for split, scores in scores_by_split.items():
            feature_parts[split][["account", "window", "as_of"]].assign(score=scores).to_parquet(out / f"{name}_{split}_scores.parquet", index=False)
    winner, reference = selection["selected"], selection["reference"]
    selected_metrics = final_results[winner]["account_window"]
    reference_metrics = final_results[reference]["account_window"]
    final_guards = {
        "overall_tp": selected_metrics["tp"] >= reference_metrics["tp"],
        "low_amount_tp": selected_metrics["low_amount_positive_tp"] >= reference_metrics["low_amount_positive_tp"],
        "per_recipe_tp": all(selected_metrics["recipe_tp"].get(key, 0) >= count for key, count in reference_metrics["recipe_tp"].items()),
        "complete_pattern_detection": all(final_results[winner]["patterns"].get(key, {}).get("detected_complete_patterns", 0) >= values["detected_complete_patterns"]
                                          for key, values in final_results[reference]["patterns"].items()),
    }
    final_guard = all(final_guards.values())
    report = {"version": VERSION, "completed_at": now(), "state": "DRAFT", "source": provenance, "partition": partition,
              "fit_uses_targets": False, "selection_uses_validation_labels": True, "no_database_registration_or_promotion": True,
              "low_amount_cutoff_evaluation_only": low_cutoff, "validation": validation, "selection": selection,
              "final_started_at": final_started, "frozen_at": frozen["frozen_at"], "frozen_manifest_sha256": frozen_hash,
              "final": final_results, "final_coverage_guards_passed": final_guard, "final_coverage_guards": final_guards,
              "final_retuned": False, "limitations": CAVEATS, "frozen": frozen}
    old.write_json(out / "report.json", report)
    print(json.dumps({"report": str(out / "report.json"), "selection": selection["selected"], "final": final_results}, indent=2), flush=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20261020)
    parser.add_argument("--estimators", type=int, default=100)
    args = parser.parse_args()
    run(args.raw, args.out, seed=args.seed, estimators=args.estimators)


if __name__ == "__main__":
    main()
