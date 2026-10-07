import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { disposeAlert, fetchAlert } from "../api/alerts";
import { createCase } from "../api/cases";
import { errorMessage } from "../api/errors";
import { Icon } from "../components/Icon";
import { ModelSignals } from "../components/ModelSignals";
import { StatusBadge } from "../components/StatusBadge";
import { useAuth } from "../context/AuthContext";
import {
  formatAmount,
  formatDateTime,
  formatDetailValue,
  formatDuration,
} from "../format";
import type { AlertDetail, DispositionDecision } from "../types";
import "../review.css";

const MIN_REASON_LENGTH = 20;

export function AlertDetailPage() {
  const { id = "" } = useParams();
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const alertQuery = useQuery({
    queryKey: ["alert", id],
    queryFn: () => fetchAlert(id),
    enabled: id !== "",
  });
  const [decision, setDecision] = useState<DispositionDecision>("escalate");
  const [reason, setReason] = useState("");

  const dispose = useMutation({
    mutationFn: () => disposeAlert(id, decision, reason.trim()),
    onSuccess: (updated: AlertDetail) => {
      queryClient.setQueryData(["alert", id], updated);
      void queryClient.invalidateQueries({ queryKey: ["alerts"] });
      void queryClient.invalidateQueries({ queryKey: ["overview"] });
      void queryClient.invalidateQueries({ queryKey: ["audit"] });
      setReason("");
    },
  });
  const openCase = useMutation({
    mutationFn: () => createCase(id),
    onSuccess: (created) => {
      queryClient.setQueryData(["case", created.id], created);
      void queryClient.invalidateQueries({ queryKey: ["alert", id] });
      void queryClient.invalidateQueries({ queryKey: ["alerts"] });
      void queryClient.invalidateQueries({ queryKey: ["overview"] });
      void queryClient.invalidateQueries({ queryKey: ["audit"] });
      navigate(`/cases/${created.id}`);
    },
  });

  if (alertQuery.isLoading)
    return (
      <div className="card empty-state" role="status">
        Memuat detail alert…
      </div>
    );
  if (alertQuery.error || !alertQuery.data)
    return (
      <p className="error" role="alert">
        {errorMessage(alertQuery.error)}
      </p>
    );
  const alert = alertQuery.data;
  const trimmedReason = reason.trim();
  const reasonTooShort = trimmedReason.length < MIN_REASON_LENGTH;
  const canDispose = user?.role === "ROLE_TRIAGE" && alert.status === "OPEN";
  const canOpenCase =
    alert.status === "ESCALATED" &&
    alert.case_id === null &&
    (user?.role === "ROLE_TRIAGE" || user?.role === "ROLE_INVESTIGATOR");

  function submitDisposition(event: FormEvent) {
    event.preventDefault();
    if (canDispose && !reasonTooShort && !dispose.isPending) dispose.mutate();
  }

  return (
    <>
      <Link className="btn-link review-back" to="/alerts">
        <Icon name="arrow-left" size={16} />
        Kembali ke antrean alert
      </Link>
      <div className="page-header">
        <div>
          <p className="eyebrow">INVESTIGASI ALERT</p>
          <h1>{alert.customer_ref}</h1>
          <p className="page-subtitle">
            {alert.customer_name} · {alert.pattern_code}
          </p>
        </div>
        <div className="review-header-badges">
          <span
            className={`review-severity review-severity-${alert.rule.severity.toLowerCase()}`}
          >
            {alert.rule.severity}
          </span>
          <StatusBadge status={alert.status} />
        </div>
      </div>
      <section className="card review-summary-card">
        <div className="review-summary-heading">
          <span className="review-summary-icon">
            <Icon name="alert" size={21} />
          </span>
          <div>
            <p className="eyebrow">PERILAKU TERAMATI</p>
            <p className="review-alert-reason">{alert.reason}</p>
          </div>
        </div>
        <div className="review-summary-meta">
          <span>
            <Icon name="clock" size={15} />
            Dibuat {formatDateTime(alert.created_at)}
          </span>
          <Link
            to={`/audit?correlation_id=${alert.correlation_id}`}
            className="btn-link"
          >
            <Icon name="history" size={15} />
            Lihat audit trail
            <Icon name="arrow-right" size={14} />
          </Link>
        </div>
      </section>
      <div className="detail-grid review-detail-grid">
        <div className="review-main-column">
          <section className="card">
            <div className="section-head">
              <h2>Mengapa rule menandai?</h2>
              <span className="review-pattern">
                {alert.rule.rule_ref} · v{alert.rule.version}
              </span>
            </div>
            {/* FR-301 AC3 / BR-301.2: keep the rule's own description verbatim. */}
            <p className="review-rule-description">{alert.rule.description}</p>
            <dl className="facts review-facts">
              <dt>Versi rule</dt>
              <dd className="mono">
                {alert.rule.rule_ref} v{alert.rule.version}
                {alert.rule.state !== "ACTIVE" && (
                  <span className="muted"> (kini {alert.rule.state})</span>
                )}
              </dd>
              <dt>Reason code</dt>
              <dd className="mono">{alert.rule.reason_code}</dd>
              <dt>Tingkat keparahan</dt>
              <dd>{alert.rule.severity}</dd>
              <dt>Window</dt>
              <dd>
                {alert.rule.window_type.toLowerCase()}{" "}
                {formatDuration(alert.rule.window_length)}
              </dd>
            </dl>
          </section>
          <section className="card">
            <div className="section-head">
              <div>
                <h2>Model signals</h2>
                <p className="muted review-caption">
                  Keluaran model mendukung investigasi; keputusan tetap dibuat
                  oleh manusia.
                </p>
              </div>
              <span className="review-count">
                {alert.model_signals.length} tersimpan
              </span>
            </div>
            <ModelSignals
              scores={alert.model_signals}
              notice={alert.signal_notice}
            />
          </section>
        </div>
        <aside className="review-decision-column">
          <section className="card review-decision-card">
            <div className="review-card-heading">
              <Icon name="users" size={19} />
              <h2>Keputusan triage</h2>
            </div>
            {alert.status !== "OPEN" ? (
              <>
                <p className="muted review-caption">
                  Alert ini sudah memiliki keputusan yang dibuat manusia.
                </p>
                <dl className="facts review-facts">
                  <dt>Keputusan</dt>
                  <dd>
                    {alert.status === "ESCALATED"
                      ? "Escalate"
                      : "False positive"}
                  </dd>
                  <dt>Ditinjau oleh</dt>
                  <dd>{alert.disposed_by_email ?? "—"}</dd>
                  <dt>Waktu tinjauan</dt>
                  <dd>{formatDateTime(alert.disposed_at)}</dd>
                </dl>
                <div className="review-recorded-reason">
                  <span className="eyebrow">ALASAN TINJAUAN</span>
                  <p>{alert.disposition_reason ?? "—"}</p>
                </div>
              </>
            ) : canDispose ? (
              <form onSubmit={submitDisposition} className="stack">
                <p className="muted review-caption">
                  Pilih disposition dan catat alasan berdasarkan hasil tinjauan.
                </p>
                <fieldset
                  className="review-decisions"
                  disabled={dispose.isPending}
                >
                  <legend>Disposition</legend>
                  <label
                    className={`review-choice ${decision === "escalate" ? "review-choice-selected" : ""}`}
                  >
                    <input
                      type="radio"
                      name="decision"
                      value="escalate"
                      checked={decision === "escalate"}
                      onChange={() => setDecision("escalate")}
                    />
                    <span>
                      <strong>Escalate</strong>
                      <small>Lanjutkan investigasi</small>
                    </span>
                  </label>
                  <label
                    className={`review-choice ${decision === "false_positive" ? "review-choice-selected" : ""}`}
                  >
                    <input
                      type="radio"
                      name="decision"
                      value="false_positive"
                      checked={decision === "false_positive"}
                      onChange={() => setDecision("false_positive")}
                    />
                    <span>
                      <strong>False positive</strong>
                      <small>Catat mengapa alert ini dapat di-dispose</small>
                    </span>
                  </label>
                </fieldset>
                <label htmlFor="disposition-reason">
                  Alasan tinjauan
                  <textarea
                    disabled={dispose.isPending}
                    id="disposition-reason"
                    aria-label="Alasan tinjauan"
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    rows={4}
                    placeholder="Tuliskan apa yang diperiksa dan alasan keputusan…"
                    aria-describedby="reason-guidance"
                    aria-invalid={reason.length > 0 && reasonTooShort}
                  />
                </label>
                <div id="reason-guidance" className="review-reason-guidance">
                  <span className="muted">
                    Minimal {MIN_REASON_LENGTH} karakter setelah spasi tepi
                    dihapus.
                  </span>
                  <span
                    className={reasonTooShort ? "muted" : "ok"}
                    aria-live="polite"
                  >
                    {trimmedReason.length}/{MIN_REASON_LENGTH}
                  </span>
                </div>
                {dispose.error && (
                  <p className="error" role="alert">
                    {errorMessage(dispose.error)}
                  </p>
                )}
                <button
                  type="submit"
                  disabled={reasonTooShort || dispose.isPending}
                >
                  {dispose.isPending
                    ? "Menyimpan keputusan…"
                    : "Simpan disposition"}
                  <Icon name="arrow-right" size={16} />
                </button>
              </form>
            ) : (
              <div className="review-read-only">
                <Icon name="shield" size={23} />
                <p>
                  Peran Anda dapat membaca detail ini. Disposition dibuat oleh
                  Triage Analyst.
                </p>
              </div>
            )}
            {alert.case_id && (
              <Link
                className="btn-link review-linked-case"
                to={`/cases/${alert.case_id}`}
              >
                <Icon name="file" size={16} />
                Buka case terkait
                <Icon name="arrow-right" size={16} />
              </Link>
            )}
            {canOpenCase && (
              <div className="review-case-action">
                <h3>Lanjutkan ke case</h3>
                <p className="muted">
                  Alert ini sudah di-escalate dan belum memiliki case terkait.
                </p>
                {openCase.error && (
                  <p className="error" role="alert">
                    {errorMessage(openCase.error)}
                  </p>
                )}
                <button
                  type="button"
                  onClick={() => openCase.mutate()}
                  disabled={openCase.isPending}
                >
                  <Icon name="file" size={16} />
                  {openCase.isPending
                    ? "Membuka case…"
                    : "Buka case dari alert ini"}
                </button>
              </div>
            )}
          </section>
          <section className="card">
            <h2>Ketertelusuran</h2>
            <dl className="facts review-facts">
              <dt>Alert ID</dt>
              <dd className="mono">{alert.id}</dd>
              <dt>Correlation ID</dt>
              <dd className="mono">
                <Link to={`/audit?correlation_id=${alert.correlation_id}`}>
                  {alert.correlation_id}
                </Link>
              </dd>
            </dl>
          </section>
        </aside>
      </div>
      <section className="card">
        <div className="section-head">
          <div>
            <h2>Transaksi evidence</h2>
            <p className="muted review-caption">
              Transaksi yang menjadi referensi rule deteksi.
            </p>
          </div>
          <span className="review-count">
            {alert.transactions.length.toLocaleString("id-ID")} transaksi
          </span>
        </div>
        <div
          className="table-scroll"
          tabIndex={0}
          role="region"
          aria-label="Transaksi evidence"
        >
          <table className="table review-evidence-table">
            <thead>
              <tr>
                <th>Transaksi / akun</th>
                <th>Waktu</th>
                <th className="num">Nominal</th>
                <th>Arah</th>
                <th>Channel</th>
                <th>Counterparty</th>
                <th>Deskripsi</th>
              </tr>
            </thead>
            <tbody>
              {alert.transactions.map((t) => (
                <tr key={t.id}>
                  <td>
                    <span className="mono">{t.transaction_ref}</span>
                    <div className="mono review-cell-sub">
                      {t.account_number}
                    </div>
                  </td>
                  <td className="review-date-cell">
                    {formatDateTime(t.transaction_date)}
                  </td>
                  <td className="num">{formatAmount(t.amount, t.currency)}</td>
                  <td>
                    <span className="review-pattern">{t.direction}</span>
                  </td>
                  <td>{t.channel}</td>
                  <td>{t.counterparty_ref ?? "—"}</td>
                  <td className="reason-cell">{t.description ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section className="card">
        <div className="section-head">
          <div>
            <h2>Faktor deteksi</h2>
            <p className="muted review-caption">
              Nilai teramati dan parameter yang disimpan bersama alert ini.
            </p>
          </div>
          <Icon name="layers" size={19} />
        </div>
        <div
          className="table-scroll"
          tabIndex={0}
          role="region"
          aria-label="Faktor deteksi"
        >
          <table className="table review-factors-table">
            <tbody>
              {Object.entries(alert.detection_details).map(([key, value]) => (
                <tr key={key}>
                  <th className="mono">{key}</th>
                  <td className="mono review-factor-value">
                    {formatDetailValue(value)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
