"""Offline reviewer UI, blinded inputs and exact manual CSV projection."""
import csv
import io
import json
import re
import shutil
import subprocess

import pandas as pd
import pytest

from app.ml.behavioral import review_html
from app.ml.behavioral.review_labels import ANNOTATIONS
from test_review_labels import inputs, export


def embedded(html):
    match = re.search(r'<script type="application/json" id="review-data">(.*?)</script>', html, re.S)
    assert match
    return json.loads(match[1])


def test_html_embeds_only_visible_cases_and_pending_rows(inputs, tmp_path):
    packet, output = tmp_path / "packet", tmp_path / "ui"
    export(inputs, packet)
    original = {p.name: p.read_bytes() for p in packet.iterdir()}
    manifest = review_html.generate_review_html(packet, output)
    html = (output / "review.html").read_text()
    payload = embedded(html)
    assert manifest["cases"] == 40 and manifest["completed_reviews"] == 0
    assert len(payload["cases"]) == len(payload["annotations"]) == 40
    assert payload["annotations"] == pd.read_csv(packet / "annotations_template.csv", dtype=str, keep_default_na=False).to_dict(orient="records")
    assert all(row["status"] == "PENDING" and row["domain_outcome"] == "UNKNOWN" for row in payload["annotations"])
    review_html._check_visible(payload["cases"])
    assert "sealed_mapping" not in html and "sampling_weight" not in html
    assert "fetch(" not in html and "XMLHttpRequest" not in html and "localStorage" not in html
    assert "connect-src 'none'" in html
    assert {p.name: p.read_bytes() for p in packet.iterdir()} == original
    with pytest.raises(FileExistsError):
        review_html.generate_review_html(packet, output)


def test_script_escaping_prevents_html_and_script_breakout():
    attack = '</script><img src=x onerror="alert(1)"> & <script>\u2028'
    encoded = review_html._json_script({"reason": attack})
    assert "<" not in encoded and ">" not in encoded and "&" not in encoded
    assert json.loads(encoded)["reason"] == attack
    assert "innerHTML" not in review_html.HTML


def test_packet_tampering_prevents_ui_export(inputs, tmp_path):
    packet = tmp_path / "packet"
    export(inputs, packet)
    with (packet / "packet.jsonl").open("a") as handle:
        handle.write("\n")
    with pytest.raises(ValueError, match="integrity"):
        review_html.generate_review_html(packet, tmp_path / "ui")
    assert not (tmp_path / "ui").exists()


def test_prohibited_metadata_in_packet_rejected():
    with pytest.raises(ValueError, match="prohibited"):
        review_html._check_visible([{"observations": [{"amount": 1, "score": .6}]}])


@pytest.mark.skipif(not shutil.which("node"), reason="Node unavailable for exact browser helper checks")
def test_js_exports_every_case_with_exact_schema_and_valid_manual_review(tmp_path):
    # Private fixture judgements, never imported into the actual source packet.
    script = review_html.EXPORT_JS + r'''
const cases=[{review_id:"one"},{review_id:"two"},{review_id:"three"}];
const responses={one:{review_id:"one",status:"ANOMALOUS",reason:'Observed "device" change, investigate\nmore',reviewer_id:"analyst-1",reviewed_at:"2026-10-20T09:00:00+07:00",domain_outcome:"UNKNOWN"}};
process.stdout.write(reviewCsv(cases,responses));
'''
    result = subprocess.run([shutil.which("node"), "-e", script], text=True, capture_output=True, check=True)
    rows = list(csv.DictReader(io.StringIO(result.stdout)))
    assert list(rows[0]) == ANNOTATIONS
    assert len(rows) == 3 and [row["review_id"] for row in rows] == ["one", "two", "three"]
    assert rows[0]["status"] == "ANOMALOUS" and '"device"' in rows[0]["reason"]
    assert all(row["domain_outcome"] == "UNKNOWN" for row in rows)
    assert all(row["status"] == "PENDING" and row["reason"] == row["reviewer_id"] == row["reviewed_at"] == "" for row in rows[1:])


@pytest.mark.skipif(not shutil.which("node"), reason="Node unavailable for browser helper checks")
@pytest.mark.parametrize("mutation", ["status", "outcome", "timestamp", "reason", "reviewer", "pending_metadata"])
def test_js_rejects_invalid_or_automatic_completed_labels(mutation):
    script = review_html.EXPORT_JS + r'''
const row={review_id:"one",status:"NORMAL",reason:"Reviewed visible context",reviewer_id:"analyst",reviewed_at:"2026-10-20T09:00:00Z",domain_outcome:"UNKNOWN"};
const mutation=process.argv[1];
if(mutation==="status")row.status="CONFIRMED_FRAUD";
if(mutation==="outcome")row.domain_outcome="CONFIRMED_FRAUD";
if(mutation==="timestamp")row.reviewed_at="2026-10-20T09:00:00";
if(mutation==="reason")row.reason="short";
if(mutation==="reviewer")row.reviewer_id="";
if(mutation==="pending_metadata")row.status="PENDING";
try{reviewCsv([{review_id:"one"}],{one:row});process.exit(2);}catch(error){process.stdout.write(error.message);}
'''
    result = subprocess.run([shutil.which("node"), "-e", script, mutation], text=True, capture_output=True, check=True)
    assert result.stdout
