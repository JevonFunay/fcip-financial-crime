"""Immutable, blinded, offline review of unlabelled validation observations.

No fitting, application database, disposition mutation or crime confirmation
exists here. Review outcomes describe visible behavioural evidence only. A
sealed stratified sample keeps both flags and nonflags, and never treats the
unlabelled source or incomplete reviews as normal activity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .pipeline import load_observations, sha256, write_json

VERSION = "blinded_validation_review_v1"
STATUSES = {"PENDING", "NORMAL", "ANOMALOUS", "INSUFFICIENT_EVIDENCE"}
RESOLVED = {"NORMAL", "ANOMALOUS"}
ANNOTATIONS = ["review_id", "status", "reason", "reviewer_id", "reviewed_at", "domain_outcome"]
VISIBLE = ["transaction_ref", "account_number", "transaction_date", "amount", "currency", "direction", "channel",
           "timestamp_precision", "source_timezone_known", "device_ref", "ip_address", "merchant_ref", "location",
           "login_attempts", "transaction_duration", "account_balance"]
CAVEAT = ("Reviewer agreement concerns visible anomalies, not verified fraud, money laundering, or production accuracy. "
          "Unreviewed and insufficient-evidence cases are unresolved, never normal labels. "
          "This validation population has already been used in research; it is not a new final holdout.")


def _records(frame: pd.DataFrame) -> list[dict]:
    out = frame.loc[:, [name for name in VISIBLE if name in frame]].copy()
    # A timezone-unknown source must not gain a UTC fact from its storage carrier.
    if "transaction_date" in out:
        known = out.get("source_timezone_known", pd.Series(False, index=out.index)).fillna(False).astype(bool)
        stamps = pd.to_datetime(out.transaction_date, utc=True)
        precision = out.get("timestamp_precision", pd.Series("day", index=out.index)).fillna("day")
        formatted = []
        for stamp, is_known, unit in zip(stamps, known, precision):
            if unit == "day":
                formatted.append(stamp.strftime("%Y-%m-%d"))
            else:
                local = stamp if is_known else stamp.tz_localize(None)
                formatted.append(local.isoformat(timespec="minutes" if unit == "minute" else "seconds"))
        out["transaction_date"] = formatted
    return json.loads(out.to_json(orient="records", date_format="iso"))


def _allocate(capacities: dict[str, int], target: int) -> dict[str, int]:
    if target <= 0 or target > sum(capacities.values()):
        raise ValueError("Sample size must be positive and no larger than its population")
    if target < len(capacities):
        raise ValueError("Sample size must cover every nonempty sampling stratum")
    allocation = {name: 0 for name in sorted(capacities)}
    while sum(allocation.values()) < target:
        for name in allocation:
            if allocation[name] < capacities[name] and sum(allocation.values()) < target:
                allocation[name] += 1
    return allocation


def _population(observations: pd.DataFrame, unit_scores: dict[str, pd.DataFrame], cutoff: float) -> pd.DataFrame:
    if set(unit_scores) != {"AML", "FRAUD"}:
        raise ValueError("Both AML and FRAUD frozen score populations are required")
    required = {"transaction_ref", "account_number", "transaction_date", "amount", "currency"}
    if not required.issubset(observations) or observations.empty or observations.transaction_ref.duplicated().any():
        raise ValueError("Source observations need unique transaction references and canonical fields")
    if not math.isfinite(cutoff) or cutoff <= 0:
        raise ValueError("The frozen source-training low-amount cutoff must be positive")
    obs = observations.copy()
    obs["transaction_date"] = pd.to_datetime(obs.transaction_date, utc=True, errors="raise")
    if obs.transaction_date.isna().any() or obs.account_number.isna().any():
        raise ValueError("Source transaction dates and account references must be present")
    obs["amount"] = pd.to_numeric(obs.amount, errors="raise")
    if not np.isfinite(obs.amount).all() or obs.amount.le(0).any() or obs.currency.nunique() != 1:
        raise ValueError("Review observations need positive finite amounts and a single currency")
    obs["as_of"] = obs.transaction_date.dt.floor("D") - pd.to_timedelta(obs.transaction_date.dt.dayofweek, unit="D") + pd.Timedelta(days=7) - pd.Timedelta(seconds=1)
    population = []
    for unit in ("AML", "FRAUD"):
        score = unit_scores[unit].copy()
        keys = ["transaction_ref"] if unit == "FRAUD" else ["account_number", "as_of"]
        if not set(keys + ["score", "threshold"]).issubset(score) or score.duplicated(keys).any():
            raise ValueError("Frozen scores need unique unit keys, scores and thresholds")
        if not np.isfinite(score[["score", "threshold"]].to_numpy(dtype=float)).all():
            raise ValueError("Scores and thresholds must be finite")
        if unit == "FRAUD":
            base = obs[["transaction_ref", "account_number", "transaction_date", "amount"]].copy()
            score = score[keys + ["score", "threshold"]]
        else:
            score["as_of"] = pd.to_datetime(score.as_of, utc=True, errors="raise")
            base = obs.groupby(keys, as_index=False).agg(amount=("amount", "mean"))
            base["transaction_ref"] = ""
            base["transaction_date"] = base.as_of
            score = score[keys + ["score", "threshold"]]
        merged = base.merge(score, on=keys, how="outer", validate="one_to_one", indicator=True)
        if not merged._merge.eq("both").all():
            raise ValueError("Frozen score keys must cover exactly the source validation population")
        merged = merged.drop(columns="_merge")
        merged["unit"] = unit
        merged["flagged"] = merged.score.ge(merged.threshold)
        merged["small"] = merged.amount.le(cutoff)
        merged["stratum"] = [f"{unit}|flag={int(flag)}|small={int(small)}" for flag, small in zip(merged.flagged, merged.small)]
        merged["unit_key"] = merged.transaction_ref if unit == "FRAUD" else merged.account_number + "|" + merged.as_of.astype(str)
        population.append(merged)
    return pd.concat(population, ignore_index=True)


def export_review_packet(observations: pd.DataFrame, unit_scores: dict[str, pd.DataFrame], out: Path, *,
                         sample_size: int = 300, seed: int = 20261020, small_amount_cutoff: float,
                         input_hashes: dict[str, str], model_metadata: dict) -> dict:
    """Export a new immutable packet; annotations are deliberately all pending.

    ``unit_scores`` contains source-validation scores only. The caller must
    establish frozen input/model provenance; no model fitting happens here.
    """
    out = Path(out)
    if out.exists():
        raise FileExistsError(f"Refusing to overwrite review artefacts: {out}")
    if not input_hashes or any(len(value) != 64 or any(c not in "0123456789abcdef" for c in value) for value in input_hashes.values()):
        raise ValueError("Every input requires a SHA-256 provenance hash")
    if set(model_metadata) != {"AML", "FRAUD"}:
        raise ValueError("Frozen model provenance is required for both units")
    export_code_sha = sha256(Path(__file__))
    population = _population(observations, unit_scores, small_amount_cutoff)
    if sample_size < population.stratum.nunique() or sample_size > len(population):
        raise ValueError("Sample size must cover all nonempty strata without exceeding the population")
    unit_sizes = population.groupby("unit").size().to_dict()
    unit_allocation = _allocate(unit_sizes, sample_size)
    rng = np.random.default_rng(seed)
    samples = []
    strata = {}
    for unit in sorted(unit_sizes):
        group = population[population.unit.eq(unit)]
        capacity = group.groupby("stratum").size().to_dict()
        allocated = _allocate(capacity, unit_allocation[unit])
        for stratum in sorted(capacity):
            frame = group[group.stratum.eq(stratum)].sort_values("unit_key", kind="stable")
            n, total = allocated[stratum], len(frame)
            picked = frame.iloc[rng.choice(total, size=n, replace=False)].copy()
            picked["population_size"] = total
            picked["sample_size"] = n
            picked["inclusion_probability"] = n / total
            picked["sampling_weight"] = total / n
            samples.append(picked)
            strata[stratum] = {"unit": unit, "flagged": bool(frame.flagged.iloc[0]), "small": bool(frame.small.iloc[0]),
                               "population_size": total, "sample_size": n, "inclusion_probability": n / total}
    sampled = pd.concat(samples, ignore_index=True)
    sampled["review_id"] = [hashlib.sha256(f"{VERSION}|{seed}|{unit}|{key}".encode()).hexdigest()[:24]
                            for unit, key in zip(sampled.unit, sampled.unit_key)]
    if sampled.review_id.duplicated().any():
        raise ValueError("Review identifiers collided")
    sampled = sampled.iloc[rng.permutation(len(sampled))].reset_index(drop=True)
    obs = observations.copy()
    obs["transaction_date"] = pd.to_datetime(obs.transaction_date, utc=True)
    obs["_available"] = obs.transaction_date
    precision = obs.get("timestamp_precision", pd.Series("day", index=obs.index))
    obs.loc[precision.eq("day"), "_available"] = obs.loc[precision.eq("day"), "transaction_date"].dt.floor("D") + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
    packet = []
    for row in sampled.itertuples():
        history = obs[obs.account_number.eq(row.account_number)]
        if row.unit == "FRAUD":
            target = history[history.transaction_ref.eq(row.transaction_ref)]
            current = target.iloc[0]
            boundary = current.transaction_date.floor("D") if current.get("timestamp_precision", "day") == "day" else current.transaction_date
            prior = history[history._available.lt(boundary)]
            unit_description = "one observed transaction"
        else:
            boundary = row.as_of - pd.Timedelta(days=7) + pd.Timedelta(seconds=1)
            target = history[history.transaction_date.ge(boundary) & history.transaction_date.le(row.as_of)]
            prior = history[history._available.lt(boundary)]
            unit_description = "one account calendar week; source timezone may be unknown"
        # Include the entire availability tie at the context boundary. An
        # arbitrary transaction ID cannot choose which simultaneous history
        # the reviewer is shown.
        available = prior.sort_values(["_available", "transaction_ref"])
        if len(available) > 30:
            lower = available.iloc[-30]._available
            available = available[available._available.ge(lower)]
        packet.append({"review_id": row.review_id, "unit": row.unit, "observation_unit": unit_description,
                       "observations": _records(target.sort_values(["transaction_date", "transaction_ref"])),
                       "strictly_prior_observations_count": len(prior),
                       "prior_context": _records(available),
                       "context_limit": "Latest 30 strictly available prior observations plus all observations tied at the oldest availability boundary; missing history and source fields remain unknown.",
                       "available_evidence": "Source observations only; no verified fraud outcome, counterparty graph, device trust register or timezone inference."})
    protocol = {"version": VERSION, "seed": seed, "sample_size": len(sampled), "small_amount_cutoff": small_amount_cutoff,
                "unit_low_amount_definitions": {"FRAUD": "transaction amount <= frozen source-training q25", "AML": "mean account-week amount <= frozen source-training q25"},
                "population": {unit: int(size) for unit, size in unit_sizes.items()}, "strata": strata,
                "sampling": "Equal allocation across units, then nonempty flag × low-amount strata, capacity-adjusted; simple random sampling without replacement inside each stratum.",
                "blinding_boundary": "Logical file separation only; sealed files are not encrypted or access-controlled. Only packet.jsonl, annotations_template.csv and REVIEW_INSTRUCTIONS.md should be given to the reviewer.",
                "blinding": "Reviewer packet and template omit scores, thresholds, model flags, stratum, synthetic labels and scenario identifiers. A separate sealed mapping is for evaluation only.",
                "status_definitions": {"PENDING": "Not yet reviewed.", "NORMAL": "No visible unusual behaviour in the available context; not verified legitimate activity.",
                                       "ANOMALOUS": "Visible unusual behaviour requiring investigation; not confirmed crime.", "INSUFFICIENT_EVIDENCE": "Visible data cannot support an anomaly or normal judgement; exclude from resolved denominators."},
                "domain_outcome": "UNKNOWN only: public unlabelled source has no independently verified crime evidence.",
                "evidence_limitations": "The downloaded source has no verified crime labels, known augmentation ancestry, device trust register or counterparty graph. Most timestamps have day precision and all timezones are unknown. These omissions can justify INSUFFICIENT_EVIDENCE; they never justify assuming normal or confirmed fraud.",
                "completed_requirements": "Reviewer identifier, substantive reason and offset-aware review timestamp; no auto-generated completed labels.",
                "caveat": CAVEAT, "input_sha256": input_hashes, "model_metadata": model_metadata,
                "code_sha256": export_code_sha}
    if sha256(Path(__file__)) != export_code_sha:
        raise ValueError("Review export code changed during packet preparation")
    out.mkdir(parents=True)
    write_json(out / "protocol.json", protocol)
    with (out / "packet.jsonl").open("x", encoding="utf-8") as handle:
        for record in packet:
            handle.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
    annotations = pd.DataFrame({"review_id": sampled.review_id, "status": "PENDING", "reason": "", "reviewer_id": "", "reviewed_at": "", "domain_outcome": "UNKNOWN"})
    annotations.to_csv(out / "annotations_template.csv", index=False, mode="x")
    mapping_columns = ["review_id", "unit", "unit_key", "account_number", "transaction_ref", "transaction_date", "amount", "score", "threshold", "flagged", "small", "stratum", "population_size", "sample_size", "inclusion_probability", "sampling_weight"]
    sampled[mapping_columns].to_csv(out / "sealed_mapping.csv", index=False, mode="x")
    population.to_parquet(out / "sealed_population.parquet", index=False)
    instructions = ("# Offline blinded behavioural review\n\n"
                    "Give the reviewer only packet.jsonl and a COPY of annotations_template.csv. Keep all sealed_* files, protocol and model metadata away from the reviewer until review is complete.\n\n"
                    "Read the current observation and available prior context. Set NORMAL, ANOMALOUS or INSUFFICIENT_EVIDENCE; fill reviewer_id, reason and an offset-aware reviewed_at timestamp. Leave unresolved cases PENDING. domain_outcome must remain UNKNOWN. Unusual behaviour is not confirmed fraud or money laundering.\n\n"
                    "Keep the immutable original template unchanged. Save responses separately and import them to a NEW output directory with the review_labels import CLI. No application alert disposition is changed.\n")
    (out / "REVIEW_INSTRUCTIONS.md").write_text(instructions, encoding="utf-8")
    manifest = {"version": VERSION, "sampled_units": len(sampled), "pending": len(sampled), "completed": 0,
                "files": {name: sha256(out / name) for name in ("protocol.json", "packet.jsonl", "annotations_template.csv", "sealed_mapping.csv", "sealed_population.parquet", "REVIEW_INSTRUCTIONS.md")},
                "input_sha256": input_hashes, "code_sha256": export_code_sha, "caveat": CAVEAT}
    write_json(out / "manifest.json", manifest)
    if sha256(Path(__file__)) != export_code_sha:
        raise ValueError("Review export code changed during packet export")
    return manifest


def verify_packet(packet_dir: Path) -> dict:
    packet_dir = Path(packet_dir)
    manifest = json.loads((packet_dir / "manifest.json").read_text())
    if manifest.get("version") != VERSION or not isinstance(manifest.get("files"), dict):
        raise ValueError("Unsupported review packet manifest")
    expected = {"protocol.json", "packet.jsonl", "annotations_template.csv", "sealed_mapping.csv", "sealed_population.parquet", "REVIEW_INSTRUCTIONS.md"}
    if set(manifest["files"]) != expected:
        raise ValueError("Review packet manifest has unexpected files")
    for name, digest in manifest["files"].items():
        if sha256(packet_dir / name) != digest:
            raise ValueError(f"Review packet integrity failure: {name}")
    return manifest


def validate_responses(packet_dir: Path, responses: Path) -> tuple[pd.DataFrame, dict]:
    """Read user-supplied judgments without fitting or inventing labels."""
    packet_dir, responses = Path(packet_dir), Path(responses)
    verify_packet(packet_dir)
    data = pd.read_csv(responses, dtype=str, keep_default_na=False)
    if set(data) != set(ANNOTATIONS) or data.review_id.duplicated().any():
        raise ValueError("Responses require exactly the annotation schema and unique review IDs")
    template = pd.read_csv(packet_dir / "annotations_template.csv", dtype=str, keep_default_na=False)
    if set(data.review_id) != set(template.review_id):
        raise ValueError("Responses must contain exactly the exported review IDs, including pending cases")
    if not data.status.isin(STATUSES).all() or not data.domain_outcome.eq("UNKNOWN").all():
        raise ValueError("Only defined review statuses and UNKNOWN domain outcomes are supported")
    for row in data.itertuples():
        if row.status == "PENDING":
            if any((row.reason.strip(), row.reviewer_id.strip(), row.reviewed_at.strip())):
                raise ValueError("Pending cases cannot contain completed-review metadata")
            continue
        if len(row.reason.strip()) < 8 or not row.reviewer_id.strip() or not row.reviewed_at.strip():
            raise ValueError("Completed reviews require reviewer ID, substantive reason and timestamp")
        try:
            timestamp = datetime.fromisoformat(row.reviewed_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Review timestamps must be ISO-8601 with a UTC offset") from exc
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("Review timestamps require an explicit UTC offset")
    mapping = pd.read_csv(packet_dir / "sealed_mapping.csv", keep_default_na=False)
    joined = mapping.merge(data, on="review_id", validate="one_to_one")
    if not joined.flagged.map(lambda value: isinstance(value, (bool, np.bool_))).all():
        raise ValueError("Sealed mapping flag values are invalid")
    protocol = json.loads((packet_dir / "protocol.json").read_text())
    return joined, protocol


def evaluate_responses(joined: pd.DataFrame, protocol: dict) -> dict:
    """Design-weighted anomaly agreement; unresolved cases never become 0s.

    A population precision estimate and finite-population SRS variance are
    available only when every sampled flagged case has a resolved judgement.
    With incomplete review, the resolved-only weighted ratio is descriptive;
    missing-review bounds and coverage expose its selection uncertainty.
    """
    result = {"caveat": CAVEAT, "fraud_precision": None, "aml_crime_precision": None,
              "unit_results": {}, "status_counts": joined.status.value_counts().to_dict()}
    for unit in ("AML", "FRAUD"):
        group = joined[joined.unit.eq(unit)]
        flagged = group[group.flagged]
        resolved = flagged[flagged.status.isin(RESOLVED)]
        total_weight = float(flagged.sampling_weight.sum())
        resolved_weight = float(resolved.sampling_weight.sum())
        anomaly_weight = float(resolved.loc[resolved.status.eq("ANOMALOUS"), "sampling_weight"].sum())
        population_flagged = sum(item["population_size"] for item in protocol["strata"].values() if item["unit"] == unit and item["flagged"])
        eligible_estimate = bool(len(flagged) and len(resolved) == len(flagged))
        variance = 0.0
        concentration_interval = None
        variance_available = eligible_estimate
        if eligible_estimate:
            for stratum, rows in flagged.groupby("stratum"):
                n = len(rows)
                total = int(protocol["strata"][stratum]["population_size"])
                if n == total:
                    continue
                if n < 2:
                    variance_available = False
                    continue
                y = rows.status.eq("ANOMALOUS").to_numpy(dtype=float)
                variance += total ** 2 * (1 - n / total) * float(np.var(y, ddof=1)) / n
        if eligible_estimate and population_flagged:
            # Hoeffding bound remains valid for SRS without replacement.
            # A Bonferroni union bound across flagged strata avoids a zero-width
            # confidence interval merely because a small sample contains no positives.
            count = flagged.stratum.nunique()
            low, high = 0.0, 0.0
            for stratum, rows in flagged.groupby("stratum"):
                total = int(protocol["strata"][stratum]["population_size"])
                mean = float(rows.status.eq("ANOMALOUS").mean())
                epsilon = 0.0 if len(rows) == total else math.sqrt(math.log(2 * count / .05) / (2 * len(rows)))
                low += total / population_flagged * max(0.0, mean - epsilon)
                high += total / population_flagged * min(1.0, mean + epsilon)
            concentration_interval = [low, high]
        estimate = anomaly_weight / population_flagged if eligible_estimate and population_flagged else None
        stderr = math.sqrt(variance) / population_flagged if variance_available and population_flagged else None
        bounds = None if total_weight == 0 else [anomaly_weight / total_weight, (anomaly_weight + total_weight - resolved_weight) / total_weight]
        result["unit_results"][unit] = {"sampled": len(group), "status_counts": group.status.value_counts().to_dict(),
            "unresolved_sampled": int((~group.status.isin(RESOLVED)).sum()),
            "unresolved_sample_fraction": float((~group.status.isin(RESOLVED)).mean()) if len(group) else None,
            "flagged_sampled": len(flagged), "flagged_population": int(population_flagged), "flagged_resolved": len(resolved),
            "resolved_flagged_weight_fraction": resolved_weight / total_weight if total_weight else None,
            "unresolved_flagged_weight_fraction": 1 - resolved_weight / total_weight if total_weight else None,
            "weighted_anomalous_share_of_resolved_flags": anomaly_weight / resolved_weight if resolved_weight else None,
            "estimated_population_anomaly_precision": estimate,
            "conservative_95pct_sampling_interval": concentration_interval,
            "approximate_95pct_design_interval": [max(0.0, estimate - 1.96 * stderr), min(1.0, estimate + 1.96 * stderr)] if stderr is not None else None,
            "unresolved_review_bounds_on_sample_weighted_anomaly_share": bounds,
            "nonflag_sampled": int((~group.flagged).sum()), "nonflag_resolved": int((~group.flagged & group.status.isin(RESOLVED)).sum()),
            "uncertainty": "The conservative interval uses stratum-wise Hoeffding bounds with Bonferroni correction for 95% sampling coverage and remains nondegenerate for zero observed anomalies. Approximate normal design interval assumes fixed reviewer judgements and stratified SRS; it excludes reviewer error and source-population transfer. With unresolved flagged reviews no population point estimate is reported; sample-weighted missing-review bounds are not a population confidence interval."}
    return result


def import_reviews(packet_dir: Path, responses: Path, out: Path) -> dict:
    out = Path(out)
    if out.exists():
        raise FileExistsError(f"Refusing to overwrite imported reviews: {out}")
    joined, protocol = validate_responses(packet_dir, responses)
    report = evaluate_responses(joined, protocol)
    report["provenance"] = {"packet_manifest_sha256": sha256(Path(packet_dir) / "manifest.json"),
                            "responses_sha256": sha256(Path(responses)), "import_code_sha256": sha256(Path(__file__))}
    out.mkdir(parents=True)
    joined.to_csv(out / "sealed_validated_reviews.csv", index=False, mode="x")
    write_json(out / "report.json", report)
    write_json(out / "manifest.json", {"version": VERSION, "files": {name: sha256(out / name) for name in ("sealed_validated_reviews.csv", "report.json")}, **report["provenance"]})
    return report


def export_source_validation(source: Path, model_root: Path, frozen_manifest: Path, out: Path, *, sample_size=300, seed=20261020) -> dict:
    """Score original validation parents with an already frozen DRAFT model."""
    from .experiments import verify_frozen
    from .features import fit_vocabulary
    from .generate import source_partitions
    from .model import predict_model
    from .research_features import build_experiment_features

    source, model_root, frozen_manifest = Path(source).resolve(), Path(model_root).resolve(), Path(frozen_manifest).resolve()
    frozen = json.loads(frozen_manifest.read_text())
    verify_frozen(frozen)
    protected = frozen.get("files", {})
    hashes = {"source": sha256(source), "frozen_manifest": sha256(frozen_manifest)}
    if protected.get(str(source)) != hashes["source"]:
        raise ValueError("Source is not protected by the supplied frozen research manifest")
    initial_code_sha = sha256(Path(__file__))
    observations, _ = load_observations(source, "kaggle_bank_v1")
    partitions = source_partitions(source)
    validation = observations[observations.account_number.isin(partitions["validation"])].copy()
    training = observations[observations.account_number.isin(partitions["train"])].copy()
    cutoff = fit_vocabulary(training)["small_amount_threshold"]
    scored, metadata = {}, {}
    for unit in ("AML", "FRAUD"):
        model_path, card_path = model_root / unit.lower() / "model.txt", model_root / unit.lower() / "model_card.json"
        if protected.get(str(model_path)) != sha256(model_path) or protected.get(str(card_path)) != sha256(card_path):
            raise ValueError("Review model/card must be protected by the supplied frozen manifest")
        card = json.loads(card_path.read_text())
        if card.get("state") != "DRAFT" or card.get("unit") != unit or card.get("targets_used") is not False or card.get("specification", {}).get("representation") != "full":
            raise ValueError("This export expects frozen unsupervised full-view DRAFT research cards")
        features = build_experiment_features(validation, unit, card["vocabulary"], "full")
        scores = predict_model(model_path.read_text(), features)
        keys = ["transaction_ref"] if unit == "FRAUD" else ["account_number", "as_of"]
        scored[unit] = features[keys].assign(score=scores, threshold=card["threshold_candidates"]["auto"])
        hashes[f"{unit}_model"], hashes[f"{unit}_card"] = sha256(model_path), sha256(card_path)
        metadata[unit] = {"state": card["state"], "unit": unit, "threshold": card["threshold_candidates"]["auto"], "model_sha256": hashes[f"{unit}_model"], "targets_used": False}
    verify_frozen(frozen)
    if sha256(Path(__file__)) != initial_code_sha:
        raise ValueError("Review source-scoring code changed before export")
    manifest = export_review_packet(validation, scored, out, sample_size=sample_size, seed=seed,
                                    small_amount_cutoff=cutoff, input_hashes=hashes, model_metadata=metadata)
    verify_frozen(frozen)
    if sha256(Path(__file__)) != initial_code_sha or sha256(frozen_manifest) != hashes["frozen_manifest"]:
        raise ValueError("Review code or frozen manifest changed during export")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("--source", type=Path, required=True)
    export.add_argument("--models", type=Path, required=True)
    export.add_argument("--frozen", type=Path, required=True)
    export.add_argument("--out", type=Path, required=True)
    export.add_argument("--sample-size", type=int, default=300)
    export.add_argument("--seed", type=int, default=20261020)
    imported = commands.add_parser("import")
    imported.add_argument("--packet", type=Path, required=True)
    imported.add_argument("--responses", type=Path, required=True)
    imported.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "export":
        result = export_source_validation(args.source, args.models, args.frozen, args.out, sample_size=args.sample_size, seed=args.seed)
    else:
        result = import_reviews(args.packet, args.responses, args.out)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
