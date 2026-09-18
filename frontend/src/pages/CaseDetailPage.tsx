import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { fetchCase } from "../api/cases";
import { errorMessage } from "../api/errors";
import { StatusBadge } from "../components/StatusBadge";
import { formatDateTime } from "../format";

export function CaseDetailPage() {
  const { id = "" } = useParams();
  const { data, isLoading, error } = useQuery({
    queryKey: ["case", id],
    queryFn: () => fetchCase(id),
    enabled: id !== "",
  });

  if (isLoading) {
    return <p className="muted">Loading...</p>;
  }
  if (error || !data) {
    return <p className="error">{errorMessage(error)}</p>;
  }

  return (
    <>
      <p>
        <Link to="/alerts">← Alert queue</Link>
      </p>
      <div className="page-header">
        <h1>{data.case_number}</h1>
        <StatusBadge status={data.status} />
      </div>

      <section className="card">
        <dl className="facts">
          <dt>Opened by</dt>
          <dd>
            {data.opened_by_email} · {formatDateTime(data.created_at)}
          </dd>
          <dt>Assigned to</dt>
          <dd>{data.assigned_to ?? "Unassigned"}</dd>
          <dt>Last updated</dt>
          <dd>{formatDateTime(data.updated_at)}</dd>
          <dt>Correlation ID</dt>
          <dd className="mono">{data.correlation_id}</dd>
        </dl>
      </section>

      <section className="card">
        <h2>Linked alerts ({data.alerts.length})</h2>
        <table className="table">
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
            {data.alerts.map((alert) => (
              <tr key={alert.id}>
                <td>{formatDateTime(alert.created_at)}</td>
                <td>
                  <Link to={`/alerts/${alert.id}`}>{alert.customer_ref}</Link>
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
      </section>
    </>
  );
}
