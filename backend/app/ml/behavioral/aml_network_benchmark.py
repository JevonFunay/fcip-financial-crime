"""Offline graph-only anomaly benchmark on IBM's published AMLSim v2.1 output.

Sender/receiver edges are supplied by the independent simulator, never inferred
from the FCIP source. SAR/pattern fields are evaluation-only simulator labels.
The original graph has one weak component, so the default protocol uses
label-blind disjoint account groups plus a chronological window boundary and
reports discarded edges and incomplete patterns explicitly. No app DB is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from sklearn.metrics import average_precision_score, roc_auc_score

from .model import fit_model, predict_model

REPOSITORY_COMMIT = "7338a4bcb1af9bcfea2201ad7daccfe2a4d569ca"
ARCHIVE_URL = "https://www.dropbox.com/sh/l3grpumqfgbxqak/AAAj0fIjtJI6n9fdIBy2V7Lia/banks/v2.1?dl=1"
ARCHIVE_SHA256 = "e36015be8c728dc0acdc437aa7ab494fdc24d3289c0090673b07de31ac12a5fa"
DOCUMENTATION_URL = "https://github.com/IBM/AMLSim/wiki/Download-Example-Data-Set"
DATA = "data/bank_mixed"
TRANSACTIONS = f"{DATA}/transactions.csv.gz"
ACCOUNTS = f"{DATA}/accounts.csv.gz"
PATTERNS = f"{DATA}/alert_transactions.csv.gz"
MEMBERS = (
    "README.md", "paramFiles/bank_mixed/conf.json", "paramFiles/bank_mixed/schema.json",
    "paramFiles/bank_mixed/alertPatterns.csv", TRANSACTIONS, ACCOUNTS, PATTERNS,
    f"{DATA}/alert_accounts.csv.gz",
)
FEATURES = [
    "IN_COUNT", "OUT_COUNT", "IN_DISTINCT_PEERS", "OUT_DISTINCT_PEERS", "DISTINCT_PEERS",
    "IN_REPEAT_SHARE", "OUT_REPEAT_SHARE", "DIRECTION_COUNT_BALANCE", "FAN_IN_RATIO", "FAN_OUT_RATIO",
    "RECIPROCAL_PEERS", "RECIPROCAL_PEER_SHARE", "DIRECTED_TRIANGLES", "SELF_TRANSACTION_SHARE",
    "MEAN_OUT_NEIGHBOR_OUT_DEGREE", "MEAN_IN_NEIGHBOR_IN_DEGREE", "ACTIVE_DAYS", "EVENTS_PER_ACTIVE_DAY",
]
CAVEAT = (
    "Independent published synthetic AMLSim data, not real financial crime. is_sar and alert_type are "
    "simulator scenario indicators used only for evaluation. Account/time partitioning removes cross-group "
    "and out-of-period edges and can fragment patterns. This research model is not registered or approved."
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_hashes(expected_files: dict[str, str]) -> None:
    """Verify the original expectations, never re-baseline mutated inputs."""
    for name, expected in expected_files.items():
        path = Path(name)
        if not path.is_file() or sha256(path) != expected:
            raise ValueError(f"Frozen benchmark input, estimator or protocol changed: {name}")


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write("\n")


def download_source(raw: Path) -> dict:
    """Fetch the published archive; pin its bytes and extract only fixed names.

    Label-derived features shipped in the archive are deliberately not extracted
    or used. The repo Apache licence is preserved separately from the external
    archive, whose README supplies no separate data licence.
    """
    raw.mkdir(parents=True, exist_ok=True)
    archive = raw / "v2.1.zip"
    if not archive.exists():
        with urllib.request.urlopen(ARCHIVE_URL, timeout=60) as response, archive.open("xb") as handle:
            for block in iter(lambda: response.read(1024 * 1024), b""):
                handle.write(block)
    if sha256(archive) != ARCHIVE_SHA256:
        raise ValueError("Published archive bytes differ from the pinned benchmark source")
    with zipfile.ZipFile(archive) as zipped:
        for name in MEMBERS:
            destination = raw / name
            payload = zipped.read(name)
            if destination.exists():
                if destination.read_bytes() != payload:
                    raise ValueError(f"Refusing to overwrite changed source {name}")
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(payload)
    for name in ("LICENSE", "README.md"):
        path = raw / f"repository_{name}"
        if not path.exists():
            url = f"https://raw.githubusercontent.com/IBM/AMLSim/{REPOSITORY_COMMIT}/{name}"
            path.write_bytes(urllib.request.urlopen(url, timeout=30).read())
    provenance = {
        "source": "IBM AMLSim published bank_mixed v2.1", "documentation_url": DOCUMENTATION_URL,
        "download_url": ARCHIVE_URL, "archive_sha256": ARCHIVE_SHA256, "archive_bytes": archive.stat().st_size,
        "reference_repository": "https://github.com/IBM/AMLSim", "reference_repository_commit": REPOSITORY_COMMIT,
        "generation_commit": None,
        "license": {"reference_repository": "Apache-2.0", "external_archive_data": "Not separately stated in archive README or linked wiki; do not infer a separate data licence from the repository licence."},
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "excluded_provided_features": "The features/ directory includes SAR-dependent GPML features and is never loaded.",
        "files": {name: sha256(raw / name) for name in (*MEMBERS, "v2.1.zip", "repository_LICENSE", "repository_README.md")},
    }
    path = raw / "provenance.json"
    if path.exists():
        prior = json.loads(path.read_text())
        if prior["files"] != provenance["files"]:
            raise ValueError("Pinned source provenance differs")
        return prior
    write_json(path, provenance)
    return provenance


def load_transactions(raw: Path) -> pd.DataFrame:
    # No amounts, SAR flags, alert IDs or labelled account attributes are materialized.
    frame = pd.read_csv(raw / TRANSACTIONS, usecols=["tran_id", "orig_acct", "bene_acct", "tran_timestamp"],
                        dtype={"tran_id": "string", "orig_acct": "string", "bene_acct": "string"})
    frame = frame.rename(columns={"tran_id": "transaction_id", "orig_acct": "sender", "bene_acct": "receiver",
                                  "tran_timestamp": "timestamp"})
    if frame[["transaction_id", "sender", "receiver", "timestamp"]].isna().any().any():
        raise ValueError("Missing source network observation")
    if frame.transaction_id.duplicated().any():
        raise ValueError("Source transaction identifiers must be unique")
    frame["timestamp"] = pd.to_datetime(frame.timestamp, utc=True, errors="raise")
    return frame


def graph_partition(frame: pd.DataFrame, *, seed: int, account_fraction: float,
                    time_fraction: float, window_days: int) -> tuple[dict[str, pd.DataFrame], dict]:
    if not 0 < account_fraction < 1 or not 0 < time_fraction < 1 or window_days < 1:
        raise ValueError("Invalid declared partition parameters")
    accounts = sorted(set(frame.sender) | set(frame.receiver))
    index = pd.Index(accounts)
    source, destination = index.get_indexer(frame.sender), index.get_indexer(frame.receiver)
    matrix = coo_matrix((np.ones(len(frame), dtype=bool), (source, destination)), shape=(len(index), len(index))).tocsr()
    components, membership = connected_components(matrix, directed=False)
    sizes = pd.Series(membership).value_counts().sort_values(ascending=False)
    assignment = {account: int.from_bytes(hashlib.sha256(f"{seed}:{account}".encode()).digest()[:8], "big") / 2**64 < account_fraction
                  for account in accounts}
    start = frame.timestamp.min().floor("D")
    windows = int((frame.timestamp.max().floor("D") - start).days // window_days) + 1
    if windows < 2:
        raise ValueError("At least two chronological observation windows are required")
    training_windows = max(1, min(windows - 1, math.floor(windows * time_fraction)))
    cutoff = start + pd.Timedelta(days=training_windows * window_days)
    sender_train = frame.sender.map(assignment).to_numpy(dtype=bool)
    receiver_train = frame.receiver.map(assignment).to_numpy(dtype=bool)
    before = frame.timestamp.lt(cutoff).to_numpy()
    train_mask = sender_train & receiver_train & before
    holdout_mask = ~sender_train & ~receiver_train & ~before
    slices = {"train": frame.loc[train_mask].copy(), "holdout": frame.loc[holdout_mask].copy()}
    if any(part.empty for part in slices.values()):
        raise ValueError("Declared account/time partition produced an empty split")
    both_train, both_holdout = sender_train & receiver_train, ~sender_train & ~receiver_train
    report = {
        "policy": "Label-blind SHA256 account groups plus non-overlapping chronological complete-window boundary; induced subgraphs only.",
        "seed": seed, "account_train_fraction": account_fraction, "time_train_fraction": time_fraction,
        "base_date": start.isoformat(), "cutoff": cutoff.isoformat(), "window_days": window_days,
        "windows": windows, "training_windows": training_windows, "weak_components": int(components),
        "largest_component_accounts": int(sizes.iloc[0]),
        "component_disjoint_possible": bool(components >= 2),
        "component_policy": "The published graph has one giant component; account grouping is a disclosed induced-subgraph alternative, not a component-disjoint claim.",
        "source_transactions": len(frame), "source_accounts": len(accounts),
        "assigned_train_accounts": sum(assignment.values()), "assigned_holdout_accounts": len(assignment) - sum(assignment.values()),
        "train_transactions": int(train_mask.sum()), "holdout_transactions": int(holdout_mask.sum()),
        "excluded_cross_group_transactions": int((sender_train != receiver_train).sum()),
        "excluded_train_accounts_after_cutoff": int((both_train & ~before).sum()),
        "excluded_holdout_accounts_before_cutoff": int((both_holdout & before).sum()),
        "excluded_total": int((~train_mask & ~holdout_mask).sum()),
    }
    return slices, report


def build_graph_features(frame: pd.DataFrame, *, base_date: str | pd.Timestamp, window_days: int = 30) -> pd.DataFrame:
    """One account-window row, known only at that non-overlapping window's end.

    Every graph is built solely from that window's supplied directed edges.
    Raw identifiers order rows but are never among the numeric model features.
    No amount/currency threshold or simulator target is a feature.
    """
    work = frame[["transaction_id", "sender", "receiver", "timestamp"]].copy()
    base = pd.Timestamp(base_date)
    work["window"] = ((work.timestamp - base).dt.total_seconds() // (window_days * 86400)).astype(int)
    result = []
    for window, observations in work.groupby("window", sort=True):
        nodes = sorted(set(observations.sender) | set(observations.receiver))
        incoming = {node: set() for node in nodes}
        outgoing = {node: set() for node in nodes}
        edges = observations[["sender", "receiver"]].drop_duplicates()
        for sender, receiver in edges.itertuples(index=False, name=None):
            if sender != receiver:
                outgoing[sender].add(receiver)
                incoming[receiver].add(sender)
        sent = observations.groupby("sender").size().to_dict()
        received = observations.groupby("receiver").size().to_dict()
        self_count = observations.loc[observations.sender.eq(observations.receiver)].groupby("sender").size().to_dict()
        active = pd.concat([observations[["sender", "timestamp"]].rename(columns={"sender": "account"}),
                            observations[["receiver", "timestamp"]].rename(columns={"receiver": "account"})])
        active_days = active.assign(day=active.timestamp.dt.floor("D")).groupby("account").day.nunique().to_dict()
        for node in nodes:
            ins, outs = incoming[node], outgoing[node]
            in_count, out_count = received.get(node, 0), sent.get(node, 0)
            total = in_count + out_count
            reciprocal = len(ins & outs)
            triangles = sum(len(outgoing[other] & ins) for other in outs)
            result.append({
                "account": node, "window": int(window), "as_of": base + pd.Timedelta(days=(int(window) + 1) * window_days) - pd.Timedelta(seconds=1),
                "IN_COUNT": in_count, "OUT_COUNT": out_count,
                "IN_DISTINCT_PEERS": len(ins), "OUT_DISTINCT_PEERS": len(outs), "DISTINCT_PEERS": len(ins | outs),
                "IN_REPEAT_SHARE": 1 - len(ins) / in_count if in_count else np.nan,
                "OUT_REPEAT_SHARE": 1 - len(outs) / out_count if out_count else np.nan,
                "DIRECTION_COUNT_BALANCE": min(in_count, out_count) / max(in_count, out_count),
                "FAN_IN_RATIO": len(ins) / (1 + len(outs)), "FAN_OUT_RATIO": len(outs) / (1 + len(ins)),
                "RECIPROCAL_PEERS": reciprocal, "RECIPROCAL_PEER_SHARE": reciprocal / len(ins | outs) if ins | outs else 0.0,
                "DIRECTED_TRIANGLES": triangles,
                "SELF_TRANSACTION_SHARE": self_count.get(node, 0) / max(in_count, out_count),
                "MEAN_OUT_NEIGHBOR_OUT_DEGREE": float(np.mean([len(outgoing[other]) for other in outs])) if outs else np.nan,
                "MEAN_IN_NEIGHBOR_IN_DEGREE": float(np.mean([len(incoming[other]) for other in ins])) if ins else np.nan,
                "ACTIVE_DAYS": active_days[node], "EVENTS_PER_ACTIVE_DAY": total / active_days[node],
            })
    return pd.DataFrame(result, columns=["account", "window", "as_of", *FEATURES]).sort_values(["window", "account"]).reset_index(drop=True)


def _metrics(labels, scores, threshold):
    labels = np.asarray(labels, dtype=bool)
    scores = np.asarray(scores, dtype=float)
    flags = scores >= threshold
    tp, fp = int((flags & labels).sum()), int((flags & ~labels).sum())
    fn, tn = int((~flags & labels).sum()), int((~flags & ~labels).sum())
    return {"units": len(labels), "positive_units": int(labels.sum()), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": tp / (tp + fp) if tp + fp else None, "recall": tp / (tp + fn) if tp + fn else None,
            "flag_rate": float(flags.mean()) if len(flags) else None,
            "average_precision": float(average_precision_score(labels, scores)) if len(np.unique(labels)) == 2 else None,
            "roc_auc": float(roc_auc_score(labels, scores)) if len(np.unique(labels)) == 2 else None}


def _bool(series: pd.Series) -> pd.Series:
    values = series.astype(str).str.lower()
    if not values.isin(["true", "false", "1", "0"]).all():
        raise ValueError("SAR values must be explicit simulator booleans")
    return values.isin(["true", "1"])


def evaluate(raw: Path, observations: pd.DataFrame, features: pd.DataFrame, scores: np.ndarray,
             thresholds: dict, *, base_date: str, window_days: int,
             training_transaction_ids: set[str] | None = None) -> dict:
    # This is called only after model/thresholds have been written and frozen.
    truth = pd.read_csv(raw / TRANSACTIONS, usecols=["tran_id", "is_sar", "alert_id"], dtype={"tran_id": "string"})
    truth["label"] = _bool(truth.is_sar)
    patterns = pd.read_csv(raw / PATTERNS, usecols=["tran_id", "alert_id", "alert_type", "is_sar"], dtype={"tran_id": "string"})
    patterns = patterns.loc[_bool(patterns.is_sar)]
    aligned = observations.merge(truth[["tran_id", "label", "alert_id"]], left_on="transaction_id", right_on="tran_id", validate="one_to_one")
    aligned["window"] = ((aligned.timestamp - pd.Timestamp(base_date)).dt.total_seconds() // (window_days * 86400)).astype(int)
    account_labels = pd.concat([aligned[["sender", "window", "label"]].rename(columns={"sender": "account"}),
                                aligned[["receiver", "window", "label"]].rename(columns={"receiver": "account"})])
    account_labels = account_labels.groupby(["account", "window"], as_index=False).label.max()
    scoring = features[["account", "window"]].assign(score=scores).merge(account_labels, on=["account", "window"], validate="one_to_one")
    sender = scoring[["account", "window", "score"]].rename(columns={"account": "sender", "score": "sender_score"})
    receiver = scoring[["account", "window", "score"]].rename(columns={"account": "receiver", "score": "receiver_score"})
    aligned = aligned.merge(sender, on=["sender", "window"], validate="many_to_one").merge(receiver, on=["receiver", "window"], validate="many_to_one")
    aligned["score"] = aligned[["sender_score", "receiver_score"]].max(axis=1)
    training_sar = int(truth.loc[truth.tran_id.isin(training_transaction_ids or set()), "label"].sum())
    source_sar, holdout_sar = int(truth.label.sum()), int(aligned.label.sum())
    result = {"label_scope": "Published simulator SAR/scenario indicators, not independently verified crime",
              "published_SAR_transactions": source_sar, "train_SAR_transactions_evaluation_only": training_sar,
              "holdout_SAR_transactions": holdout_sar,
              "protocol_excluded_SAR_transactions": source_sar - training_sar - holdout_sar,
              "thresholds": {}}
    heldout_ids = set(aligned.transaction_id)
    all_patterns = patterns.groupby(["alert_type", "alert_id"]).tran_id.agg(set)
    for name, threshold in thresholds.items():
        pattern_results = {}
        flags = aligned.set_index("transaction_id").score.ge(threshold)
        for pattern_type, group in all_patterns.groupby(level=0):
            total, complete, detected, complete_detected = 0, 0, 0, 0
            for identifiers in group:
                observed_ids = identifiers & heldout_ids
                if not observed_ids:
                    continue
                total += 1
                hit = bool(flags.reindex(list(observed_ids)).any())
                detected += hit
                if identifiers <= heldout_ids:
                    complete += 1
                    complete_detected += hit
            pattern_results[pattern_type] = {
                "published_patterns": len(group), "observed_holdout_fragments": total, "detected_fragments": detected,
                "fragment_recall": detected / total if total else None,
                "complete_holdout_patterns": complete, "detected_complete_patterns": complete_detected,
                "complete_pattern_recall": complete_detected / complete if complete else None,
            }
        result["thresholds"][name] = {
            "threshold": threshold,
            "account_window": _metrics(scoring.label, scoring.score, threshold),
            "transaction_endpoint_support": _metrics(aligned.label, aligned.score, threshold),
            "patterns": pattern_results,
        }
    return result


def run(raw: Path, out: Path, *, seed: int = 20261016, estimators: int = 100, max_samples: int = 256,
        window_days: int = 30, train_account_fraction: float = .70, train_time_fraction: float = .60) -> dict:
    if out.exists():
        raise FileExistsError("Choose a new output directory; benchmark evidence is immutable")
    provenance_path = raw / "provenance.json"
    # Capture expectations before loading or fitting. Rehashing the inputs only
    # after fit would silently bless a file changed during model computation.
    provenance_hash = sha256(provenance_path)
    provenance = json.loads(provenance_path.read_text())
    expected_files = {str((raw / name).resolve()): expected for name, expected in provenance["files"].items()}
    protected_paths = [provenance_path, Path(__file__), Path(__file__).with_name("model.py")]
    expected_files.update({str(path.resolve()): sha256(path) for path in protected_paths})
    expected_files[str(provenance_path.resolve())] = provenance_hash
    verify_hashes(expected_files)
    transactions = load_transactions(raw)
    # Account data is used only to validate supplied currency units, never labels/IDs as predictors.
    currencies = pd.read_csv(raw / ACCOUNTS, usecols=["acct_id", "acct_rptng_crncy"], dtype="string")
    if set(currencies.acct_rptng_crncy) != {"USD"}:
        raise ValueError("This pinned benchmark expects explicitly recorded USD accounts; no FX inference")
    splits, partition = graph_partition(transactions, seed=seed, account_fraction=train_account_fraction,
                                        time_fraction=train_time_fraction, window_days=window_days)
    out.mkdir(parents=True)
    protocol = {"state": "DRAFT", "fit_uses_targets": False, "threshold_selection_uses_targets": False,
                "thresholds": ["IsolationForest auto0.5", "training score quantile0.99; review-load reference, not fraud prevalence"],
                "seed": seed, "estimators": estimators, "max_samples": max_samples, "features": FEATURES,
                "software": {"numpy": np.__version__, "pandas": pd.__version__, "sklearn": __import__("sklearn").__version__},
                "unit": f"one account and non-overlapping {window_days}-day directed-graph observation window, as of its end",
                "partition": partition, "source": provenance,
                "excluded_predictors": ["amounts", "account/transaction IDs", "is_sar", "alert_id", "alert_type", "prior_sar_count", "provided GPML SAR-derived features"],
                "no_database_registration_or_promotion": True, "caveat": CAVEAT}
    write_json(out / "protocol.json", protocol)
    expected_files[str((out / "protocol.json").resolve())] = sha256(out / "protocol.json")
    train = build_graph_features(splits["train"], base_date=partition["base_date"], window_days=window_days)
    holdout = build_graph_features(splits["holdout"], base_date=partition["base_date"], window_days=window_days)
    train_accounts, holdout_accounts = set(train.account), set(holdout.account)
    if train_accounts & holdout_accounts or train.as_of.max() >= holdout.as_of.min():
        raise ValueError("Account/time separation failed")
    text, train_scores = fit_model(train, FEATURES, seed=seed, estimators=estimators, max_samples=max_samples)
    thresholds = {"auto": .5, "train_q99": float(np.quantile(train_scores, .99))}
    (out / "model.txt").write_text(text, encoding="utf-8")
    expected_files[str((out / "model.txt").resolve())] = sha256(out / "model.txt")
    card = {"state": "DRAFT", "algorithm": "Unsupervised IsolationForest; graph-only numeric inputs",
            "fit_uses_targets": False, "thresholds": thresholds, "features": FEATURES,
            "train_account_windows": len(train), "holdout_account_windows": len(holdout),
            "train_accounts": len(train_accounts), "holdout_accounts": len(holdout_accounts),
            "currency": "USD", "amount_used_as_predictor": False, "caveat": CAVEAT}
    write_json(out / "model_card.json", card)
    expected_files[str((out / "model_card.json").resolve())] = sha256(out / "model_card.json")
    verify_hashes(expected_files)
    frozen = {"frozen_at": datetime.now(timezone.utc).isoformat(), "files": dict(expected_files)}
    write_json(out / "frozen.json", frozen)
    frozen_hash = sha256(out / "frozen.json")
    holdout_scores = predict_model(text, holdout)
    # No source target was loaded into a DataFrame before the above freeze.
    assessment = evaluate(raw, splits["holdout"], holdout, holdout_scores, thresholds,
                          base_date=partition["base_date"], window_days=window_days,
                          training_transaction_ids=set(splits["train"].transaction_id))
    verify_hashes(frozen["files"])
    verify_hashes({str((out / "frozen.json").resolve()): frozen_hash})
    train.assign(score=train_scores).to_parquet(out / "train_scores.parquet", index=False)
    holdout.assign(score=holdout_scores).to_parquet(out / "holdout_scores.parquet", index=False)
    report = {"completed_at": datetime.now(timezone.utc).isoformat(), "state": "DRAFT", "source": provenance,
              "partition": partition, "model": card, "evaluation": assessment, "frozen": frozen,
              "frozen_manifest_sha256": frozen_hash,
              "fit_uses_targets": False, "threshold_selection_uses_targets": False,
              "no_database_registration_or_promotion": True,
              "limitations": [CAVEAT, "The external archive has no separately stated data licence; only the reference repository licence was verified.",
                              "Published data was tuned for GPML and uses stylized SAR amounts. This model excludes all amount features.",
                              "Graph observation windows are batch endpoints; flags do not establish real-time interception or early warning.",
                              "Account-disjoint induced subgraphs discard edges and fragment patterns; fragment recall is not whole-pattern recall.",
                              "Labels describe the independent simulator's declared SAR interventions; they do not establish real-world detection accuracy."]}
    write_json(out / "report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--seed", type=int, default=20261016)
    parser.add_argument("--estimators", type=int, default=100)
    args = parser.parse_args()
    if args.download:
        download_source(args.raw)
    report = run(args.raw, args.out, seed=args.seed, estimators=args.estimators)
    print(json.dumps({"report": str(args.out / "report.json"), "partition": report["partition"],
                      "model": report["model"], "evaluation": report["evaluation"]}, indent=2))


if __name__ == "__main__":
    main()
