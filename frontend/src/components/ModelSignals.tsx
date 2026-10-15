import { Fragment, useState } from "react";

import { Icon } from "./Icon";
import { SignalNotice } from "./Notices";
import { formatObservedDateTime } from "../format";
import type { ModelScore } from "../types";
import "../review.css";

export { SignalNotice } from "./Notices";

function formatValue(value: number | null): string {
  if (value === null) return "belum diketahui";
  return value.toLocaleString("id-ID", { maximumFractionDigits: 4 });
}

/** Explanation scale belongs to its immutable model version, not its table. */
export function ModelSignals({
  scores,
  notice,
  showEntity = false,
}: {
  scores: ModelScore[];
  notice: string;
  showEntity?: boolean;
}) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <>
      <SignalNotice text={notice} />
      {scores.length === 0 ? (
        <div className="empty-state review-signal-empty">
          <Icon name="activity" size={26} />
          <h3>Belum ada sinyal model tersimpan</h3>
          <p>Tidak ada sinyal model yang disimpan pada tampilan ini.</p>
        </div>
      ) : (
        <div
          className="table-scroll"
          tabIndex={0}
          role="region"
          aria-label="Sinyal model dan kontribusi fitur"
        >
          <table className="table review-signals-table">
            <thead>
              <tr>
                {showEntity && <th>Entity</th>}
                <th>Model</th>
                <th>Unit penilaian</th>
                <th className="num">Skor</th>
                <th className="num">Threshold</th>
                <th>Fitur teratas</th>
                <th>
                  <span className="review-sr-only">Detail fitur</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {scores.map((s) => (
                <Fragment key={s.id}>
                  <tr>
                    {showEntity && (
                      <td>
                        <strong>{s.customer_ref}</strong>
                        <div className="review-cell-sub">{s.customer_name}</div>
                      </td>
                    )}
                    <td>
                      <span className="review-pattern">
                        {s.unit === "AML"
                          ? s.score_kind === "anomaly_score"
                            ? "Model rekening-week"
                            : "Model entity-week"
                          : "Model transaksi"}
                      </span>
                      <div className="mono review-cell-sub">
                        {s.model_version}
                        {s.score_kind === "anomaly_score" && (
                          <div>
                            Unsupervised ·{" "}
                            {s.currency ?? "mata uang belum diketahui"}
                          </div>
                        )}
                      </div>
                    </td>
                    <td>
                      {s.unit === "AML" ? "Minggu berakhir" : s.transaction_ref}
                      <div className="review-cell-sub">
                        {formatObservedDateTime(
                          s.as_of,
                          s.timestamp_precision,
                          s.source_timezone_known,
                        )}
                      </div>
                    </td>
                    <td className="num">
                      <span
                        className={`review-score ${s.flagged ? "score-high" : ""}`}
                      >
                        {s.score.toFixed(3)}
                      </span>
                    </td>
                    <td className="num mono">{s.threshold.toFixed(3)}</td>
                    <td className="review-feature-cell">
                      {s.contributions.slice(0, 3).map((c) => (
                        <div className="review-feature" key={c.feature}>
                          <span>{c.definition}</span>
                          <strong>{formatValue(c.value)}</strong>
                        </div>
                      ))}
                    </td>
                    <td>
                      <button
                        type="button"
                        className="button-secondary review-detail-toggle"
                        aria-expanded={open === s.id}
                        aria-controls={`signal-${s.id}`}
                        onClick={() => setOpen(open === s.id ? null : s.id)}
                      >
                        <Icon
                          name={open === s.id ? "close" : "layers"}
                          size={15}
                        />
                        {open === s.id ? "Tutup detail" : "Lihat detail"}
                      </button>
                    </td>
                  </tr>
                  {open === s.id && (
                    <tr
                      id={`signal-${s.id}`}
                      className="review-contribution-row"
                    >
                      <td colSpan={showEntity ? 7 : 6}>
                        <div className="review-contribution-panel">
                          <h3>Kontribusi fitur</h3>
                          <p className="muted review-caption">
                            {s.explanation_method ===
                            "median_replacement_score_difference"
                              ? "Selisih skor saat satu fitur diganti median training. Nilai positif berarti skor asli lebih tinggi. Ini bukan TreeSHAP, bukan log-odds, tidak aditif, dan bukan bukti sebab-akibat. Skor anomali bukan probabilitas fraud."
                              : "Kontribusi memakai skala log-odds (TreeSHAP). Nilai positif menaikkan skor; nilainya bukan persentase."}
                          </p>
                          <table className="table compact">
                            <thead>
                              <tr>
                                <th>Feature</th>
                                <th>Arti</th>
                                <th className="num">Nilai teramati</th>
                                <th className="num">Kontribusi</th>
                              </tr>
                            </thead>
                            <tbody>
                              {s.contributions.map((c) => (
                                <tr key={c.feature}>
                                  <td className="mono">{c.feature}</td>
                                  <td>{c.definition}</td>
                                  <td className="num mono">
                                    {formatValue(c.value)}
                                  </td>
                                  <td
                                    className={`num mono ${c.contribution > 0 ? "up" : "down"}`}
                                  >
                                    {c.contribution > 0 ? "+" : ""}
                                    {c.contribution.toFixed(
                                      s.explanation_method ===
                                        "median_replacement_score_difference"
                                        ? 4
                                        : 2,
                                    )}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
