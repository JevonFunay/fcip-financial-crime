import type { AlertStatus, CaseStatus } from "../types";

export function StatusBadge({ status }: { status: AlertStatus | CaseStatus }) {
  return <span className={`badge badge-${status.toLowerCase()}`}>{status.replace("_", " ")}</span>;
}
