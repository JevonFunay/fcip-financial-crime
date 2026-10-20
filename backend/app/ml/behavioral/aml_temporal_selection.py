"""Explicit false-positive cap for future temporal selection and frozen-run audit.

Added after the 20 October full-edge run had completed. This module preserves
that run's frozen code/protocol and never rebrands its final as unseen. The
completed run already has no FP-increasing validation options; this audit
makes that fact checkable without refitting or reading any target file.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import aml_network_benchmark as old
from . import aml_temporal as temporal


def choose(validation: dict[str, dict], *, reference: str = "graph256__auto") -> dict:
    """Maximize precision with reference TP guards AND no additional FP."""
    cap = validation[reference]["fp"]
    allowed = {name: values for name, values in validation.items() if values["fp"] <= cap}
    result = temporal.choose(allowed, reference=reference)
    previous_rejections = temporal.choose(validation, reference=reference)["rejected"]
    rejected = dict(previous_rejections)
    for name, values in validation.items():
        if values["fp"] > cap:
            rejected[name] = [*rejected.get(name, []), "false_positive_increase"]
    result["rejected"] = rejected
    result["false_positive_cap"] = cap
    result["guards"] = [*result["guards"], "overall FP <= reference"]
    return result


def audit_completed_run(run: Path, out: Path) -> dict:
    """Audit stored VALIDATION metrics only, with the completed run immutable."""
    if out.exists():
        raise FileExistsError("Choose a new audit output; prior evidence is immutable")
    report_path = run / "report.json"
    initial_report_hash = old.sha256(report_path)
    report = json.loads(report_path.read_text())
    frozen_path = run / "frozen.json"
    expected = {**report["frozen"]["files"],
                str(report_path.resolve()): initial_report_hash,
                str(frozen_path.resolve()): report["frozen_manifest_sha256"],
                str(Path(__file__).resolve()): old.sha256(Path(__file__))}
    old.verify_hashes(expected)
    validation = report["validation"]
    reference = report["selection"]["reference"]
    strict = choose(validation, reference=reference)
    original = report["selection"]
    result = {
        "audit_kind": "postfreeze stored-validation FP-cap audit; no new fit, score, target parse or final evaluation",
        "audited_at": temporal.now(), "run_frozen_at": report["frozen_at"],
        "disclosure": "Explicit FP cap was requested after this run completed. It was absent from its frozen choose() implementation; this audit is not retrospective predeclaration.",
        "source_report_sha256": initial_report_hash,
        "strict_selection": strict,
        "stored_selection": original["selected"],
        "selected_option_unchanged": strict["selected"] == original["selected"],
        "eligible_options_unchanged": set(strict["eligible"]) == set(original["eligible"]),
        "options_exceeding_reference_fp": [name for name, metrics in validation.items() if metrics["fp"] > validation[reference]["fp"]],
        "actual_selected_validation_fp_nonincrease": validation[original["selected"]]["fp"] <= validation[reference]["fp"],
        "final_retuned": False, "new_target_reads": 0, "new_model_fits": 0,
        "no_database_registration_or_promotion": True,
        "integrity": {"files": expected},
    }
    old.verify_hashes(expected)
    out.mkdir(parents=True)
    old.write_json(out / "report.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = audit_completed_run(args.run, args.out)
    print(json.dumps({"report": str(args.out / "report.json"), "selected_option_unchanged": result["selected_option_unchanged"],
                      "options_exceeding_reference_fp": result["options_exceeding_reference_fp"]}, indent=2))


if __name__ == "__main__":
    main()
