# Upload-to-results mentor mockup

Approved by Jevon on 7 October 2026. This is an interactive design review of
the proposed product journey, not a production ingestion or scoring endpoint.

Open `fcip-upload-results-preview.html` in a browser. It is a self-contained
offline preview; it can be shared with the mentor without running Docker. The editable
source is the `fcip-upload-results.html` fragment, also displayed in the
conversation. Both files belong in the same commit when the source changes.
Rebuild the standalone preview with `python3 docs/mockups/build_preview.py`
from the repository root. The renderer uses only Python's standard library.

## Review the journey

1. Start as **Data Ops**, and choose **Simulasikan upload Small**. The proposed
   validation, ingestion and scoring steps lead to the dashboard. **Lihat
   contoh paket ditolak** demonstrates rejecting a ground-truth file; it does
   not inspect a real archive.
2. Use **Buka antrean alert**. Filter by business domain, active pattern and
   status, then choose **Investigasi**.
3. Switch the demo role to **Triage Analyst**. Enter a reason of at least 20
   trimmed characters and try false positive or escalation. Only an escalated
   alert can open a demo case. Decisions and cases stay inside the mockup.
4. Open **Sinyal model**. Switch between AML entity-weeks and Fraud
   transactions. Model-only signals have no disposition action while
   `ML_SCORE` is DRAFT.

There is no upload, API request, training, transaction block or production
decision. Local interaction state may be remembered by the conversation; the
offline preview resets on reload. No free-text reasons are persisted.

## Fixture provenance

All data is synthetic. Every score, chart, model-flag count, entity reference,
transaction detail and SHAP contribution in this mockup is an **illustrative
design fixture**, not a measured model output. Demo thresholds on the model
page are illustrative too; they do not configure the trained models.

The four Small ingestion/detection figures (20,916 source rows, 20,458
accepted, 458 quarantined and 2 P02 alerts) use the existing measured run in
`docs/PROJECT_CONTEXT.md` §5, "Synthetic raw dataset", and README's
"Loading it into the skeleton" section. The monthly bar heights are a made-up
allocation of the accepted count for layout review. The score distribution
shows only example **stored Fraud signals**, not the whole transaction
population. AML and Fraud flag counts have different units and are not added.

Actual ML volumes, from manifests, build reports and model cards verified on
7 October, are recorded in `docs/PROJECT_CONTEXT.md` §5:

| Dataset | Seed | Source transactions | Accepted transactions |
|---|---:|---:|---:|
| Training | 20260923 | 421,282 | 412,015 |
| Separate test holdout | 20261001 | 421,456 | 412,160 |

Fraud fit used 314,013 transactions and AML fit 113,340 entity-week units,
with internal validation kept separate. Both source datasets were generated
before fit; the test holdout was evaluated after training. Seed 20261001 is
not its generation date. After this mockup was completed, Jevon authorised
a fresh Full seed 20261007 generated on 7 October after fit, prepared for
manual CSV upload; see [the demo guide](../DEMO_FRESH_DATA.md).
Synthetic holdout performance is not a production-performance claim; see
`docs/ml/evaluation_v1.md` for label/unit recall, false positives and
leave-one-pattern-out limitations.

## Decisions for this design review

- Use one upload area and a combined, simulated processing journey. Package
  upload, automatic scoring/progress and the richer dashboard remain proposals.
  Existing production CSV uploads have a 50 MiB cap; the later fresh demo
  splits Full into two verified CSV uploads after CLI master-data preparation.
- Keep rule alerts and model-only signals on separate views, with CF-05(b)
  still DRAFT. Model names do not classify an item into a business domain or
  P-pattern. Business-domain filters operate on the known P02 rule.
- Carry permanent synthetic and signal captions, use descriptive indicators,
  and keep final decisions with humans.

References: FRD §3.2 (offline fit, no automated retraining), §4.1 (roles),
FR-406/BR-406 (fixed caption and descriptive language), L-01 (synthetic-data
limitation), NG-01 (advisory only); TRD §6.1 (CSV file drop and manifest),
§10.2 (CF-05), §10.6 (metric caveat). ZIP packaging follows the approved
option C in `docs/PROJECT_CONTEXT.md` §13; ZIP is not an explicit TRD format.
These sections were checked in the Indonesian PDFs during this review.

Mentor review should resolve the upload-to-results design before selecting
the production implementation stage. An actual background worker and
progress contract, package refresh/upsert policy, model approval and CF-05
activation are separate decisions.

## Verification

The mockup was exercised in headless Chrome: upload rejection, role controls,
processing completion, domain/status/model filters, required reason,
false-positive disposition, escalation and independent case creation for each
alert. Late conversation-state echoes were checked so older saved screens do
not override current navigation. The offline preview made no network requests.
Layout was checked
at 320, 390, 736 and 1,024 px, in light and dark appearance, with no page errors.
No automated test module was added for this reversible design prototype.

The unchanged production baseline was checked before the mockup: 406 regular
backend tests and 5 slow tests passed against isolated PostgreSQL; frontend
typecheck/build passed. The regular suite emitted dependency and numerical
comparator warnings. This review does not resolve those warnings or count
the mockup as an implemented functional requirement.
