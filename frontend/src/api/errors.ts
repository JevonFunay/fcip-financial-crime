import axios from "axios";

export function errorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    if (!error.response) {
      return "Cannot reach the backend";
    }
    const detail = (error.response.data as { detail?: unknown } | undefined)?.detail;
    if (typeof detail === "string") {
      return detail;
    }
    if (Array.isArray(detail)) {
      return detail
        .map((item: { msg?: unknown }) => (typeof item.msg === "string" ? item.msg : JSON.stringify(item)))
        .join("; ");
    }
    return `Request failed (${error.response.status})`;
  }
  return error instanceof Error ? error.message : "Unexpected error";
}
