import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { errorMessage } from "../api/errors";
import { fetchModels, fetchScores } from "../api/ml";
import { Icon } from "../components/Icon";
import { ModelSignals } from "../components/ModelSignals";
import { SignalNotice } from "../components/Notices";
import type { ModelUnit, ModelVersionOut } from "../types";
import "../analysis.css";

const PAGE_SIZE = 50;
const MODEL_TABS: { value: ModelUnit | ""; label: string }[] = [
  { value: "", label: "All signals" },
  { value: "AML", label: "AML · Mingguan" },
  { value: "FRAUD", label: "Fraud · Transaction" },
];

function validationValue(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toFixed(3)
    : "—";
}

function validationPercent(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value)
    ? `${(100 * value).toLocaleString("id-ID", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%`
    : "—";
}

function ModelCard({ model }: { model: ModelVersionOut }) {
  const isWeekly = model.unit === "AML";
  const unsupervised =
    model.training_kind === "unsupervised" ||
    model.score_kind === "anomaly_score";
  const stress = model.validation.synthetic_stress;
  return (
    <article className="card analysis-model-card">
      <div className="analysis-model-heading">
        <span className="analysis-model-icon">
          <Icon name={isWeekly ? "layers" : "activity"} size={22} />
        </span>
        <div>
          <p className="eyebrow">{isWeekly ? "AML model" : "Fraud model"}</p>
          <h3>
            {isWeekly
              ? unsupervised
                ? "Weekly account signals"
                : "Weekly entity signals"
              : "Transaction signals"}
          </h3>
        </div>
        <span
          className={`badge analysis-version-state ${model.state === "ACTIVE" ? "analysis-version-active" : ""}`}
        >
          {model.state}
        </span>
      </div>
      <p className="analysis-model-description">
        {isWeekly
          ? unsupervised
            ? "Satu skor per rekening-proxy dan minggu kalender sumber."
            : "Satu skor per entitas dan minggu kalender."
          : "Satu skor per transaksi, menggunakan riwayat yang tersedia."}
        {unsupervised &&
          " Isolation Forest tanpa label; skor mengukur keterisolasian, bukan probabilitas fraud."}
      </p>
      {model.state === "DRAFT" && (
        <p className="analysis-model-description">
          Kandidat untuk ditinjau; belum digunakan untuk menilai upload.
        </p>
      )}
      <div className="analysis-model-values">
        <div>
          <span>Model version</span>
          <strong className="mono">{model.version}</strong>
        </div>
        <div>
          <span>Flag threshold</span>
          <strong className="analysis-threshold">
            {model.threshold.toFixed(4)}
          </strong>
        </div>
      </div>
      {unsupervised ? (
        <div className="analysis-validation">
          <p>
            Evaluasi tanpa label{" "}
            <span>· {model.currency ?? "mata uang belum diketahui"}</span>
          </p>
          <p>
            Precision, recall, dan akurasi fraud belum dapat diukur pada data
            sumber tanpa label. Stress test sintetis menilai skenario yang
            dibuat, bukan kinerja fraud produksi.
          </p>
          {stress && (
            <>
              <p>Validasi stress sintetis · sebelum tes final</p>
              <dl>
                <div>
                  <dt>Precision</dt>
                  <dd>{validationPercent(stress.precision)}</dd>
                </div>
                <div>
                  <dt>Recall</dt>
                  <dd>{validationPercent(stress.recall)}</dd>
                </div>
                <div>
                  <dt>Average precision</dt>
                  <dd>{validationValue(stress.average_precision)}</dd>
                </div>
              </dl>
            </>
          )}
        </div>
      ) : (
        <div className="analysis-validation">
          <p>
            {model.state === "RETIRED"
              ? "Historical validation"
              : "Internal validation"}{" "}
            <span>· synthetic data</span>
          </p>
          <dl>
            <div>
              <dt>Average precision</dt>
              <dd>{validationValue(model.validation.average_precision)}</dd>
            </div>
            <div>
              <dt>Precision</dt>
              <dd>{validationValue(model.validation.precision)}</dd>
            </div>
            <div>
              <dt>Recall</dt>
              <dd>{validationValue(model.validation.recall)}</dd>
            </div>
          </dl>
        </div>
      )}
      <details className="analysis-model-reference">
        <summary>Training reference</summary>
        <dl>
          {unsupervised && (
            <>
              <div>
                <dt>Learning</dt>
                <dd>Unsupervised; label tidak digunakan untuk fit</dd>
              </div>
              <div>
                <dt>Mata uang</dt>
                <dd>
                  {model.currency ?? "Belum diketahui"} · tanpa konversi FX
                </dd>
              </div>
              <div>
                <dt>Penjelasan</dt>
                <dd>
                  Selisih skor dengan penggantian median training; bukan
                  TreeSHAP
                </dd>
              </div>
            </>
          )}
          <div>
            <dt>Dataset</dt>
            <dd>
              {model.trained_on.profile ?? "—"} · seed{" "}
              {model.trained_on.seed ?? "—"}
            </dd>
          </div>
          <div>
            <dt>Generator / features</dt>
            <dd>
              {model.trained_on.generator_version ?? "—"} /{" "}
              {model.feature_set_version}
            </dd>
          </div>
          <div>
            <dt>Definition hash</dt>
            <dd className="mono analysis-hash">{model.definition_hash}</dd>
          </div>
        </dl>
      </details>
    </article>
  );
}

