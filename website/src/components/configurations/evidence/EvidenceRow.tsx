"use client";

import Link from "next/link";
import { useId } from "react";
// The evidence panels' styles live beside them (one file, tokens only), so the shared stylesheets stay untouched.
import "@/styles/evidence.css";
import { getConfiguration, offlineProvenance, provenanceItems, useWorkspace } from "@/lib/configurations/client";
import { autonomyFromEpisodes, latencyBudgetFromEpisodes, latencyBudgetFromInference, latencyTargets } from "@/lib/configurations/evidence";
import { fmtCount } from "@/lib/configurations/format";
import { routes } from "@/lib/configurations/routes";
import { platformPaths } from "@/lib/configurations/runs";
import type { RunView } from "@/lib/configurations/runs";
import { evalSlices, isSliced, pickSlice, plannerCalls } from "@/lib/configurations/slices";
import type { EvalSlice } from "@/lib/configurations/slices";
import type { Configuration, Robot } from "@/lib/configurations/types";
import type { OfflineEpisode, OfflineEvaluationDetail } from "@/lib/platform/client";
import { useQueryState } from "../hooks";
import { usePlatform } from "../platform";
import { ProvenanceLine } from "../SliceResults";
import type { RobotView } from "../useRobots";
import { AutonomyPanel } from "./Autonomy";
import { LatencyBudgetPanel } from "./LatencyBudget";

/** Which slice the panels show: one radio per slice (a segmented control). */
function SliceSwitch({ slices, value, onChange }: { slices: readonly EvalSlice[]; value: string; onChange: (key: string) => void }) {
  const id = useId();
  return <div className="cv-ev-slices" role="radiogroup" aria-labelledby={`${id}-label`}>
    <span id={`${id}-label`} className="cv-ev-slices__label">Slice</span>
    <span className="cv-segment cv-segment--inline">
      {slices.map(slice => <label key={slice.key} className="cv-segment__item">
        <input type="radio" name={`${id}-slice`} value={slice.key} checked={slice.key === value} onChange={() => onChange(slice.key)} />{slice.label}
      </label>)}
    </span>
  </div>;
}

/**
 * The episodes the panels read: a sliced eval's chosen slice (`?slice=`, else its first), never its
 * slices pooled; all of an eval that is not sliced. Choosing a slice replaces the URL's query.
 */
function useSliceChoice(episodes: readonly OfflineEpisode[] | null) {
  const [chosen, setChosen] = useQueryState("slice");
  const slices = episodes ? evalSlices(episodes) : [];
  const slice = isSliced(slices) ? pickSlice(slices, chosen) : null;
  const choose = (key: string) => setChosen(key === slices[0]?.key ? null : key, { replace: true });
  return { slices, slice, shown: slice ? slice.episodes : episodes, choose };
}

/**
 * The eval page's evidence (Overview): the latency budget and autonomy of an offline eval whose
 * episodes report planner and safety metrics, side by side and equal in height, for one slice at a
 * time when the episodes form slices. Nothing for other evals. The evaluation is the one the page
 * already read (the platform cache serves it again).
 */
export function EvalEvidence({ robot, view }: { robot: Robot; view: RunView }) {
  const ws = useWorkspace();
  const config = ws.workspace && robot.configId ? getConfiguration(ws.workspace, robot.configId) : null;
  const detail = usePlatform<OfflineEvaluationDetail>(view.source === "offline" ? platformPaths.offlineEvaluation(view.id) : null);
  const episodes = detail.state.status === "ready" && Array.isArray(detail.state.data.episodes) ? detail.state.data.episodes : null;
  const { slices, slice, shown, choose } = useSliceChoice(episodes);
  // Small pure derivations (tens of episodes): computed on render.
  const any = !!episodes && (latencyBudgetFromEpisodes(episodes) !== null || autonomyFromEpisodes(episodes) !== null);
  const latency = shown ? latencyBudgetFromEpisodes(shown) : null;
  const autonomy = shown ? autonomyFromEpisodes(shown) : null;
  if (!any) return null;
  return <div className="cv-ev-row">
    {slice && <div className="cv-ev-bar"><SliceSwitch slices={slices} value={slice.key} onChange={choose} /></div>}
    {latency && <LatencyBudgetPanel budget={latency} targets={latencyTargets(config)} slice={slice?.label} />}
    {autonomy && <AutonomyPanel autonomy={autonomy} calls={shown ? plannerCalls(shown) : null} slice={slice?.label} />}
    {!latency && !autonomy && <p className="cv-ev-none">No planner or safety counts in this slice.</p>}
  </div>;
}

