"""The additive strict selector must reject FP increases, even with higher P."""
import json

import pytest

from app.ml.behavioral import aml_temporal_selection as strict
from app.ml.behavioral import aml_network_benchmark as old


def metrics(tp=10, fp=90):
    return {"tp": tp, "fp": fp, "precision": tp / (tp + fp), "low_amount_positive_tp": 3,
            "recipe_tp": {"cycle": 2}, "pattern_or_fragment_tp": {"cycle": 2}}


def test_higher_precision_does_not_allow_more_false_positives():
    result = strict.choose({"graph256__auto": metrics(), "more_precise_but_more_fp": metrics(tp=30, fp=100)})
    assert result["selected"] == "graph256__auto"
    assert result["rejected"]["more_precise_but_more_fp"] == ["false_positive_increase"]
    assert result["false_positive_cap"] == 90


def test_valid_improvement_keeps_coverage_and_reduces_fp():
    result = strict.choose({"graph256__auto": metrics(), "better": metrics(fp=50)})
    assert result["selected"] == "better"
    assert result["rejected"] == {}


def test_fp_and_tp_violations_both_reported():
    result = strict.choose({"graph256__auto": metrics(), "bad": metrics(tp=5, fp=100)})
    assert result["rejected"]["bad"] == ["overall_true_positive_loss", "false_positive_increase"]


def test_stored_validation_audit_does_not_read_targets_or_fit(tmp_path, monkeypatch):
    run = tmp_path / "run"
    run.mkdir()
    frozen = {"files": {}}
    old.write_json(run / "frozen.json", frozen)
    report = {"frozen": frozen, "frozen_manifest_sha256": old.sha256(run / "frozen.json"), "frozen_at": "2026-10-20T00:00:00Z",
              "validation": {"graph256__auto": metrics(), "loses_tp": metrics(tp=5, fp=30)},
              "selection": {"reference": "graph256__auto", "selected": "graph256__auto", "eligible": ["graph256__auto"]}}
    old.write_json(run / "report.json", report)
    monkeypatch.setattr(strict.temporal, "read_labels", lambda *a, **k: pytest.fail("No labels in postfreeze audit"))
    monkeypatch.setattr(strict.temporal, "fit_model", lambda *a, **k: pytest.fail("No fit in postfreeze audit"))
    result = strict.audit_completed_run(run, tmp_path / "audit")
    assert result["selected_option_unchanged"] is result["eligible_options_unchanged"] is True
    assert result["options_exceeding_reference_fp"] == []
    assert result["new_target_reads"] == result["new_model_fits"] == 0
    assert json.loads((tmp_path / "audit" / "report.json").read_text())["final_retuned"] is False


def test_audit_rejects_changed_frozen_inputs(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    protected = tmp_path / "protected"
    protected.write_text("original")
    frozen = {"files": {str(protected): old.sha256(protected)}}
    old.write_json(run / "frozen.json", frozen)
    old.write_json(run / "report.json", {"frozen": frozen, "frozen_manifest_sha256": old.sha256(run / "frozen.json")})
    protected.write_text("tampered")
    with pytest.raises(ValueError, match="Frozen benchmark"):
        strict.audit_completed_run(run, tmp_path / "audit")
    assert not (tmp_path / "audit").exists()
