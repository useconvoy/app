/**
 * View helpers for the Configurations index (`/app/configurations`): the URL
 * filters, a card's stack rows, one-line verdict, robot counts and attention
 * summary, and the labels and links of the activity feed. Pure functions over the
 * workspace; robots that need attention come from `attentionRobots`, the same
 * selector the dashboard banner and the health badges use.
 */
import { routes } from "./routes";
import type { AttentionRobot, ConfigFilter, ConfigSort } from "./selectors";
import { currentRevision, getConfiguration, getRobot, getRun, getSuite, latestGateRun, robotHref, robotsFor, runHref } from "./selectors";
import type { ActivityEvent, ActivityKind, Configuration, ConvoyWorkspace, Provenance } from "./types";

/* ---------- filters (query parameters ?q=, ?status=, ?sort=) ---------- */

export const INDEX_QUERY = { search: "q", status: "status", sort: "sort" } as const;
export const STATUS_FILTERS: ReadonlyArray<{ id: ConfigFilter; label: string }> = [
  { id: "all", label: "All" }, { id: "testing", label: "Testing" }, { id: "production", label: "In production" }, { id: "draft", label: "Draft" },
];
export const SORT_OPTIONS: ReadonlyArray<{ value: ConfigSort; label: string }> = [
  { value: "activity", label: "Last activity" }, { value: "name", label: "Name" }, { value: "eval", label: "Latest eval" },
];
export const parseFilter = (value: string | null | undefined): ConfigFilter => STATUS_FILTERS.find(item => item.id === value)?.id ?? "all";
export const parseSort = (value: string | null | undefined): ConfigSort => SORT_OPTIONS.find(item => item.value === value)?.value ?? "activity";

const FILTER_NOUN: Record<Exclude<ConfigFilter, "all">, string> = { testing: "testing configurations", production: "configurations in production", draft: "draft configurations" };
/** Empty-result title: "No configurations match “spot”." / "No draft configurations." */
export function noResultsTitle(query: string, filter: ConfigFilter): string {
  const noun = filter === "all" ? "configurations" : FILTER_NOUN[filter];
  return query.trim() ? `No ${noun} match “${query.trim()}”.` : `No ${noun}.`;
}

/* ---------- configuration cards ---------- */

const join = (items: ReadonlyArray<{ shortName: string }>) => items.map(item => item.shortName).join(" · ");

/** Robot, edge hardware, edge models and cloud models of the revision a card describes (under test, else production). */
export function stackRows(config: Configuration): Array<{ label: string; value: string }> {
  const revision = currentRevision(config);
  const mode = revision.edgeHardware.powerModes.find(item => item.id === revision.edgeHardware.powerModeId);
  return [
    { label: "Robot", value: `${revision.robot.name} · ${revision.robot.summary}` },
    { label: "Edge hardware", value: mode ? `${revision.edgeHardware.name} · ${mode.label} mode` : revision.edgeHardware.name },
    { label: "Edge models", value: join(revision.edgeModels) || "None" },
    { label: "Cloud models", value: join(revision.cloudModels) || (revision.routing.mode === "edge-only" ? "None (edge only)" : "None") },
  ];
}

export interface CardVerdict { tone: "good" | "warning" | "blocked"; lead: string; text: string; provenance?: Provenance | null }
/**
 * The card's one-line verdict: the stored highlight, else the newest gate result
 * (the first criterion missed, with the run's provenance), else none.
 */
export function cardVerdict(ws: ConvoyWorkspace, config: Configuration): CardVerdict | null {
  if (config.highlight) return config.highlight;
  const run = latestGateRun(ws, config.id, config.candidateRev) ?? latestGateRun(ws, config.id);
  if (!run?.gate) return null;
  const suite = getSuite(ws, run.suiteId);
  const suiteName = suite ? `${suite.name} ${suite.version}` : run.title;
  if (run.gate.passed) return { tone: "good", lead: "Passed gate.", text: `${run.rev} passed the ${suiteName} gate in Run ${run.number}.`, provenance: run.provenance };
  const missed = run.gate.results.find(result => !result.passed);
  const criterion = suite?.gate.find(item => item.id === missed?.criterionId);
  return {
    tone: "warning", lead: "Below gate.", provenance: run.provenance,
    text: missed && criterion ? `${run.rev} missed “${criterion.label} ${criterion.target}” in Run ${run.number}: ${missed.actual}.` : `${run.rev} did not pass the ${suiteName} gate in Run ${run.number}.`,
  };
}

/** "Test 2 · Production 4", naming a role's only robot: "Test 1 (Sim 01) · Production 0". */
export function robotCountsLabel(ws: ConvoyWorkspace, configId: string): string {
  const robots = robotsFor(ws, configId);
  const part = (role: "test" | "production", label: string) => {
    const list = robots.filter(robot => robot.role === role);
    return `${label} ${list.length}${list.length === 1 ? ` (${list[0].name})` : ""}`;
  };
  return `${part("test", "Test")} · ${part("production", "Production")}`;
}

/**
 * Robots that need attention (displayed health Needs attention) and robots with
 * warnings (Degraded), plus "Unit 08: Near thermal throttle · …" for a title.
 * Pass `attentionRobots(…)`: the counts then match the health badges.
 */
export function attentionCounts(entries: readonly AttentionRobot[]): { attention: number; warning: number; attentionLabel: string; warningLabel: string; detail: string } {
  const attention = entries.filter(entry => entry.severity === "attention").length, warning = entries.length - attention;
  return {
    attention, warning,
    attentionLabel: `${attention} ${attention === 1 ? "needs" : "need"} attention`,
    warningLabel: `${warning} ${warning === 1 ? "warning" : "warnings"}`,
    detail: entries.map(entry => `${entry.robot.name}: ${entry.label}`).join(" · "),
  };
}

/* ---------- activity feed ---------- */

export type FeedTone = "neutral" | "success" | "warning" | "info" | "forest";
export const ACTIVITY_LABEL: Record<ActivityKind, { label: string; tone: FeedTone; flag?: boolean }> = {
  flagged: { label: "Flagged", tone: "warning", flag: true },
  "flag-cleared": { label: "Flag cleared", tone: "neutral" },
  "run-queued": { label: "Queued", tone: "neutral" },
  "run-started": { label: "Started", tone: "info" },
  "passed-gate": { label: "Passed gate", tone: "success" },
  "below-gate": { label: "Below gate", tone: "warning" },
  promoted: { label: "Promoted", tone: "forest" },
  "configuration-created": { label: "Created", tone: "neutral" },
  "robot-added": { label: "Robot added", tone: "neutral" },
  "role-changed": { label: "Role changed", tone: "neutral" },
  reconnected: { label: "Reconnected", tone: "success" },
};

/** The event's subject as a label and, when it still resolves, a link: robot page, run page or configuration. */
export function activitySubject(ws: ConvoyWorkspace, event: Pick<ActivityEvent, "subject">): { label: string; href: string | null } {
  const { type, id } = event.subject;
  if (type === "robot") {
    const robot = getRobot(ws, id);
    return { label: robot?.name ?? id, href: robot ? robotHref(robot) : null };
  }
  if (type === "run") {
    const run = getRun(ws, id);
    return { label: run ? `Run ${run.number}` : id, href: run ? runHref(ws, run) : null };
  }
  const config = getConfiguration(ws, id);
  return { label: config?.name ?? id, href: config ? routes.configuration(config.id) : null };
}
