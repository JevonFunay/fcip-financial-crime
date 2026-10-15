export function formatAmount(amount: string, currency: string): string {
  const value = Number(amount);
  if (Number.isNaN(value)) {
    return `${currency} ${amount}`;
  }
  if (currency === "XXX")
    return `${value.toLocaleString("id-ID")} (mata uang belum diketahui)`;
  return new Intl.NumberFormat("id-ID", {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(value);
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) {
    return "-";
  }
  return new Date(iso).toLocaleString("id-ID", {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

/** UTC can be a carrier for an unknown source clock, not a claimed timezone. */
export function formatObservedDateTime(
  iso: string,
  precision?: string | null,
  sourceTimezoneKnown?: boolean | null,
): string {
  const value = new Date(iso);
  if (precision === "day" || precision === "week") {
    return (
      value.toLocaleDateString("id-ID", {
        dateStyle: "medium",
        timeZone: "UTC",
      }) + (precision === "week" ? " · kalender sumber" : " · tanggal sumber")
    );
  }
  if (sourceTimezoneKnown === false) {
    return (
      value.toLocaleString("id-ID", {
        dateStyle: "medium",
        timeStyle: "short",
        timeZone: "UTC",
      }) + " · zona waktu sumber belum diketahui"
    );
  }
  return formatDateTime(iso);
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
