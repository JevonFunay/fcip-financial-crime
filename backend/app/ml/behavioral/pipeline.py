"""Reproducible, label-free fit and post-fit evaluation for configured CSVs.

    python -m app.ml.behavioral.pipeline --source sample_data/raw/bank_transactions_data_2_augmented_clean_2.csv

The generator's sidecars are opened only by evaluation, after models are
written. No labels choose a feature, tree, hyperparameter or threshold. This
is a reusable configured workflow, not automatic interpretation of arbitrary
banking schemas, and not an automatic production-promotion service.
"""

from __future__ import annotations

import argparse
import csv
import gc
import hashlib
import io
import json
import time
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

from app.ml.behavioral.features import (FEATURE_DEFINITIONS, FEATURE_SET_VERSION,
                                       build_features, feature_names, fit_vocabulary)
from app.ml.behavioral.model import (ARTEFACT_TYPE, DEFAULT_THRESHOLD, EXPLANATION_METHOD,
                                    fit_model, predict_model)
from app.ml.behavioral.standardize import normalize_csv
from app.ml.models import canonical, definition_hash

MODEL_NAMES = {"AML": "aml_if_v1", "FRAUD": "fraud_if_v1"}
CAVEAT = ("Research data: public unlabelled source and generated simulations. Isolation scores are not "
          "fraud probabilities. Precision/recall describe only explicit synthetic stress assumptions, "
          "not real financial crime or production performance (TRD §10.6, FRD L-01).")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, default=str, allow_nan=False)
        handle.write("\n")


def load_observations(path: Path, profile: str = "fcip_canonical_v1") -> tuple[pd.DataFrame, dict]:
    result = normalize_csv(path, profile)
    if len(result.quarantine):
        raise ValueError(f"{path}: {len(result.quarantine)} rows failed normalization; inspect before fitting")
    return result.frame.drop(columns=["source_original"], errors="ignore"), result.report


def distribution(scores: np.ndarray, threshold: float = DEFAULT_THRESHOLD) -> dict:
    return {"units": len(scores), "units_flagged": int((scores >= threshold).sum()),
            "flag_rate": float(np.mean(scores >= threshold)) if len(scores) else None,
            "score_quantiles": {str(q): float(np.quantile(scores, q)) for q in (0, .25, .5, .9, .99, 1)}
            if len(scores) else {}, "threshold": threshold}


