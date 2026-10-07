import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { fetchAlerts } from "../api/alerts";
import { errorMessage } from "../api/errors";
import { Icon } from "../components/Icon";
import { SignalNotice, SIGNAL_NOTICE } from "../components/Notices";
import { StatusBadge } from "../components/StatusBadge";
import { formatDateTime } from "../format";
import type { AlertStatus } from "../types";
import "../review.css";

const STATUS_OPTIONS: { value: AlertStatus | ""; label: string }[] = [
  { value: "OPEN", label: "Alert terbuka" },
  { value: "ESCALATED", label: "Escalated" },
  { value: "DISPOSED", label: "Disposed" },
  { value: "", label: "Semua status" },
];
const PAGE_SIZE = 25;

export function AlertQueuePage() {
  const [status, setStatus] = useState<AlertStatus | "">("OPEN");
  const [offset, setOffset] = useState(0);
  const { data, isLoading, isFetching, error } = useQuery({
    queryKey: ["alerts", status, offset],
    queryFn: () =>
      fetchAlerts(status || undefined, { limit: PAGE_SIZE, offset }),
  });
  useEffect(() => {
    if (data && offset > 0 && offset >= data.total) {
      setOffset(
        Math.max(0, Math.floor((data.total - 1) / PAGE_SIZE) * PAGE_SIZE),
      );
    }
  }, [data, offset]);

  return (
    <>
      <div className="page-header">
        <div>
          <p className="eyebrow">MONITORING / TRIAGE</p>
          <h1>Alert queue</h1>
          <p className="page-subtitle">
            Tinjau perilaku yang teramati dan tentukan langkah berikutnya.
          </p>
        </div>
        {data && (
          <span className="review-count">
            <Icon name="alert" size={16} /> {data.total.toLocaleString("id-ID")}{" "}
            sesuai filter
          </span>
        )}
      </div>
      <section className="card review-queue-card">
        <div className="section-head">
          <div>
            <h2>Alert dari rule</h2>
            <p className="muted review-caption">
              Skor model menjadi sinyal pendukung untuk tinjauan manusia.
            </p>
          </div>
          <label className="review-filter">
            <span>Status</span>
            <select
              value={status}
              onChange={(e) => {
                setStatus(e.target.value as AlertStatus | "");
                setOffset(0);
              }}
            >
              {STATUS_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
        </div>
        <SignalNotice text={SIGNAL_NOTICE} />
        {isLoading && (
          <div className="empty-state" role="status">
            Memuat alert…
          </div>
        )}
        {error && (
          <p className="error" role="alert">
            {errorMessage(error)}
          </p>
        )}
        {data && data.items.length === 0 && (
          <div className="empty-state">
            <Icon name="check" size={28} />
            <h3>Belum ada alert pada tampilan ini</h3>
            <p>
              Tidak ada alert dengan status yang dipilih. Ubah status untuk
              meninjau antrean lainnya.
            </p>
          </div>
        )}
        {data && data.items.length > 0 && (
          <>
            <div
              className="table-scroll"
              tabIndex={0}
              role="region"
              aria-label="Tabel antrean alert"
            >
              <table className="table review-alert-table">
                <thead>
                  <tr>
                    <th>Entity / alert</th>
                    <th>Pattern</th>
                    <th>Status</th>
                    <th
                      className="num"
                      title="Skor model tersimpan tertinggi untuk entity dalam window alert ini"
                    >
                      Skor model
                    </th>
                    <th>Perilaku teramati</th>
                    <th>Dibuat</th>
                    <th>
                      <span className="review-sr-only">Tindakan</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((alert) => (
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
                        <div className="mono review-cell-id">{alert.id}</div>
                      </td>
                      <td>
                        <span className="review-pattern">
                          {alert.pattern_code}
                        </span>
                        <div className="review-cell-sub">Deteksi rule</div>
                      </td>
                      <td>
                        <StatusBadge status={alert.status} />
                      </td>
                      <td className="num">
                        <span className="review-score">
                          {alert.model_score === null
                            ? "—"
                            : alert.model_score.toFixed(3)}
                        </span>
                        <div className="review-cell-sub">Skor pendukung</div>
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
                          <span>Investigasi</span>
                          <Icon name="arrow-right" size={16} />
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div
              className="pager review-pager"
              aria-label="Pagination antrean alert"
            >
              <span className="muted" aria-live="polite">
                Menampilkan {(data.offset + 1).toLocaleString("id-ID")}–
                {(data.offset + data.items.length).toLocaleString("id-ID")} dari{" "}
                {data.total.toLocaleString("id-ID")} alert
              </span>
              <div className="review-pager-controls">
                <button
                  type="button"
                  className="button-secondary"
                  disabled={offset === 0 || isFetching}
                  onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                  aria-label="Halaman alert sebelumnya"
                >
                  <Icon name="arrow-left" size={15} />
                  Sebelumnya
                </button>
                <button
                  type="button"
                  className="button-secondary"
                  disabled={offset + PAGE_SIZE >= data.total || isFetching}
                  onClick={() => setOffset(offset + PAGE_SIZE)}
                  aria-label="Halaman alert berikutnya"
                >
                  Berikutnya
                  <Icon name="arrow-right" size={15} />
                </button>
              </div>
            </div>
          </>
        )}
      </section>
    </>
  );
}
