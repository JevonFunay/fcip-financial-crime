import type { CSSProperties } from "react";

const paths = {
  shield: "M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6l-8-3Z M9 12l2 2 4-4",
  grid: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
  upload: "M12 16V3 M7 8l5-5 5 5 M4 16v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4",
  scan: "M8 3H4a1 1 0 0 0-1 1v4 M16 3h4a1 1 0 0 1 1 1v4 M3 16v4a1 1 0 0 0 1 1h4 M21 16v4a1 1 0 0 1-1 1h-4 M7 12h3l2-4 2 8 2-4h1",
  alert: "m12 3 10 18H2L12 3Z M12 9v4 M12 17h.01",
  activity: "M3 12h4l3-8 4 16 3-8h4",
  history: "M3 10a9 9 0 1 1 2 8 M3 4v6h6 M12 7v5l3 2",
  "arrow-right": "M4 12h16 M14 6l6 6-6 6",
  "arrow-left": "M20 12H4 M10 6l-6 6 6 6",
  check: "m5 12 4 4L19 6",
  file: "M14 2H5a1 1 0 0 0-1 1v18a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1V8l-6-6Z M14 2v6h6 M8 13h8 M8 17h5",
  users:
    "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2 M16 3a4 4 0 0 1 0 8 M22 21v-2a4 4 0 0 0-3-4 M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z",
  wallet:
    "M20 8H4a2 2 0 0 1 0-4h14v4 M4 8a2 2 0 0 1-2-2v12a2 2 0 0 0 2 2h16V8 M20 12h-5v4h5",
  logout: "M9 4H4v16h5 M10 12h11 M17 8l4 4-4 4",
  menu: "M4 6h16 M4 12h16 M4 18h16",
  close: "m6 6 12 12 M6 18 18 6",
  chevron: "m9 5 7 7-7 7",
  search: "M21 21l-5-5 M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0Z",
  info: "M12 11v6 M12 7h.01 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z",
  clock: "M12 6v6l4 2 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z",
  layers: "m12 3 10 5-10 5L2 8l10-5Z M2 12l10 5 10-5 M2 16l10 5 10-5",
  eye: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z",
  "eye-off":
    "m3 3 18 18 M10 5c1-.2 1.3-.2 2-.2 6 0 10 7.2 10 7.2s-1 2-3 4 M6 6C3 8 2 12 2 12s4 7 10 7c2 0 3.5-.6 5-1.5 M10 10a3 3 0 0 0 4 4",
} as const;

export type IconName = keyof typeof paths;

export function Icon({
  name,
  size = 20,
  className = "",
  style,
}: {
  name: IconName;
  size?: number;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <svg
      className={`icon ${className}`}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.65"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      style={style}
    >
      <path d={paths[name]} />
    </svg>
  );
}