def train_models(train_csv: Path, source_train: pd.DataFrame, model_root: Path, *, seed: int,
                 provenance: dict, estimators: int = 100, max_samples: int = 256) -> dict[str, dict]:
    """No validation argument, target argument or ground-truth reader exists here."""
    generated, reconciliation = load_observations(train_csv)
    frame = pd.concat([source_train, generated], ignore_index=True)
    vocabulary = fit_vocabulary(frame)
    del generated
    gc.collect()
    cards = {}
    for unit, version in MODEL_NAMES.items():
        started = time.perf_counter()
        out = model_root / version
        if out.exists():
            raise FileExistsError(f"Refusing to overwrite immutable model directory {out}")
        features = build_features(frame, unit, vocabulary)
        names = feature_names(unit)
        model_text, scores = fit_model(features, names, seed=seed, estimators=estimators, max_samples=max_samples)
        card = {
            "artefact_type": ARTEFACT_TYPE, "version": version, "unit": unit, "state": "DRAFT",
            "definition_hash": definition_hash(model_text, canonical(names), FEATURE_SET_VERSION,
                                               canonical(vocabulary), canonical(DEFAULT_THRESHOLD)),
            "created_by": "app.ml.behavioral.pipeline", "submitted_by": None, "approved_by": None,
            "approved_at": None, "effective_from": None, "simulation_reference": None, "superseded_by": None,
            "caveat": CAVEAT, "trained_at": datetime.now(timezone.utc).isoformat(),
            "model": {"algorithm": "sklearn IsolationForest", "training_kind": "unsupervised",
                      "unit": "one account observation proxy per calendar week" if unit == "AML" else "one transaction",
                      "unit_code": unit, "threshold": DEFAULT_THRESHOLD,
                      "threshold_rule": "IsolationForest auto reference 0.5; FCIP inclusive review cutoff >=0.5; no labels/prevalence assumed",
                      "score_kind": "anomaly_score", "explanation_method": EXPLANATION_METHOD,
                      "hyperparameters": {"n_estimators": estimators, "max_samples": max_samples,
                                          "contamination": "auto", "seed": seed},
                      "preprocessing": "Train-only numeric medians and signed log1p; missing observed values remain null in explanations."},
            "feature_set": {"version": FEATURE_SET_VERSION, "features": names, "vocabulary": vocabulary,
                            "definitions": {name: FEATURE_DEFINITIONS[(unit, name)] for name in names},
                            "normalization_profile": "fcip_canonical_v1"},
            "training_data": {**provenance, "profile": "behavioral_train", "seed": seed,
                              "source_unlabelled_rows": len(source_train),
                              "generated_rows": len(frame) - len(source_train), "fit_input_rows": len(frame),
                              "fit_units": len(features), "targets_used": False,
                              "generator_version": provenance.get("generator_version"),
                              "normalization": reconciliation},
            "training_distribution": distribution(scores),
            "validation": {"unlabelled": {"precision": None, "recall": None,
                                           "reason": "No verified fraud/AML target labels exist in the source."}},
            "seconds": round(time.perf_counter() - started, 3),
        }
        out.mkdir(parents=True)
        (out / "model.txt").write_text(model_text, encoding="utf-8")
        write_json(out / "model_card.json", card)
        cards[unit] = card
        print(json.dumps({"phase": "fit_complete", "unit": unit, "fit_units": len(features),
                          "seconds": card["seconds"], "targets_used": False}), flush=True)
        del features, scores
        gc.collect()
    return cards


def _metrics(y: np.ndarray, scores: np.ndarray, threshold: float) -> dict:
    y = np.asarray(y, dtype=int)
    flagged = scores >= threshold
    tp, fp = int((flagged & (y == 1)).sum()), int((flagged & (y == 0)).sum())
    fn, tn = int((~flagged & (y == 1)).sum()), int((~flagged & (y == 0)).sum())
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "average_precision": float(average_precision_score(y, scores)) if len(np.unique(y)) == 2 else None,
            "roc_auc": float(roc_auc_score(y, scores)) if len(np.unique(y)) == 2 else None,
            "label_scope": "Synthetic injected stress versus assumed synthetic background/lookalikes; not real crime labels."}


def _evaluation_labels(observations: pd.DataFrame, features: pd.DataFrame, unit: str, truth: pd.DataFrame) -> pd.DataFrame:
    if truth.transaction_ref.duplicated().any() or not set(observations.transaction_ref) == set(truth.transaction_ref):
        raise ValueError("Synthetic ground truth must align one-to-one with every generated observation")
    known = observations[["transaction_ref", "account_number", "transaction_date", "amount"]].merge(
        truth.drop(columns=["account_number"], errors="ignore"), on="transaction_ref", validate="one_to_one")
    if not set(known.label.unique()).issubset({0, 1}):
        raise ValueError("Only explicit 0/1 synthetic assumptions may enter benchmark metrics")
    if unit == "FRAUD":
        return features[["transaction_ref"]].merge(known, on="transaction_ref", validate="one_to_one")
    timestamps = pd.to_datetime(known.transaction_date, utc=True)
    starts = timestamps.dt.floor("D") - pd.to_timedelta(timestamps.dt.dayofweek, unit="D")
    known["as_of"] = starts + pd.Timedelta(days=7) - pd.Timedelta(seconds=1)
    weekly = known.groupby(["account_number", "as_of"], as_index=False).agg(
        label=("label", "max"), amount=("amount", "mean"))
    return features[["account_number", "as_of"]].merge(weekly, on=["account_number", "as_of"], validate="one_to_one")


