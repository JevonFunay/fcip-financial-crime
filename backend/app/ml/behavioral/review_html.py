"""Standalone offline HTML for a verified blinded behavioural review packet.

The tool embeds only reviewer-visible observations and the pending annotation
schema. It has no networking, persistent browser storage, model fitting,
application database or crime-confirmation path. CSV export requires a user
click; selecting and saving judgements is an explicit human action.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .pipeline import sha256, write_json
from .review_labels import ANNOTATIONS, verify_packet

VERSION = "offline_blinded_review_html_v1"
PROHIBITED = {"score", "threshold", "flagged", "small", "stratum", "label", "scenario", "sampling_weight", "inclusion_probability"}


def _check_visible(value):
    if isinstance(value, dict):
        if PROHIBITED.intersection(value):
            raise ValueError("The reviewer-visible packet contains prohibited evaluation metadata")
        for child in value.values():
            _check_visible(child)
    elif isinstance(value, list):
        for child in value:
            _check_visible(child)


def _json_script(value) -> str:
    # Escaping '<' prevents an embedded </script> from closing the data block.
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


# Exported as a separate constant so the exact CSV projection can be tested
# with Node without browser automation or executing any private case data.
EXPORT_JS = r'''
const REVIEW_COLUMNS = ["review_id","status","reason","reviewer_id","reviewed_at","domain_outcome"];
const REVIEW_STATUSES = new Set(["PENDING","NORMAL","ANOMALOUS","INSUFFICIENT_EVIDENCE"]);
function pendingRow(id) {
  return {review_id:id,status:"PENDING",reason:"",reviewer_id:"",reviewed_at:"",domain_outcome:"UNKNOWN"};
}
function validateRow(row) {
  if (!REVIEW_STATUSES.has(row.status) || row.domain_outcome !== "UNKNOWN") throw new Error("Unknown review status or domain outcome.");
  if (row.status === "PENDING") {
    if (row.reason || row.reviewer_id || row.reviewed_at) throw new Error("Pending cases must have empty review metadata.");
  } else {
    if (row.reason.trim().length < 8 || !row.reviewer_id.trim()) throw new Error("Saved reviews require a reviewer ID and reason of at least 8 characters.");
    if (!/(Z|[+-]\d{2}:\d{2})$/.test(row.reviewed_at) || !Number.isFinite(Date.parse(row.reviewed_at))) throw new Error("Saved reviews require an ISO timestamp with an explicit UTC offset.");
  }
  return row;
}
function csvCell(value) { return '"' + String(value).replaceAll('"','""') + '"'; }
function reviewCsv(cases, responses) {
  if (new Set(cases.map(item=>item.review_id)).size !== cases.length) throw new Error("Duplicate case IDs.");
  const rows = cases.map(item => validateRow(responses[item.review_id] || pendingRow(item.review_id)));
  return REVIEW_COLUMNS.join(",") + "\r\n" + rows.map(row=>REVIEW_COLUMNS.map(key=>csvCell(row[key])).join(",")).join("\r\n") + "\r\n";
}
'''

HTML = r'''<!doctype html>
<html lang="id"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'none'; img-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'">
<title>FCIP · Review perilaku offline</title><style>
:root{font-family:system-ui,sans-serif;color:#163139;background:#f2f6f5}*{box-sizing:border-box}body{margin:0}header{background:#123f40;color:white;padding:25px max(20px,calc((100% - 1240px)/2))}h1{font-size:24px;margin:0 0 8px}p{line-height:1.5;margin:8px 0}main{max-width:1280px;margin:auto;padding:22px}.card{background:white;border:1px solid #d6e1df;border-radius:12px;padding:20px;margin-bottom:16px}.notice{background:#fff7df;border:1px solid #e9d59e;padding:14px;border-radius:8px;font-size:14px}label{display:block;font-size:14px;font-weight:600;margin-bottom:6px}input,textarea,select,button{font:inherit;border:1px solid #a9bdb8;border-radius:7px;padding:10px}input,textarea,select{width:100%;background:white;color:#163139}textarea{min-height:100px}button{background:#116b63;color:white;border-color:#116b63;cursor:pointer}button.secondary{background:white;color:#163139;border-color:#a9bdb8}button:disabled{opacity:.45;cursor:default}.row{display:flex;gap:14px;flex-wrap:wrap;align-items:end}.grow{flex:1;min-width:220px}.muted{color:#56716f;font-size:13px}.pill{display:inline-block;background:#e3efeb;border-radius:20px;padding:5px 10px;font-size:12px;margin-right:8px}.table-wrap{overflow:auto;max-height:460px;border:1px solid #e1e8e5;border-radius:7px}table{border-collapse:collapse;width:100%;font-size:12px}th,td{text-align:left;vertical-align:top;border-bottom:1px solid #e2e9e6;padding:9px;white-space:nowrap}th{background:#edf4f0;position:sticky;top:0}h2{font-size:18px}h3{font-size:15px}details summary{cursor:pointer;font-weight:600;padding:10px 0}#message{min-height:22px;color:#8b3022;font-weight:600;line-height:1.4}.help{font-size:13px;line-height:1.55}nav{display:flex;gap:8px;flex-wrap:wrap}footer{font-size:12px;color:#56716f;padding:0 20px 24px}code{font-family:ui-monospace,monospace;overflow-wrap:anywhere}button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{outline:3px solid #c79130;outline-offset:2px}@media(max-width:600px){main{padding:12px}.card{padding:14px}header{padding:20px}h1{font-size:21px}}
</style></head><body><header><h1>Review perilaku transaksi</h1><p>FCIP · Offline · Penilaian berdasarkan bukti yang terlihat</p></header><main>
<section class="card"><div class="notice"><strong>Belum ada label fraud terverifikasi.</strong> NORMAL berarti tidak terlihat kejanggalan dalam konteks yang tersedia. ANOMALOUS berarti perlu investigasi, bukan fraud atau pencucian uang terkonfirmasi. Pilih INSUFFICIENT_EVIDENCE jika datanya tidak cukup.</div>
<p class="help">Sebagian tanggal hanya memiliki presisi hari; timezone sumber tidak diketahui. Perangkat belum tentu tepercaya, identitas lawan transaksi tidak tersedia, dan tidak ada hasil investigasi bank. Jangan menebak informasi tersebut.</p>
<p class="help"><strong>Data hanya ada di memori tab ini.</strong> Tidak ada jaringan atau penyimpanan browser. Unduh CSV secara berkala; menutup atau memuat ulang tab akan menghapus penilaian yang ada di tab. File CSV lengkap menyertakan kasus PENDING untuk menjaga cakupan sampel.</p>
<div class="row"><div class="grow"><label for="reviewer">ID reviewer</label><input id="reviewer" autocomplete="off" placeholder="Contoh: analyst-01"></div><div><button type="button" id="export">Unduh seluruh hasil CSV</button></div></div><p id="progress" class="muted"></p>
</section>
<section class="card"><div class="row"><div class="grow"><h2 id="caseTitle"></h2><p id="caseMeta" class="muted"></p></div><nav><button type="button" class="secondary" id="previous">Sebelumnya</button><button type="button" class="secondary" id="next">Berikutnya</button></nav></div>
<div class="row"><div class="grow"><label for="jump">Pilih kasus</label><select id="jump"></select></div></div><p id="scope" class="help"></p><h3>Observasi yang dinilai</h3><div id="current" class="table-wrap"></div><details><summary id="historyTitle">Konteks sebelumnya</summary><p id="historyHelp" class="muted"></p><div id="history" class="table-wrap"></div></details>
</section>
<section class="card"><form id="reviewForm"><h2>Penilaian reviewer</h2><label for="status">Status</label><select id="status"><option value="PENDING">PENDING — belum dinilai</option value="NORMAL">NORMAL — tidak terlihat kejanggalan</option><option value="ANOMALOUS">ANOMALOUS — terlihat kejanggalan</option><option value="INSUFFICIENT_EVIDENCE">INSUFFICIENT_EVIDENCE — bukti belum cukup</option></select><p class="help">Status tetap PENDING sampai Anda memilih status dan menekan Simpan. Outcome fraud/AML selalu UNKNOWN pada paket ini.</p><label for="reason">Alasan dan bukti yang digunakan (minimal 8 karakter untuk penilaian)</label><textarea id="reason" placeholder="Jelaskan pola yang terlihat atau bukti yang belum tersedia."></textarea><p id="savedMeta" class="muted"></p><div class="row"><button type="submit">Simpan penilaian</button><button type="button" class="secondary" id="saveNext">Simpan &amp; berikutnya</button></div><p id="message" role="status" aria-live="polite"></p></form></section>
</main><footer>Penilaian offline ini tidak mengubah alert, model, atau database aplikasi. Hasilnya mendeskripsikan kesepakatan reviewer atas anomali yang terlihat, bukan akurasi deteksi kejahatan.</footer>
<script type="application/json" id="review-data">__DATA__</script>
<script>__EXPORT_JS__
const payload = JSON.parse(document.getElementById("review-data").textContent);
const cases = payload.cases;
const responses = Object.fromEntries(payload.annotations.map(row=>[row.review_id,row]));
const byId = id=>document.getElementById(id);
let index=0, dirty=false;
const fields = ["transaction_ref","account_number","transaction_date","timestamp_precision","source_timezone_known","amount","currency","direction","channel","login_attempts","transaction_duration","account_balance","device_ref","ip_address","merchant_ref","location"];
function textNode(tag,text){const element=document.createElement(tag);element.textContent=text;return element;}
function table(id,rows){const root=byId(id);root.replaceChildren();if(!rows.length){root.append(textNode("p","Tidak ada observasi sebelumnya yang tersedia."));return;}const table=document.createElement("table"),head=document.createElement("thead"),hr=document.createElement("tr"),body=document.createElement("tbody");const shown=fields.filter(key=>rows.some(row=>Object.hasOwn(row,key)));shown.forEach(key=>hr.append(textNode("th",key)));head.append(hr);rows.forEach(row=>{const tr=document.createElement("tr");shown.forEach(key=>tr.append(textNode("td",row[key]===null||row[key]===undefined||row[key]===""?"—":String(row[key]))));body.append(tr);});table.append(head,body);root.append(table);}
function progress(){const saved=cases.filter(item=>responses[item.review_id].status!=="PENDING").length;byId("progress").textContent=`${saved} / ${cases.length} penilaian tersimpan · ${cases.length-saved} PENDING`;}
function show(){const item=cases[index],row=responses[item.review_id];byId("caseTitle").textContent=`Kasus ${index+1} dari ${cases.length} · ${item.unit}`;byId("caseMeta").textContent=`Review ID: ${item.review_id}`;byId("scope").textContent=item.observation_unit+". "+item.available_evidence;byId("status").value=row.status;byId("reason").value=row.reason;byId("savedMeta").textContent=row.status==="PENDING"?"Belum ada penilaian tersimpan.":`Tersimpan oleh ${row.reviewer_id} · ${row.reviewed_at}`;byId("historyTitle").textContent=`Konteks sebelumnya (${item.prior_context.length} ditampilkan / ${item.strictly_prior_observations_count} tersedia)`;byId("historyHelp").textContent=item.context_limit;table("current",item.observations);table("history",item.prior_context);byId("previous").disabled=index===0;byId("next").disabled=index===cases.length-1;byId("jump").value=String(index);byId("message").textContent="";dirty=false;progress();}
function move(to){if(to<0||to>=cases.length)return;if(dirty&&!window.confirm("Perubahan belum disimpan. Pindah dan abaikan perubahan?")){byId("jump").value=String(index);return;}index=to;show();}
function save(){const id=cases[index].review_id,status=byId("status").value;let row=pendingRow(id);if(status!=="PENDING"){row={review_id:id,status,reason:byId("reason").value.trim(),reviewer_id:byId("reviewer").value.trim(),reviewed_at:new Date().toISOString(),domain_outcome:"UNKNOWN"};}try{validateRow(row);}catch(error){byId("message").textContent=error.message;return false;}responses[id]=row;show();byId("message").textContent=status==="PENDING"?"Kasus disimpan sebagai PENDING; metadata penilaian dikosongkan.":"Penilaian tersimpan di memori tab. Unduh CSV untuk menyimpan berkas.";return true;}
cases.forEach((item,i)=>{const option=textNode("option",`${i+1} · ${item.unit} · ${item.review_id}`);option.value=String(i);byId("jump").append(option);});
byId("reviewForm").addEventListener("submit",event=>{event.preventDefault();save();});byId("saveNext").addEventListener("click",()=>{if(save()&&index<cases.length-1){index++;show();}});byId("previous").addEventListener("click",()=>move(index-1));byId("next").addEventListener("click",()=>move(index+1));byId("jump").addEventListener("change",event=>move(Number(event.target.value)));["status","reason"].forEach(id=>byId(id).addEventListener("input",()=>{dirty=true;}));
byId("export").addEventListener("click",()=>{if(dirty){byId("message").textContent="Simpan atau batalkan perubahan pada kasus ini sebelum mengunduh CSV.";byId("message").scrollIntoView({block:"center"});return;}try{const csv=reviewCsv(cases,responses),url=URL.createObjectURL(new Blob([csv],{type:"text/csv;charset=utf-8"})),link=document.createElement("a");link.href=url;link.download="fcip_review_responses.csv";document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);byId("message").textContent=`CSV lengkap ${cases.length} baris diunduh. Simpan berkas ini untuk import offline.`;}catch(error){byId("message").textContent=error.message;}});
window.addEventListener("beforeunload",event=>{if(dirty||cases.some(item=>responses[item.review_id].status!=="PENDING")){event.preventDefault();event.returnValue="";}});
show();
</script></body></html>'''


def generate_review_html(packet_dir: Path, out: Path) -> dict:
    packet_dir, out = Path(packet_dir), Path(out)
    if out.exists():
        raise FileExistsError(f"Refusing to overwrite offline review UI: {out}")
    initial_manifest = sha256(packet_dir / "manifest.json")
    verify_packet(packet_dir)
    initial_code = sha256(Path(__file__))
    visible_hashes = {name: sha256(packet_dir / name) for name in ("packet.jsonl", "annotations_template.csv")}
    cases = [json.loads(line) for line in (packet_dir / "packet.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    annotations = pd.read_csv(packet_dir / "annotations_template.csv", dtype=str, keep_default_na=False)
    if not cases or set(annotations) != set(ANNOTATIONS) or annotations.review_id.duplicated().any():
        raise ValueError("Review template requires exact annotation columns and unique case IDs")
    ids = [item["review_id"] for item in cases]
    if len(set(ids)) != len(ids) or set(ids) != set(annotations.review_id):
        raise ValueError("Reviewer cases and annotation IDs must align exactly")
    if not annotations.status.eq("PENDING").all() or not annotations.domain_outcome.eq("UNKNOWN").all() or not annotations[["reason", "reviewer_id", "reviewed_at"]].eq("").all().all():
        raise ValueError("Initial UI must contain pending, UNKNOWN-only blank annotations")
    _check_visible(cases)
    payload = {"cases": cases, "annotations": annotations.to_dict(orient="records")}
    html = HTML.replace("__EXPORT_JS__", EXPORT_JS).replace("__DATA__", _json_script(payload))
    verify_packet(packet_dir)
    if sha256(packet_dir / "manifest.json") != initial_manifest or sha256(Path(__file__)) != initial_code:
        raise ValueError("Input manifest or review UI code changed during generation")
    out.mkdir(parents=True)
    with (out / "review.html").open("x", encoding="utf-8") as handle:
        handle.write(html)
    manifest = {"version": VERSION, "cases": len(cases), "completed_reviews": 0, "packet_manifest_sha256": initial_manifest,
                "visible_input_sha256": visible_hashes, "code_sha256": initial_code, "review_html_sha256": sha256(out / "review.html"),
                "data_scope": "Reviewer-visible cases and blank annotation template only; no sealed score, flag, stratum or population mapping embedded.",
                "network": "None; external assets and connections blocked by CSP.", "persistence": "Tab memory only; CSV export requires a user click. Closing or reloading loses in-tab annotations.",
                "label_scope": "Human observational anomaly review only; no verified fraud or money-laundering targets. All cases initially PENDING/UNKNOWN."}
    write_json(out / "manifest.json", manifest)
    verify_packet(packet_dir)
    if sha256(packet_dir / "manifest.json") != initial_manifest or sha256(Path(__file__)) != initial_code:
        raise ValueError("Input manifest or review UI code changed during export")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(generate_review_html(args.packet, args.out), indent=2))


if __name__ == "__main__":
    main()
