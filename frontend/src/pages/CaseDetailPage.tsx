import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { fetchCase } from "../api/cases";
import { errorMessage } from "../api/errors";
import { Icon } from "../components/Icon";
import { SignalNotice, SIGNAL_NOTICE } from "../components/Notices";
import { StatusBadge } from "../components/StatusBadge";
import { formatDateTime } from "../format";
import "../review.css";

export function CaseDetailPage() {
  const { id = "" } = useParams();
  const { data, isLoading, error } = useQuery({
    queryKey: ["case", id],
    queryFn: () => fetchCase(id),
    enabled: id !== "",
  });
  if (isLoading)
    return (
      <div className="card empty-state" role="status">
        Memuat detail case…
      </div>
    );
  if (error || !data)
    return (
      <p className="error" role="alert">
        {errorMessage(error)}
      </p>
    );

  return (
    <>
      <Link className="btn-link review-back" to="/alerts">
        <Icon name="arrow-left" size={16} />
        Kembali ke antrean alert
      </Link>
      <div className="page-header">
        <div>
          <p className="eyebrow">INVESTIGASI CASE</p>
          <h1>{data.case_number}</h1>
          <p className="page-subtitle">
            Catatan bersama untuk alert yang sedang diinvestigasi.
          </p>
        </div>
        <StatusBadge status={data.status} />
      </div>
      <div className="detail-grid review-case-grid">
        <section className="card">
          <div className="review-card-heading">
            <Icon name="file" size={20} />
            <h2>Ringkasan case</h2>
          </div>
          <dl className="facts review-facts">
            <dt>Dibuka oleh</dt>
            <dd>{data.opened_by_email}</dd>
            <dt>Dibuat</dt>
            <dd>{formatDateTime(data.created_at)}</dd>
            <dt>Ditugaskan kepada</dt>
            <dd>{data.assigned_to ?? "Belum ditugaskan"}</dd>
            <dt>Diperbarui</dt>
            <dd>{formatDateTime(data.updated_at)}</dd>
          </dl>
        </section>
        <section className="card review-case-trace">
          <div className="review-card-heading">
            <Icon name="history" size={20} />
            <h2>Audit trail</h2>
          </div>
          <p className="muted">
            Telusuri tindakan yang tercatat untuk case ini.
          </p>
          <p className="mono review-correlation">{data.correlation_id}</p>
          <Link
            className="btn-link"
            to={`/audit?correlation_id=${data.correlation_id}`}
          >
            Lihat rangkaian audit
            <Icon name="arrow-right" size={16} />
          </Link>
        </section>
      </div>
      <section className="card">
        <div className="section-head">
          <div>
            <h2>Alert terkait</h2>
            <p className="muted review-caption">
              Buka alert untuk meninjau rule, faktor teramati, dan sinyal model.
            </p>
          </div>
          <span className="review-count">
            {data.alerts.length.toLocaleString("id-ID")} terkait
          </span>
        </div>
        <SignalNotice text={SIGNAL_NOTICE} />
        {data.alerts.length === 0 ? (
          <div className="empty-state">
            Belum ada alert yang terkait dengan case ini.
          </div>
        ) : (
          <div
            className="table-scroll"
            tabIndex={0}
            role="region"
            aria-label="Alert terkait dalam case"
          >
            <table className="table review-alert-table">
              <thead>
                <tr>
                  <th>Entity</th>
                  <th>Pattern</th>
                  <th>Status</th>
                  <th>Perilaku teramati</th>
                  <th>Dibuat</th>
                  <th>
                    <span className="review-sr-only">Tindakan</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.alerts.map((alert) => (
                  <tr key={alert.id}>
                    <td>
                      <Link
                        className="review-entity-link"
                        to={`/alerts/${alert.id}`}
                      >
                        {alert.customer_ref}
                      </Link>
                      <div className="review-cell-sub">
                        {alert.customer_name}
                      </div>
                    </td>
                    <td>
                      <span className="review-pattern">
                        {alert.pattern_code}
                      </span>
                    </td>
                    <td>
                      <StatusBadge status={alert.status} />
                    </td>
                    <td className="reason-cell">{alert.reason}</td>
                    <td className="review-date-cell">
                      {formatDateTime(alert.created_at)}
                    </td>
                    <td>
                      <Link
                        className="btn-link review-investigate"
                        to={`/alerts/${alert.id}`}
                        aria-label={`Investigasi ${alert.customer_ref}`}
                      >
                        Investigasi
                        <Icon name="arrow-right" size={16} />
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