def evaluate(models_root: Path, observations: pd.DataFrame, *, ground_truth: Path | None = None,
             original_rows: bool = False, out: Path | None = None) -> dict:
    """Only this function reads an optional synthetic evaluation sidecar."""
    truth = pd.read_csv(ground_truth) if ground_truth is not None else None
    report = {"caveat": CAVEAT, "input_rows": len(observations), "source_is_unlabelled": truth is None,
              "models": {}}
    for unit, version in MODEL_NAMES.items():
        folder = models_root / version
        card = json.loads((folder / "model_card.json").read_text())
        text = (folder / "model.txt").read_text()
        vocabulary = card["feature_set"]["vocabulary"]
        features = build_features(observations, unit, vocabulary)
        scores = predict_model(text, features)
        result = distribution(scores, card["model"]["threshold"])
        if truth is not None:
            labels = _evaluation_labels(observations, features, unit, truth)
            result["synthetic_stress"] = _metrics(labels.label.to_numpy(), scores, card["model"]["threshold"])
            small = pd.to_numeric(labels.amount).to_numpy(dtype=float) <= float(vocabulary["small_amount_threshold"])
            result["small_amount"] = _metrics(labels.label.to_numpy()[small],
                                              scores[small],
                                              card["model"]["threshold"])
            joined = observations[["transaction_ref", "account_number", "transaction_date"]].merge(truth, on="transaction_ref",
                                                                                              suffixes=("", "_truth"))
            if unit == "FRAUD":
                flags = pd.DataFrame({"transaction_ref": features.transaction_ref, "flagged": scores >= card["model"]["threshold"]})
                joined = joined.merge(flags, on="transaction_ref", validate="one_to_one")
            else:
                ts = pd.to_datetime(joined.transaction_date, utc=True)
                joined["as_of"] = ts.dt.floor("D") - pd.to_timedelta(ts.dt.dayofweek, unit="D") + pd.Timedelta(days=7) - pd.Timedelta(seconds=1)
                flags = features[["account_number", "as_of"]].assign(flagged=scores >= card["model"]["threshold"])
                joined = joined.merge(flags, on=["account_number", "as_of"], validate="many_to_one")
            positive = joined[joined.label.eq(1)]
            result["scenarios"] = {}
            for name, group in positive.groupby("scenario"):
                detected = group.groupby("scenario_id").flagged.max() if "scenario_id" in group else group.groupby("account_number").flagged.max()
                result["scenarios"][name] = {"detected": int(detected.sum()), "total": len(detected),
                                            "recall": float(detected.mean())}
        else:
            result.update({"precision": None, "recall": None, "roc_auc": None,
                           "reason": "No verified fraud/AML labels; flag count is review load, not detection accuracy."})
        report["models"][unit] = result
        if out is not None:
            out.mkdir(parents=True, exist_ok=True)
            review = features[["account_number", "transaction_ref", "as_of"]].assign(
                score=scores, flagged=scores >= card["model"]["threshold"])
            review.to_parquet(out / f"{unit.lower()}_scores.parquet", index=False)
        del features, scores
        gc.collect()
    return report


