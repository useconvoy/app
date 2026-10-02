"use client";

import Link from "next/link";
// The evidence panels' styles live beside them (one file, tokens only), so the shared stylesheets stay untouched.
import "@/styles/evidence.css";
import { getConfiguration, useWorkspace } from "@/lib/configurations/client";
import { autonomyFromEpisodes, latencyBudgetFromEpisodes, latencyBudgetFromInference, latencyTargets } from "@/lib/configurations/evidence";
import { fmtCount } from "@/lib/configurations/format";
import { routes } from "@/lib/configurations/routes";
import { platformPaths } from "@/lib/configurations/runs";
import type { RunView } from "@/lib/configurations/runs";
import type { Configuration, Robot } from "@/lib/configurations/types";
import type { OfflineEvaluationDetail } from "@/lib/platform/client";
import { usePlatform } from "../platform";
import type { RobotView } from "../useRobots";
import { AutonomyPanel } from "./Autonomy";
import { LatencyBudgetPanel } from "./LatencyBudget";

/**
 * The eval page's evidence (Overview): the latency budget and autonomy of an offline eval whose
 * episodes report planner and safety metrics, side by side and equal in height. Nothing for other
 * evals. The evaluation is the one the page already read (the platform cache serves it again).
 */
export function EvalEvidence({ robot, view }: { robot: Robot; view: RunView }) {
  const ws = useWorkspace();
  const config = ws.workspace && robot.configId ? getConfiguration(ws.workspace, robot.configId) : null;
  const detail = usePlatform<OfflineEvaluationDetail>(view.source === "offline" ? platformPaths.offlineEvaluation(view.id) : null);
  const episodes = detail.state.status === "ready" && Array.isArray(detail.state.data.episodes) ? detail.state.data.episodes : null;
  const latency = episodes ? latencyBudgetFromEpisodes(episodes) : null;
  const autonomy = episodes ? autonomyFromEpisodes(episodes) : null;
  if (!latency && !autonomy) return null;
  return <div className="cv-ev-row">
    {latency && <LatencyBudgetPanel budget={latency} targets={latencyTargets(config)} />}
    {autonomy && <AutonomyPanel autonomy={autonomy} />}
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
 * reports planner latency, else of its live device's latest requests; and that eval's autonomy. Nothing
 * when neither exists. While the robots' evals or that eval are read the row waits, so it never shows
 * the live device's figures and then switches to the eval's.
 */
export function ConfigEvidence({ config, views }: { config: Configuration; views: readonly RobotView[] }) {
  const latest = newestOffline(views);
  const detail = usePlatform<OfflineEvaluationDetail>(latest ? platformPaths.offlineEvaluation(latest.run.id) : null);
  const episodes = detail.state.status === "ready" && Array.isArray(detail.state.data.episodes) ? detail.state.data.episodes : null;
  const live = views.find(view => view.robot.deviceId && view.readings.live) ?? null;
  const spans = live?.live.data?.inference ?? null;
  // Small pure derivations (tens of episodes or spans): computed on render.
  const fromEval = episodes ? latencyBudgetFromEpisodes(episodes) : null;
  const fromLive = spans ? latencyBudgetFromInference(spans) : null;
  const autonomy = episodes ? autonomyFromEpisodes(episodes) : null;
  if (views.some(view => view.runsLoading) || (latest && (detail.state.status === "loading" || detail.state.status === "idle"))) return null;
  const budget = fromEval ?? fromLive;
  if (!budget && !autonomy) return null;
  const evalLink = latest && <Link className="cv-link" href={routes.run(config.id, latest.robot.id, latest.run.id)}>{latest.run.label} · {latest.robot.name}</Link>;
  const liveLink = live && <Link className="cv-link" href={routes.robot(config.id, live.robot.id, "traces")}>{live.robot.name} · {fmtCount(fromLive?.n ?? 0)} requests</Link>;
  return <section className="cv-ev-row cv-ev-row--dashboard" aria-label="Latency and autonomy">
    {budget && <LatencyBudgetPanel compact budget={budget} targets={latencyTargets(config)} source={fromEval ? evalLink : liveLink} />}
    {autonomy && <AutonomyPanel compact autonomy={autonomy} source={evalLink} />}
  </section>;
}
