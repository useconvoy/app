/** The cookbook's 16 × 16 line icons (stroke 1.5, round caps; one path each). No other icon set is used. */
export const ICON_PATHS = {
  flag: "M3.5 14V2.5M3.5 3h8.5l-2 3 2 3H3.5",
  "arrow-up-right": "M5 11 11 5M6 5h5v5",
  plus: "M8 3v10M3 8h10",
  search: "M2.75 7a4.25 4.25 0 1 0 8.5 0a4.25 4.25 0 1 0-8.5 0M10.25 10.25 13.5 13.5",
  play: "M5 3.5v9l7-4.5z",
  pause: "M5.5 3.5v9M10.5 3.5v9",
  "step-forward": "M4 3.5v9l6-4.5zM12.5 3.5v9",
  "step-back": "M12 3.5v9L6 8zM3.5 3.5v9",
  check: "m3 8.5 3.25 3.25L13 5",
  warning: "M8 2.5 14 13.5H2zM8 6.5v3.25M8 11.75v.01",
  blocked: "M2.5 8a5.5 5.5 0 1 0 11 0a5.5 5.5 0 1 0-11 0M4.1 11.9l7.8-7.8",
  "chevron-right": "M6 3.5 10.5 8 6 12.5",
  external: "M9.5 2.5h4v4M13.5 2.5 7.5 8.5M12 9.5v4H2.5V4h4",
  close: "m4 4 8 8M12 4l-8 8",
  info: "M2.5 8a5.5 5.5 0 1 0 11 0a5.5 5.5 0 1 0-11 0M8 7.5v3.25M8 5.25v.01",
  clock: "M2.5 8a5.5 5.5 0 1 0 11 0a5.5 5.5 0 1 0-11 0M8 5v3.25l2.25 1.5",
  sort: "M5.5 6 8 3.5 10.5 6M5.5 10 8 12.5 10.5 10",
  "sort-down": "M8 3v10M4.5 9.5 8 13l3.5-3.5",
  "sort-up": "M8 13V3M4.5 6.5 8 3l3.5 3.5",
} as const;
export type IconName = keyof typeof ICON_PATHS;

/** Decorative icon (aria-hidden). Give an icon-only button its own `aria-label`. */
export function Icon({ name, small = false, className = "" }: { name: IconName; small?: boolean; className?: string }) {
  return <svg className={`cfg-icon${small ? " cfg-icon--sm" : ""}${className ? ` ${className}` : ""}`} viewBox="0 0 16 16" aria-hidden="true"><path d={ICON_PATHS[name]} /></svg>;
}

/** The Convoy mark drawn with tokens (oxide square, paper "C"). */
export function ConvoyMark() {
  return <svg className="cfg-mark" viewBox="0 0 64 64" aria-hidden="true"><rect width="64" height="64" rx="14" /><path d="M44 16H22a6 6 0 0 0-6 6v20a6 6 0 0 0 6 6h22" /></svg>;
}
