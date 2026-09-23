import { useState } from "react";
import { keepPreviousData, useMutation, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";

import { exportAudit, searchAudit } from "../api/audit";
import { errorMessage } from "../api/errors";
import { formatDateTime } from "../format";
import type { AuditFilters } from "../types";

const PAGE_SIZE = 50;
const OBJECT_TYPES = ["", "ALERT", "CASE", "BATCH", "DETECTION_RUN", "AUDIT_EXPORT"];

/** FR-1105 / UAT-12: find a whole chain from one correlation ID, without
 *  database access (NFR-13). Deep-linked from alert and case detail. */
export function AuditPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [offset, setOffset] = useState(0);

  // The URL is the source of truth, so a deep link from an alert or case lands
  // on the filtered chain and stays shareable.
  const filters: AuditFilters = {
    correlation_id: searchParams.get("correlation_id") ?? "",
    object_type: searchParams.get("object_type") ?? "",
    object_id: searchParams.get("object_id") ?? "",
    actor_email: searchParams.get("actor_email") ?? "",
  };

  const update = (key: keyof AuditFilters, value: string) => {
    const next = new URLSearchParams(searchParams);
    if (value.trim() === "") {
      next.delete(key);
    } else {
      next.set(key, value.trim());
    }
    setSearchParams(next, { replace: true });
    setOffset(0);
  };

  const { data, isFetching, error } = useQuery({
    queryKey: ["audit", filters, offset],
    queryFn: () => searchAudit(filters, { limit: PAGE_SIZE, offset }),
    placeholderData: keepPreviousData,
  });

  const download = useMutation({ mutationFn: () => exportAudit(filters) });

  const total = data?.total ?? 0;
  const hasFilter = Object.values(filters).some((value) => value !== "");

  return (
    <>
      <div className="page-header">
        <h1>Audit trail</h1>
        <span className="muted">{total} event</span>
      </div>

      <section className="card">
        <div className="filter-grid">
          <label>
            Correlation ID
            <input
              value={filters.correlation_id}
              onChange={(e) => update("correlation_id", e.target.value)}
              placeholder="UUID rantai kejadian"
            />
          </label>
          <label>
            Tipe objek
            <select value={filters.object_type} onChange={(e) => update("object_type", e.target.value)}>
              {OBJECT_TYPES.map((type) => (
                <option key={type} value={type}>
                  {type === "" ? "Semua" : type}
                </option>
              ))}
            </select>
          </label>
          <label>
            Object ID
            <input
              value={filters.object_id}
              onChange={(e) => update("object_id", e.target.value)}
              placeholder="UUID alert / case / batch"
            />
          </label>
          <label>
            Aktor (email)
            <input
              value={filters.actor_email}
              onChange={(e) => update("actor_email", e.target.value)}
              placeholder="triage@fcip.internal"
            />
          </label>
        </div>
        <div className="inline-controls">
          <button type="button" onClick={() => setSearchParams(new URLSearchParams(), { replace: true })}>
            Reset filter
          </button>
          <button type="button" onClick={() => download.mutate()} disabled={download.isPending}>
            {download.isPending ? "Menyiapkan..." : "Export CSV"}
          </button>
          <span className="muted">Export tercatat sebagai audit event tersendiri.</span>
        </div>
        {download.error && <p className="error">{errorMessage(download.error)}</p>}
      </section>

      {error && <p className="error">{errorMessage(error)}</p>}

      <section className="card">
        <table className="table">
          <thead>
            <tr>
              <th>Waktu</th>
              <th>Objek</th>
              <th>Transisi</th>
              <th>Aktor</th>
              <th>Alasan</th>
            </tr>
          </thead>
          <tbody>
            {data?.items.map((event) => (
              <tr key={event.id}>
                <td>{formatDateTime(event.created_at)}</td>
                <td>
                  {event.object_type}
                  <div className="muted mono">{event.object_id.slice(0, 8)}…</div>
                </td>
                <td>
                  {event.from_state ?? "—"} → <strong>{event.to_state}</strong>
                </td>
                <td>
                  {event.actor_email ?? <span className="muted">system</span>}
                  {event.actor_role && <div className="muted">{event.actor_role.replace("ROLE_", "")}</div>}
                </td>
                <td className="reason-cell">{event.reason ?? "-"}</td>
              </tr>
            ))}
            {data?.items.length === 0 && (
              <tr>
                <td colSpan={5} className="muted">
                  {hasFilter
                    ? "Tidak ada audit event yang cocok dengan filter di atas."
                    : "Belum ada audit event."}
                </td>
              </tr>
            )}
          </tbody>
        </table>

        <div className="inline-controls">
          <button type="button" onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))} disabled={offset === 0}>
            ← Sebelumnya
          </button>
          <span className="muted">
            {total === 0 ? 0 : offset + 1}–{Math.min(offset + PAGE_SIZE, total)} dari {total}
            {isFetching && " · memuat..."}
          </span>
          <button
            type="button"
            onClick={() => setOffset(offset + PAGE_SIZE)}
            disabled={offset + PAGE_SIZE >= total}
          >
            Berikutnya →
          </button>
        </div>
      </section>
    </>
  );
}
