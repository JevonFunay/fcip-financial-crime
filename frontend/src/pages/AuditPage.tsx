import { useEffect, useMemo, useState, type FormEvent } from "react";
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";

import { exportAudit, searchAudit } from "../api/audit";
import { errorMessage } from "../api/errors";
import { Icon } from "../components/Icon";
import { formatDateTime } from "../format";
import type { AuditFilters } from "../types";
import "../analysis.css";

const PAGE_SIZE = 50;
const OBJECT_TYPES = [
  "ALERT",
  "CASE",
  "BATCH",
  "DETECTION_RUN",
  "AUDIT_EXPORT",
  "RULE_VERSION",
  "MODEL_VERSION",
];
const FILTER_KEYS = [
  "correlation_id",
  "object_type",
  "object_id",
  "actor_email",
] as const;
const FILTER_LABELS: Record<(typeof FILTER_KEYS)[number], string> = {
  correlation_id: "Correlation ID",
  object_type: "Object type",
  object_id: "Object ID",
  actor_email: "Actor email",
};
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function readFilters(params: URLSearchParams): Required<AuditFilters> {
  return {
    correlation_id: params.get("correlation_id") ?? "",
    object_type: params.get("object_type") ?? "",
    object_id: params.get("object_id") ?? "",
    actor_email: params.get("actor_email") ?? "",
  };
}

