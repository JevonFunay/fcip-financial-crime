import { Fragment, useState } from "react";

import { formatDateTime } from "../format";
import type { ModelScore } from "../types";

function formatValue(value: number | null): string {
  if (value === null) return "unknown";
  return Math.abs(value) >= 1000 ? value.toLocaleString("id-ID", { maximumFractionDigits: 0 }) : String(+value.toFixed(4));
}

/** FR-406: the notice is part of the component, so no surface can drop or close it (BR-406.1). */
export function SignalNotice({ text }: { text: string }) {
  return <p className="notice">{text}</p>;
}

/** Scores with the named features behind them (BR-401.3), in descriptive words (BR-406.2). */
export function ModelSignals({ scores, notice, showEntity = false }: { scores: ModelScore[]; notice: string; showEntity?: boolean }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <>
      <SignalNotice text={notice} />
      {scores.length === 0 ? (
        <p className="muted">No model has scored this entity over this window.</p>
      ) : (
        <table className="table">
          <thead>
            <tr>
              {showEntity && <th>Entity</th>}
              <th>Model</th>
              <th>Unit</th>
              <th className="num">Score</th>
              <th className="num">Threshold</th>
              <th>Strongest features</th>
            </tr>
          </thead>
          <tbody>
            {scores.map((s) => (
              <Fragment key={s.id}>
                <tr onClick={() => setOpen(open === s.id ? null : s.id)} className="clickable-row">
                  {showEntity && (
                    <td>
                      {s.customer_ref}
                      <div className="muted">{s.customer_name}</div>
                    </td>
                  )}
                  <td className="mono">{s.model_version}</td>
                  <td>
                    {s.unit === "AML" ? `Week ending ${formatDateTime(s.as_of)}` : `${s.transaction_ref} · ${formatDateTime(s.as_of)}`}
                  </td>
                  <td className={`num mono ${s.flagged ? "score-high" : ""}`}>{s.score.toFixed(3)}</td>
                  <td className="num mono">{s.threshold.toFixed(3)}</td>
                  <td>
                    {s.contributions.slice(0, 3).map((c) => (
                      <div key={c.feature}>
                        <span className="mono">{c.feature}</span> = {formatValue(c.value)}
                      </div>
                    ))}
                    <span className="muted">{open === s.id ? "hide detail" : "show detail"}</span>
                  </td>
                </tr>
                {open === s.id && (
                  <tr>
                    <td colSpan={showEntity ? 6 : 5}>
                      <table className="table compact">
                        <thead>
                          <tr>
                            <th>Feature</th>
                            <th>Meaning</th>
                            <th className="num">Value</th>
                            <th className="num">Contribution</th>
                          </tr>
                        </thead>
                        <tbody>
                          {s.contributions.map((c) => (
                            <tr key={c.feature}>
                              <td className="mono">{c.feature}</td>
                              <td>{c.definition}</td>
                              <td className="num mono">{formatValue(c.value)}</td>
                              <td className={`num mono ${c.contribution > 0 ? "up" : "down"}`}>
                                {c.contribution > 0 ? "+" : ""}
                                {c.contribution.toFixed(2)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
