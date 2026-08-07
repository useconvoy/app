import type { ComponentProps } from "react";

import { CONTROL } from "./field";

/**
 * The select, wearing the system instead of the operating system.
 *
 * This stays a real <select>. The options panel is the one part of the
 * control the platform owns outright, and that is worth keeping: on a phone
 * it is the native wheel, with assistive tech it is the native listbox, and
 * neither needs a line of our JavaScript. What was wrong before was the
 * closed control: `appearance-none` takes back the box, and the chevron is
 * our own glyph in our own ink instead of the platform's.
 *
 * The panel now renders light everywhere because the root declares
 * `color-scheme: light`; before that, a dark-mode OS painted its charcoal
 * menu under our paper page.
 */
export function Select({
  className,
  children,
  ...rest
}: ComponentProps<"select">) {
  return (
    <span className="relative block min-w-0">
      <select
        {...rest}
        className={["appearance-none pr-9", CONTROL, className ?? ""].join(" ")}
      >
        {children}
      </select>
      <svg
        aria-hidden="true"
        width="10"
        height="6"
        viewBox="0 0 10 6"
        fill="none"
        className="pointer-events-none absolute top-1/2 right-3.5 -translate-y-1/2"
      >
        <path
          d="M1 1l4 4 4-4"
          className="stroke-muted"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </span>
  );
}
