/**
 * The route mark: a rule with three checkpoints, the first solid and the rest
 * open. One step done, two ahead, which is what a Convoy run looks like.
 *
 * The same motif appears as the run step list, the plan renderer, and the
 * run-history strip. It is never decoration.
 *
 * The open checkpoints are filled with the surface behind them rather than
 * left transparent, so the rule does not show through the middle of a node.
 * That means the surface has to be declared: on a card the mark sits on
 * `card`, everywhere else on `field`. Getting it wrong shows as a seam.
 */
export type MarkSurface = "field" | "card";

const SURFACE_CLASS: Record<MarkSurface, string> = {
  field: "fill-field",
  card: "fill-card",
};

export function RouteMark({
  width = 30,
  surface = "field",
  title,
  className,
}: {
  /** Rendered width in px; height follows the 30x14 ratio. */
  width?: number;
  /** The surface the mark sits on, so open checkpoints match it. */
  surface?: MarkSurface;
  /** Give this only when the mark stands alone as a link or image. Beside the
   * wordmark it is decorative and stays hidden from assistive technology. */
  title?: string;
  className?: string;
}) {
  const height = Math.round((width / 30) * 14 * 100) / 100;
  const open = SURFACE_CLASS[surface];

  return (
    <svg
      width={width}
      height={height}
      viewBox="0 0 30 14"
      fill="none"
      role={title ? "img" : undefined}
      aria-hidden={title ? undefined : true}
      className={className}
    >
      {title ? <title>{title}</title> : null}
      <line
        x1="1"
        y1="7"
        x2="29"
        y2="7"
        className="stroke-pine"
        strokeWidth="2"
      />
      <circle cx="5" cy="7" r="3.4" className="fill-pine" />
      <circle
        cx="15"
        cy="7"
        r="3.4"
        className={`${open} stroke-pine`}
        strokeWidth="2"
      />
      <circle
        cx="25"
        cy="7"
        r="3.4"
        className={`${open} stroke-pine`}
        strokeWidth="2"
      />
    </svg>
  );
}
