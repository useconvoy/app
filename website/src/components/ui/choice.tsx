import type { ComponentProps, ReactNode } from "react";

/**
 * Checkbox and radio, drawn by the design system.
 *
 * Both are real inputs styled with `appearance-none`, so keyboard, forms,
 * and assistive tech see exactly what they always saw; only the paint is
 * ours. The check glyph is an SVG sibling revealed by `peer-checked` rather
 * than a background image, because a data-URI background would need a hex
 * literal and the token gate exists precisely to stop those.
 *
 * The radio's dot needs no extra element at all: when checked, the border
 * thickens to 5px in pine and the card-colored center becomes the dot.
 *
 * Each control ships with its label, because a bare 18px square is not a
 * touch target and a caption two elements away is not a label. The text is
 * ordinary sans, not the field-label mono: these read as sentences a person
 * agrees to, not as column headings.
 */
type ChoiceProps = Omit<ComponentProps<"input">, "type" | "children"> & {
  label: ReactNode;
  /** Smaller secondary line under the label. */
  hint?: string;
};

const BOX =
  "peer size-[18px] shrink-0 appearance-none rounded-[4px] border border-line bg-card " +
  "checked:border-pine checked:bg-pine " +
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-pass " +
  "disabled:cursor-not-allowed disabled:opacity-50";

export function Checkbox({ label, hint, className, ...rest }: ChoiceProps) {
  return (
    <label className={["flex cursor-pointer gap-2.5", className ?? ""].join(" ")}>
      {/* self-start: the label is a stretch flex row, and a hint line makes
          it taller than the box; without this the wrapper stretches and the
          centered glyph lands below the box instead of inside it. */}
      <span className="relative mt-0.5 inline-flex shrink-0 self-start">
        <input type="checkbox" {...rest} className={BOX} />
        <svg
          aria-hidden="true"
          width="11"
          height="9"
          viewBox="0 0 11 9"
          fill="none"
          className="pointer-events-none absolute inset-0 m-auto opacity-0 peer-checked:opacity-100"
        >
          <path
            d="M1.5 4.6 4.2 7.2 9.5 1.6"
            className="stroke-card-alt"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </span>
      <span className="min-w-0">
        <span className="block text-sm leading-snug text-ink">{label}</span>
        {hint ? <span className="mt-0.5 block text-xs text-muted">{hint}</span> : null}
      </span>
    </label>
  );
}

export function Radio({ label, hint, className, ...rest }: ChoiceProps) {
  return (
    <label className={["flex cursor-pointer gap-2.5", className ?? ""].join(" ")}>
      <input
        type="radio"
        {...rest}
        className={
          "mt-0.5 size-[18px] shrink-0 appearance-none rounded-full border border-line bg-card " +
          "checked:border-[5px] checked:border-pine " +
          "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-pass " +
          "disabled:cursor-not-allowed disabled:opacity-50"
        }
      />
      <span className="min-w-0">
        <span className="block text-sm leading-snug text-ink">{label}</span>
        {hint ? <span className="mt-0.5 block text-xs text-muted">{hint}</span> : null}
      </span>
    </label>
  );
}
