import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { disposeAlert, fetchAlert } from "../api/alerts";
import { createCase } from "../api/cases";
import { errorMessage } from "../api/errors";
import { StatusBadge } from "../components/StatusBadge";
import { useAuth } from "../context/AuthContext";
import { formatAmount, formatDateTime, formatDetailValue } from "../format";
import type { AlertDetail, DispositionDecision } from "../types";

const MIN_REASON_LENGTH = 20;

export function AlertDetailPage() {
  const { id = "" } = useParams();
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const alertQuery = useQuery({ queryKey: ["alert", id], queryFn: () => fetchAlert(id), enabled: id !== "" });

  const [decision, setDecision] = useState<DispositionDecision>("escalate");
  const [reason, setReason] = useState("");

  const dispose = useMutation({
    mutationFn: () => disposeAlert(id, decision, reason),
    onSuccess: (updated: AlertDetail) => {
      queryClient.setQueryData(["alert", id], updated);
      void queryClient.invalidateQueries({ queryKey: ["alerts"] });
      setReason("");
    },
  });

  const openCase = useMutation({
    mutationFn: () => createCase(id),
    onSuccess: (created) => {
      void queryClient.invalidateQueries({ queryKey: ["alert", id] });
      void queryClient.invalidateQueries({ queryKey: ["alerts"] });
      navigate(`/cases/${created.id}`);
    },
  });

  if (alertQuery.isLoading) {
    return <p className="muted">Loading...</p>;
  }
  if (alertQuery.error || !alertQuery.data) {
    return <p className="error">{errorMessage(alertQuery.error)}</p>;
  }
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
    if (!reasonTooShort) {
      dispose.mutate();
    }
  }

  return (
    <>
      <p>
        <Link to="/alerts">← Alert queue</Link>
      </p>
      <div className="page-header">
        <h1>
          {alert.pattern_code} · {alert.customer_ref}
        </h1>
        <StatusBadge status={alert.status} />
      </div>

      <section className="card">
        <dl className="facts">
          <dt>Customer</dt>
          <dd>
            {alert.customer_name} ({alert.customer_ref})
          </dd>
          <dt>Created</dt>
          <dd>{formatDateTime(alert.created_at)}</dd>
          <dt>Reason</dt>
          <dd>{alert.reason}</dd>
          <dt>Correlation ID</dt>
          <dd className="mono">
            <Link to={`/audit?correlation_id=${alert.correlation_id}`}>{alert.correlation_id}</Link>
          </dd>
          {alert.case_id && (
            <>
              <dt>Case</dt>
              <dd>
                <Link to={`/cases/${alert.case_id}`}>Open case →</Link>
              </dd>
            </>
          )}
        </dl>
      </section>

      {alert.status !== "OPEN" && (
        <section className="card">
          <h2>Disposition</h2>
          <dl className="facts">
            <dt>Decision</dt>
            <dd>{alert.status === "ESCALATED" ? "Escalate" : "False positive"}</dd>
            <dt>By</dt>
            <dd>
              {alert.disposed_by_email ?? "-"} · {formatDateTime(alert.disposed_at)}
            </dd>
            <dt>Reason</dt>
            <dd>{alert.disposition_reason}</dd>
          </dl>
        </section>
      )}

      {canDispose && (
        <section className="card">
          <h2>Triage decision</h2>
          <form onSubmit={submitDisposition} className="stack">
            <div className="radio-row">
              <label className="inline">
                <input
                  type="radio"
                  name="decision"
                  value="escalate"
                  checked={decision === "escalate"}
                  onChange={() => setDecision("escalate")}
                />
                Escalate
              </label>
              <label className="inline">
                <input
                  type="radio"
                  name="decision"
                  value="false_positive"
                  checked={decision === "false_positive"}
                  onChange={() => setDecision("false_positive")}
                />
                False positive
              </label>
            </div>
            <label>
              Reason (required, at least {MIN_REASON_LENGTH} characters)
              <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={3} />
              <span className={reasonTooShort ? "muted" : "ok"}>
                {trimmedReason.length}/{MIN_REASON_LENGTH}
              </span>
            </label>
            {dispose.error && <p className="error">{errorMessage(dispose.error)}</p>}
            <div>
              <button type="submit" disabled={reasonTooShort || dispose.isPending}>
                {dispose.isPending ? "Saving..." : "Submit disposition"}
              </button>
            </div>
          </form>
        </section>
      )}

      {canOpenCase && (
        <section className="card">
          <h2>Case</h2>
          <p className="muted">This alert has been escalated but no case has been opened yet.</p>
          {openCase.error && <p className="error">{errorMessage(openCase.error)}</p>}
          <button type="button" onClick={() => openCase.mutate()} disabled={openCase.isPending}>
            {openCase.isPending ? "Opening..." : "Open case from this alert"}
          </button>
        </section>
      )}

      <section className="card">
        <h2>Evidence transactions ({alert.transactions.length})</h2>
        <table className="table">
          <thead>
            <tr>
              <th>Ref</th>
              <th>Date</th>
              <th>Account</th>
              <th className="num">Amount</th>
              <th>Direction</th>
              <th>Channel</th>
              <th>Counterparty</th>
              <th>Description</th>
            </tr>
          </thead>
          <tbody>
            {alert.transactions.map((t) => (
              <tr key={t.id}>
                <td className="mono">{t.transaction_ref}</td>
                <td>{formatDateTime(t.transaction_date)}</td>
                <td className="mono">{t.account_number}</td>
                <td className="num">{formatAmount(t.amount, t.currency)}</td>
                <td>{t.direction}</td>
                <td>{t.channel}</td>
                <td>{t.counterparty_ref ?? "-"}</td>
                <td>{t.description ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="card">
        <h2>Detection factors</h2>
        <table className="table">
          <tbody>
            {Object.entries(alert.detection_details).map(([key, value]) => (
              <tr key={key}>
                <th className="mono">{key}</th>
                <td className="mono">{formatDetailValue(value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}