def publish_upload_parts(csv_path: Path, out: Path, *, max_bytes: int = 50 * 1024 * 1024) -> dict:
    """Split complete canonical records under the unchanged web upload cap."""
    if out.exists():
        raise FileExistsError(f"Refusing to overwrite {out}")
    out.mkdir(parents=True)
    parts, buffer, rows = [], bytearray(), 0
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        def encoded(record):
            stream = io.StringIO(newline="")
            csv.writer(stream, lineterminator="\n").writerow(record)
            return stream.getvalue().encode("utf-8")
        first = encoded(header)
        buffer.extend(first)
        for record in reader:
            data = encoded(record)
            if len(first) + len(data) > max_bytes:
                raise ValueError("One CSV record exceeds the upload cap")
            if rows and len(buffer) + len(data) > max_bytes:
                name = f"transactions_{len(parts) + 1:03d}.csv"
                (out / name).write_bytes(buffer)
                parts.append({"file": name, "rows": rows, "bytes": len(buffer), "sha256": sha256(out / name)})
                buffer, rows = bytearray(first), 0
            buffer.extend(data)
            rows += 1
        if rows:
            name = f"transactions_{len(parts) + 1:03d}.csv"
            (out / name).write_bytes(buffer)
            parts.append({"file": name, "rows": rows, "bytes": len(buffer), "sha256": sha256(out / name)})
    manifest = {"source_sha256": sha256(csv_path), "total_rows": sum(x["rows"] for x in parts),
                "max_bytes": max_bytes, "parts": parts,
                "instruction": "Prepare matching account proxies; upload all parts before manual detection. No label sidecars are uploaded."}
    write_json(out / "upload_manifest.json", manifest)
    return manifest