/** FR-1105: applied filters and pagination stay in the URL for a shareable audit chain. */
export function AuditPage() {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = useMemo(() => readFilters(searchParams), [searchParams]);
  const [draft, setDraft] = useState(() => readFilters(searchParams));
  const [validationError, setValidationError] = useState("");
  const offsetValue = Number(searchParams.get("offset") ?? "0");
  const offset =
    Number.isSafeInteger(offsetValue) && offsetValue >= 0 ? offsetValue : 0;

  useEffect(() => {
    setDraft(filters);
    setValidationError("");
  }, [filters]);

  const audit = useQuery({
    queryKey: ["audit", filters, offset],
    queryFn: () => searchAudit(filters, { limit: PAGE_SIZE, offset }),
    placeholderData: keepPreviousData,
  });
  const download = useMutation({
    mutationFn: () => exportAudit(filters),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["audit"] });
    },
  });
  const total = audit.data?.total ?? 0;
  const shownOffset = audit.data?.offset ?? offset;
  const activeFilters = FILTER_KEYS.filter((key) => filters[key] !== "");
  const hasDraftChanges = FILTER_KEYS.some(
    (key) => draft[key] !== filters[key],
  );

  useEffect(() => {
    if (
      !audit.data ||
      audit.isPlaceholderData ||
      offset === 0 ||
      offset < audit.data.total
    )
      return;
    const next = new URLSearchParams(searchParams);
    const lastOffset = Math.max(
      0,
      Math.floor((audit.data.total - 1) / PAGE_SIZE) * PAGE_SIZE,
    );
    if (lastOffset) next.set("offset", String(lastOffset));
    else next.delete("offset");
    setSearchParams(next, { replace: true });
  }, [
    audit.data,
    audit.isPlaceholderData,
    offset,
    searchParams,
    setSearchParams,
  ]);

  function applyFilters(event: FormEvent) {
    event.preventDefault();
    for (const key of ["correlation_id", "object_id"] as const) {
      if (draft[key].trim() && !UUID_PATTERN.test(draft[key].trim())) {
        setValidationError(`${FILTER_LABELS[key]} must be a complete UUID.`);
        return;
      }
    }
    const next = new URLSearchParams(searchParams);
    for (const key of FILTER_KEYS) {
      const value = draft[key].trim();
      if (value) next.set(key, value);
      else next.delete(key);
    }
    next.delete("offset");
    setSearchParams(next, { replace: true });
    setValidationError("");
    download.reset();
  }

  function resetFilters() {
    setSearchParams(new URLSearchParams(), { replace: true });
    setDraft({
      correlation_id: "",
      object_type: "",
      object_id: "",
      actor_email: "",
    });
    setValidationError("");
    download.reset();
  }

  function changePage(value: number) {
    const next = new URLSearchParams(searchParams);
    if (value === 0) next.delete("offset");
    else next.set("offset", String(value));
    setSearchParams(next);
  }

  return (
    <div className="analysis-page">
      <div className="page-header">
        <div>
          <p className="eyebrow">Governance & oversight</p>
          <h1>Audit trail</h1>
          <p className="page-subtitle">
            Telusuri aktivitas, keputusan, dan correlation ID yang
            menghubungkannya.
          </p>
        </div>
        <button
          type="button"
          className="button-secondary analysis-header-action"
          onClick={() => download.mutate()}
          disabled={download.isPending}
        >
          <Icon name="file" size={17} />{" "}
          {download.isPending ? "Preparing export…" : "Export CSV"}
        </button>
      </div>

      <section
        className="card analysis-audit-filters"
        aria-labelledby="audit-filter-heading"
      >
        <div className="section-head">
          <div>
            <h2 id="audit-filter-heading">Find an audit chain</h2>
            <p className="muted">
              Terapkan filter correlation ID, objek, atau aktor untuk menelusuri
              kejadian.
            </p>
          </div>
          <span className="analysis-small-count">
            {activeFilters.length
              ? `${activeFilters.length} active filters`
              : "All events"}
          </span>
        </div>
        <form onSubmit={applyFilters} noValidate>
          <div className="analysis-filter-grid">
            <label>
              Correlation ID
              <input
                aria-label="Correlation ID"
                value={draft.correlation_id}
                onChange={(event) =>
                  setDraft({ ...draft, correlation_id: event.target.value })
                }
                placeholder="Full correlation UUID"
                maxLength={36}
                spellCheck={false}
              />
              <span className="analysis-field-help">
                ID bersama untuk aktivitas yang saling terkait
              </span>
            </label>
            <label>
              Object type
              <select
                value={draft.object_type}
                onChange={(event) =>
                  setDraft({ ...draft, object_type: event.target.value })
                }
              >
                <option value="">All object types</option>
                {draft.object_type &&
                  !OBJECT_TYPES.includes(draft.object_type) && (
                    <option value={draft.object_type}>
                      {draft.object_type}
                    </option>
                  )}
                {OBJECT_TYPES.map((type) => (
                  <option key={type} value={type}>
                    {type.replace(/_/g, " ")}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Object ID
              <input
                value={draft.object_id}
                onChange={(event) =>
                  setDraft({ ...draft, object_id: event.target.value })
                }
                placeholder="Full alert, case, or batch UUID"
                maxLength={36}
                spellCheck={false}
              />
            </label>
            <label>
              Actor email
              <input
                value={draft.actor_email}
                onChange={(event) =>
                  setDraft({ ...draft, actor_email: event.target.value })
                }
                placeholder="e.g. triage@fcip.internal"
                autoCapitalize="none"
                spellCheck={false}
              />
            </label>
          </div>
          {validationError && (
            <p className="error" role="alert">
              {validationError}
            </p>
          )}
          <div className="analysis-filter-actions">
            <button type="submit">
              <Icon name="search" size={16} /> Apply filters
            </button>
            <button
              type="button"
              className="button-secondary"
              onClick={resetFilters}
              disabled={
                !activeFilters.length && !hasDraftChanges && offset === 0
              }
            >
              Reset filters
            </button>
            {hasDraftChanges && (
              <span className="muted">
                Terapkan filter untuk memperbarui hasil.
              </span>
            )}
          </div>
        </form>
      </section>

      {download.error && (
        <div className="error analysis-query-error" role="alert">
          {errorMessage(download.error)}
        </div>
      )}
      {download.isSuccess && (
        <div className="analysis-export-success" role="status">
          <Icon name="check" size={17} /> CSV diunduh. Ekspor tercatat sebagai
          audit event.
        </div>
      )}

      <section
        className="card analysis-audit-events"
        aria-labelledby="audit-events-heading"
        aria-busy={audit.isFetching}
      >
        <div className="section-head">
          <div>
            <h2 id="audit-events-heading">Recorded events</h2>
            <p className="muted">
              Urutan kronologis · kejadian paling awal terlebih dahulu
            </p>
          </div>
          {audit.data && (
            <span className="analysis-small-count" aria-live="polite">
              {audit.isPlaceholderData
                ? "Updating…"
                : `${total.toLocaleString("id-ID")} events`}
            </span>
          )}
        </div>
        {activeFilters.length > 0 && (
          <div className="analysis-filter-summary">
            {activeFilters.map((key) => (
              <span key={key}>
                <strong>{FILTER_LABELS[key]}</strong>
                <span className={key.endsWith("id") ? "mono" : ""}>
                  {filters[key]}
                </span>
              </span>
            ))}
          </div>
        )}
        {(audit.isPending || audit.isPlaceholderData) && (
          <div className="analysis-loading" role="status">
            Loading recorded events…
          </div>
        )}
        {audit.error && (
          <div className="error analysis-query-error" role="alert">
            <p>{errorMessage(audit.error)}</p>
            <button
              type="button"
              className="button-secondary"
              onClick={() => void audit.refetch()}
            >
              Try again
            </button>
          </div>
        )}
        {audit.data &&
          !audit.isPlaceholderData &&
          audit.data.items.length === 0 && (
            <div className="empty-state analysis-empty">
              <Icon name="history" size={30} />
              <h3>
                {activeFilters.length
                  ? "No matching events"
                  : "No audit events yet"}
              </h3>
              <p>
                {activeFilters.length
                  ? "Adjust or reset the filters to explore other recorded actions."
                  : "Recorded actions will appear here as the platform is used."}
              </p>
            </div>
          )}
        {audit.data &&
          !audit.isPlaceholderData &&
          audit.data.items.length > 0 && (
            <div
              className="table-scroll"
              tabIndex={0}
              role="region"
              aria-label="Recorded audit events"
            >
              <table className="table analysis-audit-table">
                <thead>
                  <tr>
                    <th>Timestamp</th>
                    <th>Object</th>
                    <th>State transition</th>
                    <th>Actor</th>
                    <th>Recorded reason</th>
                    <th>Audit chain</th>
                  </tr>
                </thead>
                <tbody>
                  {audit.data.items.map((event) => (
                    <tr key={event.id}>
                      <td className="analysis-audit-time">
                        {formatDateTime(event.created_at)}
                      </td>
                      <td>
                        <span className="analysis-object-type">
                          {event.object_type.replace(/_/g, " ")}
                        </span>
                        <div
                          className="mono analysis-cell-sub"
                          title={event.object_id}
                        >
                          {event.object_id.slice(0, 8)}…
                        </div>
                      </td>
                      <td>
                        <div className="analysis-transition">
                          <span>{event.from_state ?? "—"}</span>
                          <Icon name="arrow-right" size={14} />
                          <strong>{event.to_state}</strong>
                        </div>
                      </td>
                      <td>
                        <strong className="analysis-actor">
                          {event.actor_email ?? "System"}
                        </strong>
                        {event.actor_role && (
                          <div className="analysis-cell-sub">
                            {event.actor_role
                              .replace("ROLE_", "")
                              .replace(/_/g, " ")}
                          </div>
                        )}
                      </td>
                      <td className="analysis-audit-reason">
                        {event.reason ?? "—"}
                      </td>
                      <td>
                        <Link
                          className="analysis-trace-link"
                          to={`/audit?correlation_id=${event.correlation_id}`}
                          aria-label={`Trace audit chain ${event.correlation_id}`}
                        >
                          Trace chain <Icon name="arrow-right" size={14} />
                        </Link>
                        <div
                          className="mono analysis-cell-sub"
                          title={event.correlation_id}
                        >
                          {event.correlation_id.slice(0, 8)}…
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        {audit.data &&
          !audit.isPlaceholderData &&
          audit.data.items.length > 0 && (
            <div className="analysis-pagination">
              <span className="muted">
                {(shownOffset + 1).toLocaleString("id-ID")}–
                {Math.min(shownOffset + PAGE_SIZE, total).toLocaleString(
                  "id-ID",
                )}{" "}
                of {total.toLocaleString("id-ID")} events
                {audit.isFetching && " · updating…"}
              </span>
              <div>
                <button
                  type="button"
                  className="button-secondary"
                  onClick={() => changePage(Math.max(0, offset - PAGE_SIZE))}
                  disabled={offset === 0 || audit.isFetching}
                >
                  <Icon name="arrow-left" size={16} /> Previous
                </button>
                <button
                  type="button"
                  className="button-secondary"
                  onClick={() => changePage(offset + PAGE_SIZE)}
                  disabled={offset + PAGE_SIZE >= total || audit.isFetching}
                >
                  Next <Icon name="arrow-right" size={16} />
                </button>
              </div>
            </div>
          )}
        <div className="analysis-readonly-note">
          <Icon name="history" size={17} />
          <span>
            Ekspor CSV menggunakan filter yang telah diterapkan dan tercatat di
            audit trail.
          </span>
        </div>
      </section>
    </div>
  );
}
