import type { AlertStatus, CaseStatus } from "../types";
import "../review.css";

export function StatusBadge({ status }: { status: AlertStatus | CaseStatus }) {
  return (
    <span className={`badge badge-${status.toLowerCase()}`}>
      <span className="review-status-dot" aria-hidden="true" />
      {status.replace(/_/g, " ")}
    </span>
  );
}
