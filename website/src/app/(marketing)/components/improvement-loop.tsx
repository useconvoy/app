/**
 * The improvement loop, drawn on the dark band.
 *
 * Four stages and three strands, and the strands are the part that matters:
 * a row of four labels says the product has four stages, while a row of four
 * labels joined by what passes between them says the stages feed each other.
 * The arc underneath closes the circuit back to the first stage.
 *
 * Built from borders rather than an SVG so it reflows at any width without a
 * viewBox to keep in step, and rendered twice: a row on a wide screen, a
 * column on a narrow one. The copy is declared once.
 *
 * The greens tuned for paper go flat on this fill, so nodes and strands use
 * the brighter token and every label is near-white.
 */
import { Fragment } from "react";

/* The reference labels these RUN, TRACE, EVAL, IMPROVE. Two of those are
 * words this company uses with itself; the captions under them are the plain
 * ones, so the stages take their names from the captions. */
const STAGES = ["RUN", "RECORD", "SCORE", "IMPROVE"] as const;

const STRANDS = [
  "every step recorded",
  "scored against the routine",
  "ships to the next run",
] as const;

function Node({ label, lead }: { label: string; lead: boolean }) {
  return (
    <span className="inline-flex shrink-0 items-center gap-2 rounded-full border border-pass-bright/70 px-3.5 py-1.5 font-mono text-[11.5px] font-semibold tracking-[0.14em] text-card-alt">
      <span
        aria-hidden="true"
        className={`h-[7px] w-[7px] rounded-full ${
          lead ? "pulse-live bg-pass-bright" : "border border-pass-bright"
        }`}
      />
      {label}
    </span>
  );
}

export function ImprovementLoop() {
  return (
    <div aria-label="Run, trace, evaluate, improve, and back to the next run">
      {/* Wide: one row, strands labelled underneath, arc closing the loop. */}
      <div className="hidden md:block">
        <div className="grid grid-cols-[auto_1fr_auto_1fr_auto_1fr_auto] items-center">
          {STAGES.map((stage, index) => (
            <Fragment key={stage}>
              {index > 0 ? (
                <span
                  aria-hidden="true"
                  className="border-t border-dashed border-pass-bright/55"
                />
              ) : null}
              <Node label={stage} lead={index === 0} />
            </Fragment>
          ))}

          {/* Second row: a caption under each strand, nothing under a node. */}
          <span />
          {STRANDS.map((strand) => (
            <Fragment key={strand}>
              <span className="px-3 pt-2.5 text-center font-mono text-[11px] tracking-[0.06em] text-card-alt/70">
                {strand}
              </span>
              <span />
            </Fragment>
          ))}
        </div>

        {/* The return, closing the circuit from the last stage back to the
            first. Three sides of a rounded box is an arc, and it costs nothing
            to draw one that way. The insets put its ends under the centers of
            the stages it joins. */}
        <div className="relative mt-1.5 ml-9 h-7 rounded-b-[14px] border-x border-b border-dashed border-pass-bright/40 mr-[3.4rem]" />
      </div>

      {/* Narrow: a column, each strand carrying its own caption. */}
      <ol className="space-y-0 md:hidden">
        {STAGES.map((stage, index) => (
          <li key={stage}>
            <Node label={stage} lead={index === 0} />
            {index < STRANDS.length ? (
              <span className="my-1 flex items-center gap-3 pl-[18px]">
                <span
                  aria-hidden="true"
                  className="h-7 border-l border-dashed border-pass-bright/55"
                />
                <span className="font-mono text-[11px] tracking-[0.06em] text-card-alt/70">
                  {STRANDS[index]}
                </span>
              </span>
            ) : null}
          </li>
        ))}
      </ol>
    </div>
  );
}