def run(source: Path, out: Path, models_out: Path, *, train_rows: int, validation_rows: int, test_rows: int,
        seed: int, reference_date: date, estimators: int = 100, max_samples: int = 256) -> dict:
    from app.ml.behavioral.generate import generate, source_partitions
    if out.exists() or any((models_out / version).exists() for version in MODEL_NAMES.values()):
        raise FileExistsError("Choose new output/model roots; existing datasets and versions are never overwritten")
    out.mkdir(parents=True)
    partitions = source_partitions(source)
    original, source_report = load_observations(source, "kaggle_bank_v1")
    write_json(out / "source_profile.json", source_report)
    print(json.dumps({"phase": "generate_train", "rows": train_rows}), flush=True)
    train = generate(source, out / "train", "train", train_rows, seed, reference_date=reference_date)
    generate(source, out / "validation", "validation", validation_rows, seed + 1,
             fit_profile=out / "train" / "fit_profile.json", reference_date=reference_date)
    source_train = original[original.account_number.isin(partitions["train"])].copy()
    cards = train_models(out / "train" / "transactions.csv", source_train, models_out, seed=seed,
                         provenance={"source_sha256": sha256(source), "source_account_partition": partitions["train"],
                                     "generator_version": train["generator_version"],
                                     "generated_csv_sha256": sha256(out / "train" / "transactions.csv"),
                                     "manifest_sha256": sha256(out / "train" / "manifest.json")},
                         estimators=estimators, max_samples=max_samples)
    reports = {}
    generated, _ = load_observations(out / "validation" / "transactions.csv")
    reports["validation"] = evaluate(models_out, generated,
                                      ground_truth=out / "validation" / "ground_truth.csv",
                                      out=out / "evaluation" / "validation")
    reports["source_validation_unlabelled"] = evaluate(
        models_out, original[original.account_number.isin(partitions["validation"])].copy())
    del generated
    # Record diagnostic validation results before the initial card freeze.
    # This never changes the estimator, preprocessing, threshold or features.
    for unit, version in MODEL_NAMES.items():
        cards[unit]["validation"] = {
            "unlabelled": reports["source_validation_unlabelled"]["models"][unit],
            "synthetic_stress": reports["validation"]["models"][unit]["synthetic_stress"],
        }
        (models_out / version / "model_card.json").write_text(
            json.dumps(cards[unit], indent=2, default=str, allow_nan=False) + "\n", encoding="utf-8")
    frozen = {version: {name: sha256(models_out / version / name) for name in ("model.txt", "model_card.json")}
              for version in MODEL_NAMES.values()}
    frozen_at = datetime.now(timezone.utc).isoformat()
    # A genuinely new dataset is generated only after both model artefacts exist.
    print(json.dumps({"phase": "generate_post_fit_test", "rows": test_rows}), flush=True)
    test_generation_started_at = datetime.now(timezone.utc).isoformat()
    test = generate(source, out / "test", "test", test_rows, seed + 2,
                    fit_profile=out / "train" / "fit_profile.json", reference_date=reference_date)
    for split in ("test",):
        generated, _ = load_observations(out / split / "transactions.csv")
        reports[split] = evaluate(models_out, generated, ground_truth=out / split / "ground_truth.csv",
                                  out=out / "evaluation" / split)
        source_split = original[original.account_number.isin(partitions[split])].copy()
        reports[f"source_{split}_unlabelled"] = evaluate(models_out, source_split)
        del generated, source_split
        gc.collect()
    if any(sha256(models_out / version / name) != expected for version, files in frozen.items() for name, expected in files.items()):
        raise ValueError("Model artefacts changed during final evaluation")
    uploads = publish_upload_parts(out / "test" / "transactions.csv", out / "test_web")
    report = {"schema_version": 1, "completed_at": datetime.now(timezone.utc).isoformat(),
              "source": {"path": str(source), "sha256": sha256(source), "rows": len(original), "currency": "USD",
                         "currency_provenance": "Confirmed by Jevon on 14 October 2026", "target_labels": False,
                         "timezone": None, "publisher_url": None},
              "generated_rows": {"train": train_rows, "validation": validation_rows, "test": test_rows},
              "source_account_partitions": partitions, "seeds": {"train": seed, "validation": seed + 1, "test": seed + 2},
              "frozen_model_checksums": frozen, "test_generated_after_fit": True,
              "models_frozen_at": frozen_at, "test_generation_started_at": test_generation_started_at,
              "test_manifest_sha256": sha256(out / "test" / "manifest.json"),
              "fit_uses_targets": False, "threshold_tuned_on_labels": False,
              "models": {unit: {"version": card["version"], "fit_units": card["training_data"]["fit_units"],
                                 "fit_input_rows": card["training_data"]["fit_input_rows"]} for unit, card in cards.items()},
              "evaluation": reports, "web_uploads": uploads, "caveat": CAVEAT,
              "limitations": ["Source ancestry, publisher and original augmentation recipe are not independently verified.",
                              "Source account demographics are inconsistent; generated account coherence is a simulation choice.",
                              "No counterparty graph, merchant ownership, MCC, country or independently labelled financial crime.",
                              "Synthetic volume is not an increase in independent real observations.",
                              "Account proxies are not resolved real customers; source timestamps have unknown timezone.",
                              "Frozen threshold 0.5 follows sklearn auto isolation scoring, not a measured crime prevalence."]}
    write_json(out / "evaluation.json", report)
    print(json.dumps({"phase": "complete", "report": str(out / "evaluation.json"),
                      "generated_rows": report["generated_rows"], "test_generated_after_fit": True}), flush=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("ml_data/behavioral_s20261015"))
    parser.add_argument("--models-out", type=Path, default=Path("ml_models/behavioral_s20261015"))
    parser.add_argument("--train-rows", type=int, default=1_000_000)
    parser.add_argument("--validation-rows", type=int, default=200_000)
    parser.add_argument("--test-rows", type=int, default=200_000)
    parser.add_argument("--seed", type=int, default=20261015)
    parser.add_argument("--reference-date", type=date.fromisoformat, default=date(2026, 10, 15))
    parser.add_argument("--estimators", type=int, default=100)
    parser.add_argument("--max-samples", type=int, default=256)
    args = parser.parse_args()
    if min(args.train_rows, args.validation_rows, args.test_rows) < 1000:
        parser.error("Each generated split needs at least 1,000 observations")
    run(args.source, args.out, args.models_out, train_rows=args.train_rows, validation_rows=args.validation_rows,
        test_rows=args.test_rows, seed=args.seed, reference_date=args.reference_date,
        estimators=args.estimators, max_samples=args.max_samples)


if __name__ == "__main__":
    main()
