import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { fetchAlerts } from "../api/alerts";
import { errorMessage } from "../api/errors";
import { StatusBadge } from "../components/StatusBadge";
import { formatDateTime } from "../format";
import type { AlertStatus } from "../types";

const STATUS_OPTIONS: { value: AlertStatus | ""; label: string }[] = [
  { value: "OPEN", label: "Open" },
  { value: "ESCALATED", label: "Escalated" },
  { value: "DISPOSED", label: "Disposed" },
  { value: "", label: "All" },
];

export function AlertQueuePage() {
  const [status, setStatus] = useState<AlertStatus | "">("OPEN");
  const navigate = useNavigate();
  const { data, isLoading, error } = useQuery({
    queryKey: ["alerts", status],
    queryFn: () => fetchAlerts(status || undefined),
  });

  return (
    <>
      <div className="page-header">
        <h1>Alert queue</h1>
        <label className="inline">
          Status
          <select value={status} onChange={(e) => setStatus(e.target.value as AlertStatus | "")}>
            {STATUS_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        {data && <span className="muted">{data.total} alert(s)</span>}
      </div>

      {isLoading && <p className="muted">Loading...</p>}
      {error && <p className="error">{errorMessage(error)}</p>}

      {data && data.items.length === 0 && <p className="muted">No alerts with this status.</p>}

      {data && data.items.length > 0 && (
        <table className="table clickable">
          <thead>
            <tr>
              <th>Created</th>
              <th>Customer</th>
              <th>Pattern</th>
              <th>Status</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((alert) => (
              <tr key={alert.id} onClick={() => navigate(`/alerts/${alert.id}`)}>
                <td>{formatDateTime(alert.created_at)}</td>
                <td>
                  <Link to={`/alerts/${alert.id}`} onClick={(e) => e.stopPropagation()}>
                    {alert.customer_ref}
                  </Link>
                  <div className="muted">{alert.customer_name}</div>
                </td>
                <td>{alert.pattern_code}</td>
                <td>
                  <StatusBadge status={alert.status} />
                </td>
                <td className="reason-cell">{alert.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
