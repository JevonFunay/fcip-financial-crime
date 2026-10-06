import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { errorMessage } from "../api/errors";
import { fetchModels, fetchScores } from "../api/ml";
import { ModelSignals } from "../components/ModelSignals";
import type { ModelUnit } from "../types";

const PAGE_SIZE = 50;

/** What the models flagged (score at or above each version's threshold). Not
 *  alerts: while the ML_SCORE rule is DRAFT a score raises none (CF-05). */
export function ModelFlagsPage() {
  const [unit, setUnit] = useState<ModelUnit | "">("");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const models = useQuery({ queryKey: ["ml-models"], queryFn: fetchModels });
  const scores = useQuery({
    queryKey: ["ml-scores", unit, search, offset],
    queryFn: () => fetchScores({ unit: unit || undefined, search: search || undefined, limit: PAGE_SIZE, offset }),
    placeholderData: keepPreviousData,
  });
  const total = scores.data?.total ?? 0;

  return (
    <>
      <div className="page-header">
        <h1>Model flags</h1>
        {scores.data && <span className="muted">{total.toLocaleString("id-ID")} unit(s) at or above threshold</span>}
      </div>

      {scores.data && <p className="notice">{scores.data.synthetic_notice}</p>}

      <section className="card">
        <h2>Model versions</h2>
        {models.error && <p className="error">{errorMessage(models.error)}</p>}
        {models.data && models.data.length === 0 && (
          <p className="muted">No model version registered: python -m app.scripts.register_models</p>
        )}
        {models.data && models.data.length > 0 && (
          <table className="table">
            <thead>
              <tr>
                <th>Version</th>
                <th>Unit</th>
                <th>State</th>
                <th className="num">Threshold</th>
                <th>Trained on</th>
                <th className="num">Validation AP / P / R</th>
              </tr>
            </thead>
            <tbody>
              {models.data.map((m) => (
                <tr key={m.id}>
                  <td className="mono">{m.version}</td>
                  <td>{m.unit === "AML" ? "entity-week" : "transaction"}</td>
                  <td>{m.state}</td>
                  <td className="num mono">{m.threshold.toFixed(3)}</td>
                  <td>
                    generator {m.trained_on.generator_version}, {m.trained_on.profile}, seed {m.trained_on.seed}
                  </td>
                  <td className="num mono">
                    {[m.validation.average_precision, m.validation.precision, m.validation.recall]
                      .map((v) => (v === undefined ? "-" : v.toFixed(3)))
                      .join(" / ")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="card">
        <div className="section-head">
          <h2>Flagged units</h2>
          <div className="toolbar">
            <input
              type="search"
              placeholder="Search entity ref or name"
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setOffset(0);
              }}
              aria-label="Search flagged units"
            />
            <select
              value={unit}
              onChange={(e) => {
                setUnit(e.target.value as ModelUnit | "");
                setOffset(0);
              }}
              aria-label="Model"
            >
              <option value="">Both models</option>
              <option value="AML">Weekly entity model (AML)</option>
              <option value="FRAUD">Transaction model</option>
            </select>
          </div>
        </div>
        {scores.error && <p className="error">{errorMessage(scores.error)}</p>}
        {scores.data && <ModelSignals scores={scores.data.items} notice={scores.data.signal_notice} showEntity />}
        {total > PAGE_SIZE && (
          <div className="pager">
            <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
              Previous
            </button>
            <span className="muted">
              {offset + 1}-{Math.min(offset + PAGE_SIZE, total)} of {total.toLocaleString("id-ID")}
            </span>
            <button type="button" disabled={offset + PAGE_SIZE >= total} onClick={() => setOffset(offset + PAGE_SIZE)}>
              Next
            </button>
          </div>
        )}
      </section>
    </>
  );
}