/** CF-05: stored flags support investigation; this page offers no disposition. */
export function ModelFlagsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedUnit = searchParams.get("unit");
  const unit: ModelUnit | "" =
    selectedUnit === "AML" || selectedUnit === "FRAUD" ? selectedUnit : "";
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const models = useQuery({ queryKey: ["models"], queryFn: fetchModels });
  const scores = useQuery({
    queryKey: ["model-scores", unit, search, offset],
    queryFn: () =>
      fetchScores({
        unit: unit || undefined,
        search: search || undefined,
        limit: PAGE_SIZE,
        offset,
      }),
    placeholderData: keepPreviousData,
  });

  useEffect(() => {
    if (searchInput.trim() === search) return;
    const timer = setTimeout(() => {
      setSearch(searchInput.trim());
      setOffset(0);
    }, 300);
    return () => clearTimeout(timer);
  }, [searchInput, search]);

  useEffect(() => {
    setOffset(0);
  }, [unit]);

  function selectModel(value: ModelUnit | "") {
    const next = new URLSearchParams(searchParams);
    if (value) next.set("unit", value);
    else next.delete("unit");
    setSearchParams(next, { replace: true });
    setOffset(0);
  }

  const total = scores.data?.total ?? 0;
  const shownOffset = scores.data?.offset ?? offset;
  const unitLabel =
    unit === "AML"
      ? "observasi mingguan"
      : unit === "FRAUD"
        ? "transactions"
        : "model units";

  return (
    <div className="analysis-page">
      <div className="page-header">
        <div>
          <p className="eyebrow">Detection intelligence</p>
          <h1>Model signals</h1>
          <p className="page-subtitle">
            Telusuri skor model dan fitur yang diamati di balik setiap sinyal.
          </p>
        </div>
        <Link to="/alerts" className="button-secondary analysis-header-action">
          <Icon name="alert" size={17} /> Open alert queue
        </Link>
      </div>

      <section
        className="analysis-model-section"
        aria-labelledby="model-versions-heading"
      >
        <div className="section-head">
          <div>
            <h2 id="model-versions-heading">Model versions</h2>
            <p className="muted">
              Versi yang terdaftar dan unit yang dinilai oleh tiap model.
            </p>
          </div>
          {models.data && (
            <span className="analysis-small-count">
              {models.data.length} registered
            </span>
          )}
        </div>
        {models.isPending && (
          <div className="card analysis-loading" role="status">
            Loading model versions…
          </div>
        )}
        {models.error && (
          <div className="card error" role="alert">
            {errorMessage(models.error)}
          </div>
        )}
        {models.data && models.data.length === 0 && (
          <div className="card empty-state">
            <Icon name="layers" size={28} />
            <h3>No model versions available</h3>
            <p>
              Scores will be available when an active model version is
              registered.
            </p>
          </div>
        )}
        {models.data && models.data.length > 0 && (
          <div className="analysis-model-grid">
            {models.data.map((model) => (
              <ModelCard key={model.id} model={model} />
            ))}
          </div>
        )}
      </section>

      <section
        className="card analysis-signal-card"
        aria-labelledby="model-signals-heading"
        aria-busy={scores.isFetching}
      >
        <div className="section-head analysis-signal-heading">
          <div>
            <h2 id="model-signals-heading">Units above threshold</h2>
            <p className="muted">
              Entity-week dan transaksi adalah unit berbeda; flag model
              merupakan sinyal pendukung.
            </p>
          </div>
          {scores.data && (
            <span className="analysis-small-count" aria-live="polite">
              {scores.isPlaceholderData
                ? "Updating…"
                : `${total.toLocaleString("id-ID")} ${unitLabel}`}
            </span>
          )}
        </div>

        <div className="analysis-signals-toolbar">
          <div
            className="analysis-tabs"
            role="tablist"
            aria-label="Scoring model"
          >
            {MODEL_TABS.map((tab, index) => (
              <button
                key={tab.value}
                type="button"
                role="tab"
                id={`model-tab-${index}`}
                aria-selected={unit === tab.value}
                aria-controls="model-scores-panel"
                tabIndex={unit === tab.value ? 0 : -1}
                className={unit === tab.value ? "analysis-tab-active" : ""}
                onClick={() => selectModel(tab.value)}
                onKeyDown={(event) => {
                  let next = index;
                  if (event.key === "ArrowRight")
                    next = (index + 1) % MODEL_TABS.length;
                  else if (event.key === "ArrowLeft")
                    next = (index + MODEL_TABS.length - 1) % MODEL_TABS.length;
                  else if (event.key === "Home") next = 0;
                  else if (event.key === "End") next = MODEL_TABS.length - 1;
                  else return;
                  event.preventDefault();
                  selectModel(MODEL_TABS[next].value);
                  document.getElementById(`model-tab-${next}`)?.focus();
                }}
              >
                {tab.label}
              </button>
            ))}
          </div>
          <label className="analysis-search-field">
            <span className="analysis-sr-only">Search flagged units</span>
            <Icon name="search" size={17} />
            <input
              type="search"
              placeholder="Search entity reference or name"
              maxLength={100}
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
            />
          </label>
        </div>

        <div
          id="model-scores-panel"
          role="tabpanel"
          aria-labelledby={`model-tab-${MODEL_TABS.findIndex((tab) => tab.value === unit)}`}
        >
          {(!scores.data || scores.isPlaceholderData) && <SignalNotice />}
          {(scores.isPending || scores.isPlaceholderData) && (
            <div className="analysis-loading" role="status">
              Loading model signals…
            </div>
          )}
          {scores.error && (
            <div className="error analysis-query-error" role="alert">
              <p>{errorMessage(scores.error)}</p>
              <button
                type="button"
                className="button-secondary"
                onClick={() => void scores.refetch()}
              >
                Try again
              </button>
            </div>
          )}
          {scores.data &&
            !scores.isPlaceholderData &&
            (scores.data.items.length === 0 ? (
              <>
                <SignalNotice text={scores.data.signal_notice} />
                <div className="empty-state analysis-empty">
                  <Icon name="activity" size={30} />
                  <h3>
                    {search
                      ? "No matching signals"
                      : "No units above threshold"}
                  </h3>
                  <p>
                    {search
                      ? "Try another entity reference or name, or select a different model."
                      : "Stored model flags will appear here after detection runs."}
                  </p>
                </div>
              </>
            ) : (
              <ModelSignals
                scores={scores.data.items}
                notice={scores.data.signal_notice}
                showEntity
              />
            ))}
        </div>

        {scores.data && !scores.isPlaceholderData && total > 0 && (
          <div className="analysis-pagination">
            <span className="muted">
              {(shownOffset + 1).toLocaleString("id-ID")}–
              {Math.min(shownOffset + PAGE_SIZE, total).toLocaleString("id-ID")}{" "}
              of {total.toLocaleString("id-ID")} {unitLabel}
            </span>
            <div>
              <button
                type="button"
                className="button-secondary"
                disabled={offset === 0 || scores.isFetching}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              >
                <Icon name="arrow-left" size={16} /> Previous
              </button>
              <button
                type="button"
                className="button-secondary"
                disabled={offset + PAGE_SIZE >= total || scores.isFetching}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                Next <Icon name="arrow-right" size={16} />
              </button>
            </div>
          </div>
        )}
        <div className="analysis-readonly-note">
          <Icon name="shield" size={17} />
          <span>
            Sinyal hanya dapat ditinjau. Keputusan triage dilakukan pada alert
            di antrean.
          </span>
        </div>
      </section>
    </div>
  );
}
