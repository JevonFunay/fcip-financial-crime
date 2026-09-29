export function formatAmount(amount: string, currency: string): string {
  const value = Number(amount);
  if (Number.isNaN(value)) {
    return `${currency} ${amount}`;
  }
  return new Intl.NumberFormat("id-ID", { style: "currency", currency, maximumFractionDigits: 2 }).format(value);
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) {
    return "-";
  }
  return new Date(iso).toLocaleString("id-ID", { dateStyle: "medium", timeStyle: "short" });
}

export function formatDetailValue(value: unknown): string {
  if (value === null || value === undefined) {
    return "-";
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

/** "P7D" -> "7 days", "PT36H" -> "36 hours". Anything else is shown as sent. */
export function formatDuration(iso: string): string {
  const match = /^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?$/.exec(iso);
  if (!match || match.slice(1).every((part) => part === undefined)) {
    return iso;
  }
  const units: [string | undefined, string][] = [
    [match[1], "day"],
    [match[2], "hour"],
    [match[3], "minute"],
  ];
  return units
    .filter(([value]) => value !== undefined)
    .map(([value, unit]) => `${value} ${unit}${value === "1" ? "" : "s"}`)
    .join(" ");
}