/** The newest offline eval any of the configuration's robots links, with its robot. */
function newestOffline(views: readonly RobotView[]): { run: RunView; robot: Robot } | null {
  const time = (at: string | null) => (at ? Date.parse(at) : Number.NaN) || 0;
  return views.flatMap(view => view.runs.filter(run => run.source === "offline").map(run => ({ run, robot: view.robot })))
    .toSorted((a, b) => time(b.run.at) - time(a.run.at))[0] ?? null;
}

/**
 * The configuration dashboard's evidence, compact: the latency budget of its newest offline eval that
 * reports planner latency, else of its live device's latest requests; and that eval's autonomy. A
 * sliced eval shows one slice at a time; how the eval ran (declared on its robot) heads the row.
 * Nothing when neither exists. While the robots' evals or that eval are read the row waits, so it
 * never shows the live device's figures and then switches to the eval's.
 */
export function ConfigEvidence({ config, views }: { config: Configuration; views: readonly RobotView[] }) {
  const latest = newestOffline(views);
  const detail = usePlatform<OfflineEvaluationDetail>(latest ? platformPaths.offlineEvaluation(latest.run.id) : null);
  const episodes = detail.state.status === "ready" && Array.isArray(detail.state.data.episodes) ? detail.state.data.episodes : null;
  const { slices, slice, shown, choose } = useSliceChoice(episodes);
  const live = views.find(view => view.robot.deviceId && view.readings.live) ?? null;
  const spans = live?.live.data?.inference ?? null;
  // Small pure derivations (tens of episodes or spans): computed on render. An eval with planner latency
  // in any slice keeps to its own figures, so a slice without them never borrows the live device's.
  const evalLatency = !!episodes && latencyBudgetFromEpisodes(episodes) !== null;
  const fromEval = shown ? latencyBudgetFromEpisodes(shown) : null;
  const fromLive = !evalLatency && spans ? latencyBudgetFromInference(spans) : null;
  const autonomy = shown ? autonomyFromEpisodes(shown) : null;
  if (views.some(view => view.runsLoading) || (latest && (detail.state.status === "loading" || detail.state.status === "idle"))) return null;
  const budget = fromEval ?? fromLive;
  // The eval's own row: its slices to choose from and how it ran. A live device's figures have neither.
  const fromThisEval = evalLatency || (!!episodes && autonomyFromEpisodes(episodes) !== null);
  const chosen = fromThisEval ? slice : null;
  if (!budget && !autonomy && !chosen) return null;
  const evalLink = latest && <Link className="cv-link" href={routes.run(config.id, latest.robot.id, latest.run.id)}>{latest.run.label} · {latest.robot.name}</Link>;
  const liveLink = live && <Link className="cv-link" href={routes.robot(config.id, live.robot.id, "traces")}>{live.robot.name} · {fmtCount(fromLive?.n ?? 0)} requests</Link>;
  const provenance = latest && fromThisEval ? offlineProvenance(latest.robot, latest.run.id) : null;
  return <section className="cv-ev-row cv-ev-row--dashboard" aria-label="Latency and autonomy">
    {(chosen || provenanceItems(provenance).length > 0) && <div className="cv-ev-bar">
      <ProvenanceLine provenance={provenance} />
      {chosen && <SliceSwitch slices={slices} value={chosen.key} onChange={choose} />}
    </div>}
    {budget && <LatencyBudgetPanel compact budget={budget} targets={latencyTargets(config)} slice={fromEval ? chosen?.label : undefined} source={fromEval ? evalLink : liveLink} />}
    {autonomy && <AutonomyPanel compact autonomy={autonomy} calls={shown ? plannerCalls(shown) : null} slice={chosen?.label} source={evalLink} />}
    {!budget && !autonomy && <p className="cv-ev-none">No planner or safety counts in this slice.</p>}
  </section>;
}
