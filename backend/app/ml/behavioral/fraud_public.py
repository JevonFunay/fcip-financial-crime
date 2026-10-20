"""Independent, target-blind unsupervised ULB/OpenML 1597 research benchmark.

This adapter does not map opaque PCA components to FCIP banking fields. Supplied
Class labels are read only for held-out evaluation, never to filter or fit TRAIN.
Time supplies a relative chronology, not an absolute timestamp or a predictor.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from sklearn.cluster import MiniBatchKMeans
from sklearn.metrics import average_precision_score
from threadpoolctl import threadpool_limits

from .model import fit_model, predict_model

VERSION = "public_creditcard_unsupervised_v1"
SOURCE_API = "https://www.openml.org/api/v1/json/data/1597"
SOURCE_PAGE = "https://www.kaggle.com/datasets/mlg-ulb/creditcardfraud"
PC = [f"V{i}" for i in range(1, 29)]
BASE = PC + ["Amount"]
PEER = ["peer_distance", "peer_component_excess", "peer_amount_excess"]
REFERENCE = "raw_256__auto"
SPECS = (("raw_256", "raw", 256), ("raw_2048", "raw", 2048),
         ("peer_2048", "peer", 2048))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def verify_frozen(frozen: dict) -> None:
    for name, expected in frozen["files"].items():
        if _hash(Path(name)) != expected:
            raise ValueError(f"Frozen input changed: {name}")


def read_features(source: Path) -> pd.DataFrame:
    """Column projection prevents parsing Class during data/feature preparation."""
    schema = pq.read_schema(source)
    if set(schema.names) != set(BASE + ["Time", "Class"]):
        raise ValueError("OpenML 1597 schema differs from the versioned adapter")
    frame = pq.read_table(source, columns=["Time"] + BASE).to_pandas()
    values = frame.to_numpy(dtype=float)
    if not np.isfinite(values).all() or (frame.Amount < 0).any() or (frame.Time < 0).any():
        raise ValueError("Expected finite numeric components, nonnegative amount/relative time")
    return frame.astype(float).sort_values("Time", kind="stable").reset_index(drop=True)


def chronological_partitions(frame: pd.DataFrame) -> tuple[dict[str, pd.DataFrame], dict]:
    """60/20/20 row targets; equal relative timestamps never straddle a boundary."""
    if len(frame) < 30 or not frame.Time.is_monotonic_increasing:
        raise ValueError("At least thirty observations in relative chronological order required")
    times = frame.Time.to_numpy()
    lower = float(times[int(len(frame) * .6) - 1])
    upper = float(times[int(len(frame) * .8) - 1])
    if not lower < upper:
        raise ValueError("Not enough distinct relative times for chronological holdouts")
    frames = {"train": frame[frame.Time <= lower].reset_index(drop=True),
              "validation": frame[(frame.Time > lower) & (frame.Time <= upper)].reset_index(drop=True),
              "final": frame[frame.Time > upper].reset_index(drop=True)}
    if any(len(part) < 2 for part in frames.values()):
        raise ValueError("Chronological partition is empty")
    manifest = {"train_upper_inclusive": lower, "validation_upper_inclusive": upper,
                "clock": "elapsed seconds since first observation; absolute date/timezone unknown",
                "rule": "Time<=train_upper; train_upper<Time<=validation_upper; Time>validation_upper",
                "ties_kept_together": True, "target_used": False,
                "row_counts": {key: len(part) for key, part in frames.items()}}
    return frames, manifest


def read_labels(source: Path, split: str, partition: dict) -> np.ndarray:
    """Predicate projection parses held-out labels only; TRAIN is forbidden."""
    if split == "validation":
        filters = [("Time", ">", partition["train_upper_inclusive"]),
                   ("Time", "<=", partition["validation_upper_inclusive"])]
    elif split == "final":
        filters = [("Time", ">", partition["validation_upper_inclusive"])]
    else:
        raise ValueError("TRAIN labels are never available to this workflow")
    answers = pq.read_table(source, columns=["Time", "Class"], filters=filters).to_pandas()
    answers = answers.sort_values("Time", kind="stable")
    numeric = pd.to_numeric(answers.Class, errors="raise").to_numpy(dtype=float)
    if not np.isfinite(numeric).all() or not set(numeric).issubset({0., 1.}):
        raise ValueError("Supplied Class must contain only binary labels")
    if len(numeric) != partition["row_counts"][split]:
        raise ValueError("Held-out Class rows do not align with projected observations")
    return numeric.astype(int)


def fit_peers(train: pd.DataFrame, *, seed: int, clusters: int = 16) -> dict:
    """Population peers in opaque PCA space; no invented customer identities."""
    x = train[PC].to_numpy(dtype=float)
    center = np.median(x, axis=0)
    scale = np.quantile(x, .75, axis=0) - np.quantile(x, .25, axis=0)
    scale = np.where(scale > 1e-8, scale, 1.)
    z = (x - center) / scale
    k = min(clusters, max(2, len(train) // 25))
    with threadpool_limits(limits=4):
        km = MiniBatchKMeans(n_clusters=k, random_state=seed, n_init=5,
                            batch_size=2048, max_iter=100).fit(z)
        assigned = km.predict(z)
    amounts = np.log1p(train.Amount.to_numpy())
    distances = np.linalg.norm(z - km.cluster_centers_[assigned], axis=1)
    profiles = []
    for group in range(k):
        subset = assigned == group
        # Empty/very sparse training peers use TRAIN-global fallback, not future data.
        if subset.sum() < 10:
            subset = np.ones(len(train), dtype=bool)
        local = z[subset]
        med = np.median(local, axis=0)
        mad = np.median(np.abs(local - med), axis=0)
        mad = np.where(mad > .05, mad, .05)
        a = amounts[subset]
        amed = float(np.median(a))
        amad = max(.1, float(np.median(np.abs(a - amed))))
        dscale = max(.1, float(np.median(distances[subset])))
        profiles.append({"count": int((assigned == group).sum()), "component_median": med.tolist(),
                         "component_mad": mad.tolist(), "amount_log_median": amed,
                         "amount_log_mad": amad, "distance_median": dscale,
                         "global_fallback": bool((assigned == group).sum() < 10)})
    return {"version": VERSION, "fit_uses_targets": False, "fit_rows": len(train),
            "kind": "TRAIN-only population clusters of opaque PCA components, not per-account history",
            "algorithm": {"name": "MiniBatchKMeans", "requested_clusters": clusters,
                          "actual_clusters": k, "seed": seed, "n_init": 5,
                          "batch_size": 2048, "max_iter": 100,
                          "component_mad_floor": .05, "amount_log_mad_floor": .1,
                          "distance_median_floor": .1, "sparse_peer_global_fallback_below": 10},
            "center": center.tolist(), "scale": scale.tolist(),
            "centroids": km.cluster_centers_.tolist(), "profiles": profiles}


def peer_features(frame: pd.DataFrame, profile: dict) -> pd.DataFrame:
    if profile.get("version") != VERSION or profile.get("fit_uses_targets") is not False:
        raise ValueError("Invalid target-blind peer profile")
    z = (frame[PC].to_numpy(dtype=float) - profile["center"]) / profile["scale"]
    centers = np.asarray(profile["centroids"])
    # Allocation stays bounded: N x K, not N x K x 28.
    squared = np.maximum(0., (z*z).sum(axis=1)[:, None] + (centers*centers).sum(axis=1)
                         - 2 * z @ centers.T)
    assigned = squared.argmin(axis=1)
    distance = np.sqrt(squared[np.arange(len(z)), assigned])
    output = frame[BASE].copy()
    extra = np.zeros((len(frame), 3))
    log_amount = np.log1p(frame.Amount.to_numpy())
    for group, info in enumerate(profile["profiles"]):
        mask = assigned == group
        extra[mask, 0] = distance[mask] / info["distance_median"]
        excess = np.abs((z[mask] - info["component_median"]) / info["component_mad"])
        extra[mask, 1] = np.quantile(excess, .9, axis=1) if mask.any() else []
        extra[mask, 2] = np.abs(log_amount[mask] - info["amount_log_median"]) / info["amount_log_mad"]
    output[PEER] = extra
    return output


def metrics(y: np.ndarray, scores: np.ndarray, threshold: float) -> dict:
    flags = scores >= threshold
    tp, fp = int((flags & (y == 1)).sum()), int((flags & (y == 0)).sum())
    fn, tn = int((~flags & (y == 1)).sum()), int((~flags & (y == 0)).sum())
    return {"rows": len(y), "positives": int(y.sum()), "flagged": int(flags.sum()),
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": tp/(tp+fp) if tp+fp else None,
            "recall": tp/(tp+fn) if tp+fn else None,
            "average_precision": float(average_precision_score(y, scores)) if len(np.unique(y)) == 2 else None}


def evaluate(frame: pd.DataFrame, labels: np.ndarray, scores: np.ndarray,
             threshold: float, small_cutoff: float) -> dict:
    small = frame.Amount.to_numpy() <= small_cutoff
    slices = {"all": metrics(labels, scores, threshold),
              "small_amount": metrics(labels[small], scores[small], threshold)}
    # Relative-time thirds are stability slices, not independent customer folds.
    times = frame.Time.to_numpy()
    bounds = np.quantile(times, [1/3, 2/3])
    groups = np.searchsorted(bounds, times, side="left")
    for group in range(3):
        mask = groups == group
        slices[f"relative_time_block:{group}"] = metrics(labels[mask], scores[mask], threshold)
    return slices


def select(results: dict) -> dict:
    reference = results[REFERENCE]
    excluded = {}
    eligible = []
    for key, slices in results.items():
        reasons = []
        for name, baseline in reference.items():
            if slices[name]["tp"] < baseline["tp"]:
                reasons.append(f"{name}: fewer reference true positives")
        if slices["all"]["fp"] > reference["all"]["fp"]:
            reasons.append("all: more false positives")
        if reasons:
            excluded[key] = reasons
        else:
            eligible.append(key)
    eligible.sort(key=lambda key: (-(results[key]["all"]["precision"] or 0.),
                                   results[key]["all"]["fp"], key != REFERENCE, key))
    winner = eligible[0]
    improvement = (results[winner]["all"]["precision"] or 0.) > (reference["all"]["precision"] or 0.)
    if not improvement:
        winner = REFERENCE
    return {"reference": REFERENCE, "selected": winner, "improvement_found": improvement,
            "required_reference_tp_retention": 1., "source": "held-out supplied validation labels only",
            "slices_guarded": list(reference), "excluded": excluded}


def run(source: Path, metadata: Path, out: Path, *, seed: int = 20261020,
        estimators: int = 100) -> dict:
    source, metadata, out = source.resolve(), metadata.resolve(), out.resolve()
    if out.exists():
        raise FileExistsError("Use a new run directory; frozen research is immutable")
    out.mkdir(parents=True)
    code = [Path(__file__).resolve(), Path(__file__).with_name("model.py").resolve()]
    initial = {str(path): _hash(path) for path in (source, metadata, *code)}
    provenance = json.loads(metadata.read_text())["data_set_description"]
    if str(provenance["id"]) != "1597" or str(provenance["version"]) != "1":
        raise ValueError("Expected official OpenML 1597 version1 metadata")
    frames, partition = chronological_partitions(read_features(source))
    small_cutoff = float(frames["train"].Amount.quantile(.25))
    protocol = {"version": VERSION, "started_at_utc": _now(), "source_api": SOURCE_API,
                "creator_page": SOURCE_PAGE, "dataset_id": 1597, "dataset_version": 1,
                "download_url": provenance["parquet_url"], "source_sha256": initial[str(source)],
                "metadata_sha256": initial[str(metadata)], "licence_metadata_verbatim": provenance.get("licence"),
                "licence_limitation": "OpenML says Public; exact reusable licence is not established. Raw data stays ignored and is not redistributed.",
                "currency": "source units unknown; no conversion or USD/EUR assumption",
                "label_provenance": "Supplied Class fraud labels for real anonymized European-card transactions; not FCIP reviewer decisions or synthetic injections. Independent adjudication is unavailable.",
                "feature_names": {"raw": BASE, "peer": BASE + PEER},
                "peer_fit": "TRAIN-only robust median/IQR scaling of opaque PCA components; MiniBatchKMeans16, n_init5, batch2048, max_iter100; fixed seed. No customer identity claim.",
                "excluded": ["Class", "Time", "row index", "absolute date", "account/device/counterparty inventions"],
                "partition": partition, "small_amount_cutoff_train_q25": small_cutoff,
                "fits_use_targets": False, "all_fits_before_validation_labels": True,
                "thresholds": ["auto_0.5", "train_q0.90", "train_q0.95", "train_q0.99"],
                "selection": "precision with TP>=reference in all/small/three relative-time blocks, FP<=reference; fallback allowed",
                "candidates": [{"name": n, "view": v, "max_samples": s} for n,v,s in SPECS],
                "seed": seed, "estimators": estimators,
                "limitations": ["Public historical benchmark, not unseen production data or fraud-probability calibration.",
                                "No account identity: customer-disjoint splitting and behavioural history are impossible.",
                                "PCA was supplied upstream; its fit population/time scope is unknown and cannot be reconstructed.",
                                "Only two days of relative time; no long-term drift or AML-network claim."]}
    _write(out / "protocol.json", protocol)
    profile = fit_peers(frames["train"], seed=seed)
    _write(out / "peer_profile.json", profile)
    # Seal declared protocol and all TRAIN-derived preprocessing before the
    # first forest fit. A mid-run edit is rejected rather than re-hashed as truth.
    for path in (out / "protocol.json", out / "peer_profile.json"):
        initial[str(path)] = _hash(path)
    train_views = {"raw": frames["train"][BASE], "peer": peer_features(frames["train"], profile)}
    models, thresholds = {}, {}
    for name, view, sample in SPECS:
        text, train_scores = fit_model(train_views[view], list(train_views[view]), seed=seed,
                                       estimators=estimators, max_samples=sample)
        model_path = out / "models" / name / "model.txt"
        model_path.parent.mkdir(parents=True)
        model_path.write_text(text)
        threshold = {"auto": .5, **{f"train_q{q:.2f}": float(np.quantile(train_scores, q))
                                    for q in (.9, .95, .99)}}
        thresholds[name] = threshold
        models[name] = (text, view)
        _write(model_path.with_name("model_card.json"), {"research_only": True, "status": "DRAFT",
                "fit_uses_targets": False, "source_sha256": initial[str(source)],
                "train_rows": len(frames["train"]), "view": view, "thresholds": threshold,
                "features": list(train_views[view]), "model_sha256": _hash(model_path)})
        for path in (model_path, model_path.with_name("model_card.json")):
            initial[str(path)] = _hash(path)
        print(json.dumps({"stage": "fit", "candidate": name, "targets_used": False}), flush=True)
    del train_views
    verify_frozen({"files": initial})
    # This is the first supplied target parse; TRAIN Class is never requested.
    validation_labels = read_labels(source, "validation", partition)
    validation = {}
    validation_scores = {}
    views = {"raw": frames["validation"][BASE], "peer": peer_features(frames["validation"], profile)}
    for name, (text, view) in models.items():
        scores = predict_model(text, views[view])
        validation_scores[name] = scores
        for suffix, threshold in thresholds[name].items():
            validation[f"{name}__{suffix}"] = evaluate(frames["validation"], validation_labels,
                                                     scores, threshold, small_cutoff)
    choice = select(validation)
    _write(out / "validation.json", validation)
    _write(out / "selection.json", choice)
    pd.DataFrame({"Time": frames["validation"].Time, "Amount": frames["validation"].Amount,
                  "supplied_label": validation_labels, **validation_scores}).to_parquet(out / "validation_scores.parquet", index=False)
    for path in (out / "validation.json", out / "selection.json", out / "validation_scores.parquet"):
        initial[str(path)] = _hash(path)
    verify_frozen({"files": initial})
    frozen = {"frozen_at_utc": _now(), "files": dict(initial)}
    _write(out / "frozen.json", frozen)
    frozen_manifest_sha = _hash(out / "frozen.json")
    verify_frozen(frozen)
    if _hash(out / "frozen.json") != frozen_manifest_sha:
        raise ValueError("Frozen manifest changed during final evaluation")
    final_started = _now()
    # Only now may final Class be projected and parsed. No selection follows.
    final_labels = read_labels(source, "final", partition)
    final_views = {"raw": frames["final"][BASE], "peer": peer_features(frames["final"], profile)}
    final = {}
    final_scores = {}
    for role, key in (("reference", REFERENCE), ("selected", choice["selected"])):
        name, suffix = key.split("__")
        text, view = models[name]
        scores = predict_model(text, final_views[view])
        final[role] = {"candidate": key, "threshold": thresholds[name][suffix],
                       "slices": evaluate(frames["final"], final_labels, scores,
                                          thresholds[name][suffix], small_cutoff)}
        final_scores[role] = scores
    pd.DataFrame({"Time": frames["final"].Time, "Amount": frames["final"].Amount,
                  "supplied_label": final_labels, **final_scores}).to_parquet(out / "final_scores.parquet", index=False)
    final_guard = []
    for name, base in final["reference"]["slices"].items():
        if final["selected"]["slices"][name]["tp"] < base["tp"]:
            final_guard.append(f"{name}: fewer reference true positives")
    if final["selected"]["slices"]["all"]["fp"] > final["reference"]["slices"]["all"]["fp"]:
        final_guard.append("all: more false positives")
    verify_frozen(frozen)
    if _hash(out / "frozen.json") != frozen_manifest_sha:
        raise ValueError("Frozen manifest changed during final evaluation")
    report = {"version": VERSION, "completed_at_utc": _now(), "protocol": protocol,
              "selection": choice, "validation_selected": validation[choice["selected"]],
              "validation_reference": validation[REFERENCE], "final": final,
              "final_reference_retention_passes": not final_guard, "final_guard_failures": final_guard,
              "final_labels_first_parsed_at_utc": final_started, "freeze_precedes_final_labels": True,
              "fit_uses_targets": False, "selection_uses_supplied_validation_labels": True,
              "no_database_registration_or_promotion": True, "frozen": frozen,
              "frozen_manifest_sha256": frozen_manifest_sha}
    _write(out / "report.json", report)
    print(json.dumps({"stage": "complete", "selected": choice["selected"],
                      "improvement_found": choice["improvement_found"], "out": str(out)}), flush=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20261020)
    parser.add_argument("--estimators", type=int, default=100)
    args = parser.parse_args()
    run(args.source, args.metadata, args.out, seed=args.seed, estimators=args.estimators)


if __name__ == "__main__":
    main()
