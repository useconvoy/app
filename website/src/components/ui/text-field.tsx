import type { ComponentProps } from "react";

import { CONTROL } from "./field";

/** Single-line text, email, url, date: the shared control skin, unchanged. */
export function TextField({
  className,
  ...rest
}: ComponentProps<"input">) {
  return <input {...rest} className={[CONTROL, className ?? ""].join(" ")} />;
}

/**
 * Numbers wear the mono the rest of the system reserves for figures, with
 * tabular digits so a value edit does not change the field's rhythm. The
 * platform spinner is hidden: two 8px arrows are not a usable control and
 * every value here (dollar caps, thresholds) is typed, not nudged.
 */
export function NumberField({
  className,
  ...rest
}: ComponentProps<"input">) {
  return (
    <input
      type="number"
      inputMode="decimal"
      {...rest}
      className={[
        CONTROL,
        "font-mono tabular-nums [appearance:textfield]",
        "[&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none",
        className ?? "",
      ].join(" ")}
    />
  );
}

/** Multi-line text. Resizable downward only, so it cannot break the row above. */
export function Textarea({
  className,
  ...rest
}: ComponentProps<"textarea">) {
  return (
    <textarea
      {...rest}
      className={[CONTROL, "min-h-20 resize-y leading-relaxed", className ?? ""].join(" ")}
    />
  );
}
