"use client";

// Slice results and declared provenance are shown on several pages; their styles live beside them (tokens only).
import "@/styles/slices.css";
import { useMemo } from "react";
import { fmtFixed } from "@/lib/configurations/format";
import { runShare } from "@/lib/configurations/runs";
import type { RunView } from "@/lib/configurations/runs";
import { provenanceItems } from "@/lib/configurations/selectors";
import { evalSlices, isSliced, sliceResultsText } from "@/lib/configurations/slices";
import type { EvalSlice } from "@/lib/configurations/slices";
import type { EvalProvenance } from "@/lib/configurations/types";
import type { OfflineEvaluationDetail } from "@/lib/platform/client";
import { Missing } from "./Badges";
import { useOfflineDetails } from "./platform";
import type { Remote } from "./platform";
import { Tile } from "./Tiles";

/** An offline eval's slices once its episodes are read. */
export type RunSlices = { status: "loading" } | { status: "ready"; slices: EvalSlice[] } | { status: "error" };

function toSlices(remote: Remote<OfflineEvaluationDetail>): RunSlices {
  if (remote.status === "ready") return { status: "ready", slices: Array.isArray(remote.data.episodes) ? evalSlices(remote.data.episodes) : [] };
  return remote.status === "error" ? { status: "error" } : { status: "loading" };
}

/** The slices of each offline eval among `runs`, keyed by its id (other evals are absent): one shared read per eval. */
export function useRunSlices(runs: ReadonlyArray<RunView | null | undefined>): ReadonlyMap<string, RunSlices> {
  const details = useOfflineDetails(runs.map(run => run?.source === "offline" ? run.id : null));
  return useMemo(() => new Map([...details].map(([id, remote]) => [id, toSlices(remote)])), [details]);
}

/** The slices of a sliced eval, else null (not sliced, not an offline eval, or not read yet). */
export function slicedOf(slices: RunSlices | undefined): EvalSlice[] | null {
  return slices?.status === "ready" && isSliced(slices.slices) ? slices.slices : null;
}

/** Each slice's successes of its episodes, inline and wrapping: "Nominal 3/5  Pill count 30 0/5". */
export function SliceList({ slices }: { slices: readonly EvalSlice[] }) {
  return <span className="cv-slices" title={sliceResultsText(slices)}>
    {slices.map(slice => <span key={slice.key} className="cv-slices__item">
      <span className="cv-slices__name">{slice.label}</span> <b>{slice.successes}/{slice.episodes.length}</b>
    </span>)}
  </span>;
}

/**
 * An eval's success for a table cell or a card: per slice when its episodes form slices, else its
 * share (`counts`: with its successes of its episodes). An offline eval waits for its episodes, so a
 * sliced one never shows a pooled rate.
 */
export function RunSuccess({ run, slices, counts = false }: { run: Pick<RunView, "source" | "episodes" | "successes">; slices: RunSlices | undefined; counts?: boolean }) {
  if (run.source === "offline") {
    if (!slices || slices.status === "loading") return <Missing label="Loading" />;
    if (slices.status === "error") return <Missing />;
    const sliced = slicedOf(slices);
    if (sliced) return <SliceList slices={sliced} />;
  }
  const share = runShare(run);
  if (share === null) return <Missing />;
  return <>{fmtFixed(share * 100, 0)} %{counts && <span className="cv-muted"> · {run.successes}/{run.episodes}</span>}</>;
}

/**
 * The success tile of a robot's or a configuration's newest scored eval: "Success by slice" when its
 * episodes form slices (`slicedSub` names the eval), else its rate. An offline eval's tile waits for
 * its episodes and never falls back to a pooled rate or pooled counts.
 */
export function SuccessTile({ run, slices, slicedSub }: { run: RunView | null; slices: RunSlices | undefined; slicedSub: string }) {
  const sliced = slicedOf(slices);
  if (run && sliced) return <Tile label="Success by slice" value={<SliceList slices={sliced} />} sub={slicedSub} />;
  if (run?.source === "offline" && slices?.status !== "ready") {
    return <Tile label="Success rate" value={<Missing label={slices?.status === "error" ? "Not reported" : "Loading"} />} sub={run.label} />;
  }
  const share = run ? runShare(run) : null;
  return <Tile label="Success rate" value={share === null ? null : fmtFixed(share * 100, 0)} unit="%" sub={run ? `${run.label} · ${run.successes}/${run.episodes}` : "No evals"} />;
}

/**
 * How an eval ran, as its robot declares it, on one line: "MuJoCo planner run · Simulator-state
 * perception · Scripted IK · … · Runner: Mac · Transport: …". Nothing when none is declared.
 */
export function ProvenanceLine({ provenance }: { provenance: EvalProvenance | null | undefined }) {
  const items = provenanceItems(provenance);
  if (!items.length) return null;
  return <ul className="cv-provenance" aria-label="How this eval ran">
    {items.map(item => <li key={item.field} title={`${item.label}: declared on the robot, not checked by Convoy`}>{item.text}</li>)}
  </ul>;
}
