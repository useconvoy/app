/**
 * Structural validator for `ConvoyWorkspace` schema version 1, written by hand
 * so error paths are precise ("robots[3].telemetry.latest.socTempC: expected a
 * finite number or null"). It checks shapes, enumerations, references between
 * collections, uniqueness, and the evidence rules: a stored provenance is never
 * "measured", and a robot with a live device binding stores no telemetry.
 * Unknown keys are reported as warnings and kept.
 */
import { CONFIGURED_DEVICE, ID_PATTERN, WORKSPACE_SCHEMA_VERSION } from "./types";
import type { ConvoyWorkspace } from "./types";

export interface ValidationIssue { path: string; message: string }
export type ValidationResult =
  | { ok: true; workspace: ConvoyWorkspace; warnings: ValidationIssue[] }
  | { ok: false; issues: ValidationIssue[]; warnings: ValidationIssue[] };

const MAX_ISSUES = 100;
type Data = Record<string, unknown>;

class Context {
  issues: ValidationIssue[] = [];
  warnings: ValidationIssue[] = [];
  issue(path: string, message: string) { if (this.issues.length < MAX_ISSUES) this.issues.push({ path, message }); }
  warn(path: string, message: string) { if (this.warnings.length < MAX_ISSUES) this.warnings.push({ path, message }); }
}

type Check = (value: unknown, path: string, ctx: Context) => void;
interface Field { check: Check; optional: boolean }

const isRecord = (value: unknown): value is Data => typeof value === "object" && value !== null && !Array.isArray(value);
const join = (path: string, key: string | number) => typeof key === "number" ? `${path}[${key}]` : path ? `${path}.${key}` : key;

/* ---------- primitive checks ---------- */

const str: Check = (v, p, ctx) => { if (typeof v !== "string" || !v.trim()) ctx.issue(p, "expected a non-empty string"); };
const text: Check = (v, p, ctx) => { if (typeof v !== "string") ctx.issue(p, "expected a string"); };
const id: Check = (v, p, ctx) => { if (typeof v !== "string" || !ID_PATTERN.test(v)) ctx.issue(p, "expected an id of 1–64 letters, digits, '_' or '-'"); };
const bool: Check = (v, p, ctx) => { if (typeof v !== "boolean") ctx.issue(p, "expected true or false"); };
const time: Check = (v, p, ctx) => { if (typeof v !== "string" || !/^\d{4}-\d{2}-\d{2}T/.test(v) || !Number.isFinite(Date.parse(v))) ctx.issue(p, "expected an ISO 8601 date-time"); };
function num(options: { min?: number; max?: number; integer?: boolean } = {}): Check {
  return (v, p, ctx) => {
    if (typeof v !== "number" || !Number.isFinite(v)) return ctx.issue(p, options.integer ? "expected an integer" : "expected a finite number");
    if (options.integer && !Number.isInteger(v)) return ctx.issue(p, "expected an integer");
    if (options.min !== undefined && v < options.min) ctx.issue(p, `expected a number ≥ ${options.min}`);
    if (options.max !== undefined && v > options.max) ctx.issue(p, `expected a number ≤ ${options.max}`);
  };
}
const number = num();
const count = num({ min: 0, integer: true });
const nonNegative = num({ min: 0 });
const percent = num({ min: 0, max: 100 });
const share = num({ min: 0, max: 1 });

function nullable(check: Check, label?: string): Check {
  return (v, p, ctx) => {
    if (v === null) return;
    const before = ctx.issues.length;
    check(v, p, ctx);
    if (label && ctx.issues.length > before) ctx.issues[ctx.issues.length - 1].message = `${label} or null`;
  };
}
const nullableNumber = nullable(number, "expected a finite number");
const nullableNonNegative = nullable(nonNegative, "expected a number ≥ 0");
const nullablePercent = nullable(percent, "expected a percentage between 0 and 100");
const nullableString = nullable(str, "expected a non-empty string");
const nullableTime = nullable(time, "expected an ISO 8601 date-time");
const nullableId = nullable(id, "expected an id");

function oneOf<T extends string>(values: readonly T[]): Check {
  return (v, p, ctx) => { if (typeof v !== "string" || !values.includes(v as T)) ctx.issue(p, `expected one of ${values.map(value => `"${value}"`).join(", ")}`); };
}
function arr(item: Check, options: { min?: number; max?: number } = {}): Check {
  return (v, p, ctx) => {
    if (!Array.isArray(v)) return ctx.issue(p, "expected an array");
    if (options.min !== undefined && v.length < options.min) ctx.issue(p, `expected at least ${options.min} item${options.min === 1 ? "" : "s"}`);
    if (options.max !== undefined && v.length > options.max) ctx.issue(p, `expected at most ${options.max} items`);
    v.forEach((value, i) => item(value, join(p, i), ctx));
  };
}
const req = (check: Check): Field => ({ check, optional: false });
const opt = (check: Check): Field => ({ check, optional: true });
function obj(shape: Record<string, Field>, extra?: (value: Data, path: string, ctx: Context) => void): Check {
  return (v, p, ctx) => {
    if (!isRecord(v)) return ctx.issue(p, "expected an object");
    for (const [key, field] of Object.entries(shape)) {
      if (v[key] === undefined) { if (!field.optional) ctx.issue(join(p, key), "is required"); continue; }
      field.check(v[key], join(p, key), ctx);
    }
    for (const key of Object.keys(v)) if (!(key in shape)) ctx.warn(join(p, key), "is not part of schema version 1 and is ignored");
    extra?.(v, p, ctx);
  };
}
function record(keys: readonly string[], item: Check): Check {
  return (v, p, ctx) => {
    if (!isRecord(v)) return ctx.issue(p, "expected an object");
    for (const [key, value] of Object.entries(v)) {
      if (!keys.includes(key)) ctx.issue(join(p, key), `expected one of ${keys.map(k => `"${k}"`).join(", ")}`);
      else item(value, join(p, key), ctx);
    }
  };
}
const tuple2Share: Check = (v, p, ctx) => {
  if (!Array.isArray(v) || v.length !== 2) return ctx.issue(p, "expected [low, high] shares between 0 and 1");
  share(v[0], join(p, 0), ctx); share(v[1], join(p, 1), ctx);
  if (typeof v[0] === "number" && typeof v[1] === "number" && v[0] > v[1]) ctx.issue(p, "expected low ≤ high");
};

/* ---------- evidence ---------- */

const provenance: Check = obj({
  kind: req((v, p, ctx) => {
    if (v === "measured") return ctx.issue(p, "measured values come from live device bindings and are never stored; use \"recorded\" with a date, \"sample\" or \"not-reported\"");
    oneOf(["recorded", "sample", "not-reported"])(v, p, ctx);
  }),
  at: opt(time), until: opt(time), n: opt(count), source: opt(str),
}, (v, p, ctx) => {
  if (v.kind === "recorded" && v.at === undefined) ctx.issue(join(p, "at"), "recorded evidence needs a date");
  if (typeof v.at === "string" && typeof v.until === "string" && Date.parse(v.until) < Date.parse(v.at)) ctx.issue(join(p, "until"), "must not be before at");
});

const values = arr(nullableNumber);
const uniform = obj({ stepS: req(num({ min: 0.001 })), values: req(values) });
const sampled = obj({ start: req(time), stepS: req(num({ min: 0.001 })), values: req(values) });
const percentiles = obj({ p50: req(nullableNonNegative), p95: req(nullableNonNegative), n: opt(count) });
const clockKind = oneOf(["device", "browser", "wall", "simulated", "server"]);
const clockLabel = obj({ zone: req(str), utcOffsetMinutes: req(num({ min: -840, max: 840, integer: true })) });
const severity = oneOf(["minor", "major", "critical"]);
const pathKind = oneOf(["edge", "cloud", "fallback"]);

/* ---------- configurations ---------- */

const robotSpec = obj({
  name: req(str), summary: req(str), details: opt(str), cameras: req(arr(str)), controlRateHz: req(nullable(nonNegative)),
  actionSpace: req(str), safetyController: opt(str),
  simTwin: req(nullable(obj({ name: req(str), engine: req(str), note: opt(str) }))),
  provenance: req(provenance),
});
const edgeHardware = obj({
  name: req(str), memoryGiB: req(num({ min: 0.001 })), memoryNote: opt(str), compute: opt(str), software: opt(str),
  powerModes: req(arr(obj({ id: req(id), label: req(str), capW: req(nullable(num({ min: 0.001 }))), note: opt(str), recommended: opt(bool) }), { min: 1 })),
  powerModeId: req(id),
  thermal: req(obj({ sensor: req(str), swThrottleC: req(nullableNumber), hwThrottleC: req(nullableNumber), shutdownC: req(nullableNumber) })),
  edgeBudgetGiB: opt(nullableNonNegative),
}, (v, p, ctx) => {
  if (Array.isArray(v.powerModes) && !v.powerModes.some(mode => isRecord(mode) && mode.id === v.powerModeId)) ctx.issue(join(p, "powerModeId"), "must match one of powerModes[].id");
});
const modelRole = oneOf(["planner", "policy", "fallback-policy", "skill-pack", "verifier"]);
const modelState = oneOf(["active", "fallback-only", "proposed", "blocked", "not-deployed"]);
const edgeModel = obj({
  id: req(id), role: req(modelRole), name: req(str), shortName: req(str), runtime: req(str), detail: opt(str),
  contextTokens: opt(nullable(count)), residentGiB: req(nullableNonNegative), state: req(modelState), evidence: opt(provenance),
});
const cloudModel = obj({
  id: req(id), role: req(modelRole), name: req(str), shortName: req(str), serving: req(nullableString), detail: opt(str),
  chunk: opt(nullable(obj({ actions: req(num({ min: 1, integer: true })), rateHz: req(num({ min: 0.001 })), validityMs: req(num({ min: 1 })) }))),
  state: req(modelState), evidence: opt(provenance),
});
const routing = obj({
  mode: req(oneOf(["hybrid", "cloud-only", "edge-only"])), summary: req(str), defaultRoute: req(str),
  fallbackTrigger: req(obj({ cloudRttP95Ms: req(nullableNonNegative), windowS: req(nullableNonNegative), packetLossPct: req(nullablePercent), missedDeadlines: req(nullable(count)) })),
  fallbackAction: req(obj({ validityMs: req(nullableNonNegative), blendInto: req(nullableString), speedFactor: req(nullableNonNegative), otherwise: req(oneOf(["safe-hold", "pause-and-escalate"])) })),
  escalation: req(obj({ stallS: req(nullableNonNegative), failedGrasps: req(nullable(count)), onVerifierAnomaly: req(oneOf(["escalate", "log"])), channel: req(str) })),
  thermalGuard: req(obj({ socTempC: req(nullableNumber), overCurrentEventsPer10Min: req(nullable(count)), action: req(str) })),
});
const safetyDefinition = obj({ id: req(id), name: req(str), rule: req(str), severity: req(severity), criticalWhen: opt(str) });
const flagRules = obj({
  socTempWarnC: req(nullableNumber), socTempAttentionC: req(nullableNumber), boardPowerWarnPctOfCap: req(nullablePercent),
  memoryAvailableWarnPct: req(nullablePercent), fallbackAttentionPct: req(nullablePercent), edgeP95BudgetMs: req(nullableNonNegative),
  cloudTimeoutBudgetPct: req(nullablePercent), reportAttentionS: req(nullableNonNegative),
});
const compatibility = obj({
  id: req(id), verdict: req(oneOf(["pass", "warn", "block", "pending"])), title: req(str), detail: req(str), evidence: req(provenance),
  action: opt(obj({ label: req(str), href: req(nullableString) })),
});
const revision = obj({
  rev: req(id), createdAt: req(time), note: opt(str), robot: req(robotSpec), edgeHardware: req(edgeHardware),
  edgeModels: req(arr(edgeModel)), cloudModels: req(arr(cloudModel)), routing: req(routing),
  safety: req(obj({ name: req(str), definitions: req(arr(safetyDefinition)), note: opt(str) })),
  flagRules: req(flagRules), compatibility: req(arr(compatibility)),
});
const latencySeries = obj({
  start: req(time), stepS: req(num({ min: 0.001 })), p50: req(values), p95: req(values), fallbackPct: opt(arr(nullablePercent)), clock: req(clockKind),
}, (v, p, ctx) => {
  const n = Array.isArray(v.p50) ? v.p50.length : -1;
  if (Array.isArray(v.p95) && v.p95.length !== n) ctx.issue(join(p, "p95"), "must have the same length as p50");
  if (Array.isArray(v.fallbackPct) && v.fallbackPct.length !== n) ctx.issue(join(p, "fallbackPct"), "must have the same length as p50");
});
const production = obj({
  provenance: req(provenance), window: req(obj({ from: req(time), to: req(time), label: req(str) })),
  robots: req(count), reporting: req(count), edgePlannerMs: req(percentiles), cloudChunkMs: req(nullable(percentiles)),
  fallbackPct: req(nullablePercent),
  interventionsPerRobotHour: req(nullable(obj({ mean: req(nullableNonNegative), min: req(nullableNonNegative), max: req(nullableNonNegative) }))),
  safetyEvents: req(nullable(obj({ critical: req(count), major: req(count), minor: req(count), note: opt(str) }))),
  latency: req(obj({ edge: req(nullable(latencySeries)), cloud: req(nullable(latencySeries)) })),
}, (v, p, ctx) => { if (typeof v.reporting === "number" && typeof v.robots === "number" && v.reporting > v.robots) ctx.issue(join(p, "reporting"), "must not exceed robots"); });
const configuration = obj({
  id: req((v, p, ctx) => { id(v, p, ctx); if (v === "new") ctx.issue(p, "\"new\" is reserved for the new-configuration route"); }),
  name: req(str), purpose: req(text), status: req(oneOf(["draft", "testing", "production"])), recommended: req(bool),
  productionRev: req(nullableId), candidateRev: req(nullableId), revisions: req(arr(revision, { min: 1 })),
  suiteId: req(nullableId), createdAt: req(time), updatedAt: req(time),
  highlight: opt(obj({ tone: req(oneOf(["good", "warning", "blocked"])), lead: req(str), text: req(str), provenance: opt(provenance) })),
  production: opt(production),
}, (v, p, ctx) => {
  const revs = Array.isArray(v.revisions) ? v.revisions.filter(isRecord).map(r => r.rev) : [];
  for (const key of ["productionRev", "candidateRev"] as const) {
    if (typeof v[key] === "string" && !revs.includes(v[key])) ctx.issue(join(p, key), `"${v[key]}" is not one of revisions[].rev`);
  }
  duplicates(revs, join(p, "revisions"), "rev", ctx);
  if (v.status === "production" && v.productionRev === null) ctx.issue(join(p, "productionRev"), "a configuration in production needs a production revision");
});

/* ---------- robots ---------- */

const telemetryReading = obj({
  at: req(nullableTime), cpuPct: req(nullablePercent), gpuPct: req(nullablePercent), memAvailableMiB: req(nullableNonNegative),
  memTotalMiB: req(nullableNonNegative), socTempC: req(nullableNumber), boardPowerW: req(nullableNonNegative),
  boardPowerPeakW: opt(nullableNonNegative), batteryPct: opt(nullablePercent), runtimeState: opt(nullableString),
});
const metrics = ["cpuPct", "gpuPct", "memAvailableMiB", "socTempC", "boardPowerW"] as const;
const flag = obj({
  id: req(id), rule: req(oneOf(["soc-temp", "board-power", "memory", "edge-p95", "cloud-timeouts", "fallback", "no-report", "safety-stop", "manual"])),
  severity: req(oneOf(["warning", "attention"])), label: req(str), detail: req(str), at: req(time), note: opt(str), by: opt(str), provenance: req(provenance),
});
const robot = obj({
  id: req(id), name: req(str), configId: req(nullableId), role: req(oneOf(["test", "production"])), site: req(str), rev: req(nullableId),
  deviceId: opt((v, p, ctx) => { if (v !== CONFIGURED_DEVICE) id(v, p, ctx); }),
  kind: req(oneOf(["robot", "bench", "simulator"])), description: opt(str),
  health: req(oneOf(["healthy", "degraded", "attention", "offline", "not-reported"])), healthReason: opt(nullable(text)),
  flags: req(arr(flag)),
  telemetry: opt(obj({
    provenance: req(provenance), latest: req(telemetryReading),
    recent: opt(record(metrics, sampled)), day: opt(record(metrics, sampled)), clock: req(clockKind),
  })),
  latency: opt(obj({
    provenance: req(provenance), window: req(str), edgePlannerMs: req(percentiles), ttftMs: opt(nullable(percentiles)),
    cloudChunkMs: req(nullable(percentiles)), fallbackPct: req(nullablePercent), cloudTimeoutPct: opt(nullablePercent),
  })),
  interventions: opt(obj({ perHour: req(nullableNonNegative), robotHours: opt(nullableNonNegative), provenance: req(provenance) })),
  clock: opt(clockLabel), lastSeenAt: opt(nullableTime), registeredAt: req(time), agentVersion: opt(nullableString),
}, (v, p, ctx) => {
  if (v.deviceId !== undefined && v.telemetry !== undefined) ctx.issue(join(p, "telemetry"), "a robot with a live device binding gets telemetry from the device and must not store it");
  if (v.configId === null && v.rev !== null) ctx.issue(join(p, "rev"), "an unattached robot has no revision");
  if (typeof v.configId === "string" && v.rev === null) ctx.issue(join(p, "rev"), "an attached robot needs the revision it runs");
});

/* ---------- evaluations ---------- */

const suite = obj({
  id: req(id), name: req(str), version: req(str), description: req(text), simEngine: req(str), timing: req(str),
  episodesPerRun: req(num({ min: 1, integer: true })), seedsPerCell: req(num({ min: 1, integer: true })),
  tasks: req(arr(obj({ id: req(id), name: req(str), referenceMedianS: opt(nullableNonNegative) }), { min: 1 })),
  sliceFamilies: req(arr(obj({ id: req(id), name: req(str), slices: req(arr(obj({ id: req(id), name: req(str), description: opt(str) }), { min: 1 })) }))),
  safety: req(arr(safetyDefinition)),
  gate: req(arr(obj({ id: req(id), label: req(str), target: req(str) }))),
});
const runStatus = oneOf(["queued", "running", "passed-gate", "below-gate", "completed", "did-not-qualify", "cancelled", "failed"]);
const run = obj({
  id: req(id), number: req(num({ min: 0, integer: true })), kind: req(oneOf(["suite", "hil-latency", "timing", "episode"])), title: req(str),
  suiteId: req(nullableId), configId: req(id), rev: req(id), variant: req(str), robotId: req(id), purpose: opt(str),
  status: req(runStatus), progress: opt(nullable(obj({ done: req(count), total: req(num({ min: 1, integer: true })) }, (v, p, ctx) => {
    if (typeof v.done === "number" && typeof v.total === "number" && v.done > v.total) ctx.issue(join(p, "done"), "must not exceed total");
  }))),
  startedAt: req(nullableTime), finishedAt: req(nullableTime), simEngine: opt(str),
  counts: req(obj({ episodes: req(count), successes: req(nullable(count)) }, (v, p, ctx) => {
    if (typeof v.successes === "number" && typeof v.episodes === "number" && v.successes > v.episodes) ctx.issue(join(p, "successes"), "must not exceed episodes");
  })),
  successCi95: opt(nullable(tuple2Share)),
  safety: req(nullable(obj({
    per100: req(nullableNonNegative), episodesWithViolations: req(count), critical: req(count), major: req(count), minor: req(count),
    byCheck: req(arr(obj({ checkId: req(id), count: req(count) }))),
  }))),
  episodeTime: req(nullable(obj({ medianS: req(nullableNonNegative), p90S: req(nullableNonNegative), clock: req(clockKind) }))),
  baselineRunId: opt(nullableId),
  perTask: req(arr(obj({ taskId: req(id), episodes: req(count), successes: req(count), medianS: req(nullableNonNegative), safetyPer100: req(nullableNonNegative) }))),
  slices: req(arr(obj({ sliceId: req(id), episodes: req(count), successes: req(count), ci95: opt(nullable(tuple2Share)), safetyPer100: req(nullableNonNegative), medianS: req(nullableNonNegative) }))),
  failureModes: req(arr(obj({ label: req(str), count: req(count) }))),
  gate: req(nullable(obj({ passed: req(bool), results: req(arr(obj({ criterionId: req(id), passed: req(bool), actual: req(str) }))) }))),
  hilLatency: opt(nullable(obj({ plannerMs: req(percentiles), ttftMs: opt(nullable(percentiles)), provenance: req(provenance) }))),
  provenance: req(provenance), recordedEvaluationId: opt(nullableId), recordedEpisodeId: opt(nullableId), note: opt(str),
}, (v, p, ctx) => {
  if ((v.status === "queued" || v.status === "running") && !isRecord(v.progress)) ctx.issue(join(p, "progress"), "a queued or running run needs progress { done, total }");
  if (v.kind === "suite" && v.suiteId === null) ctx.issue(join(p, "suiteId"), "a suite run needs a suite");
  const gated = v.status === "passed-gate" || v.status === "below-gate";
  if (gated && !isRecord(v.gate)) ctx.issue(join(p, "gate"), "a gate result needs gate results");
  if (gated && isRecord(v.gate) && v.gate.passed !== (v.status === "passed-gate")) ctx.issue(join(p, "status"), "must agree with gate.passed");
});
const rollout = obj({
  id: req(id), runId: req(id), taskId: req(id), sliceIds: req(arr(id)), seed: req(count),
  outcome: req(oneOf(["succeeded", "failed", "timeout", "safety-stop"])), failureMode: opt(nullableString),
  violations: req(arr(obj({ checkId: req(id), severity: req(severity), atS: req(nonNegative) }))),
  durationS: req(nonNegative), steps: req(count), rateHz: req(nullable(num({ min: 0.001 }))), reward: opt(nullableNumber), wallS: opt(nullableNonNegative),
  events: req(arr(obj({ atS: req(nonNegative), label: req(str), tone: req(oneOf(["info", "warning", "good"])), detail: opt(str) }))),
  stepDetail: opt(arr(obj({ step: req(count), atS: req(nonNegative), planner: req(nullableString), action: req(str), latencyMs: req(nullableNonNegative), path: opt(nullable(pathKind)), note: opt(str) }))),
  signals: opt(obj({ gripperAperture: opt(uniform), eeSpeed: opt(uniform), eeSpeedLimit: opt(nullableNonNegative) })),
  provenance: req(provenance), episodeId: opt(nullableId),
});

/* ---------- traces, logs, activity ---------- */

const trace = obj({
  id: req(id), robotId: req(id), at: req(time), clock: req(clockKind), instruction: req(str), reference: opt(nullableString),
  decision: req(obj({ kind: req(oneOf(["skill", "declined"])), skill: req(nullableString), params: opt(nullableString), reason: opt(nullableString) })),
  path: req(pathKind), route: req(str), plannerMs: req(nullableNonNegative), policyMs: req(nullable(percentiles)), policyNote: opt(nullableString),
  outcome: req(oneOf(["succeeded", "failed", "escalated", "clarified"])), outcomeNote: opt(nullableString), durationS: req(nullableNonNegative),
  escalated: req(bool),
  spans: req(arr(obj({ id: req(id), name: req(str), path: req(oneOf(["edge", "cloud", "fallback", "operator", "none"])), startMs: req(nonNegative), durationMs: req(nonNegative), parentId: opt(nullableId), group: opt(id) }))),
  spanGroups: opt(arr(obj({ id: req(id), title: req(str), unit: req(oneOf(["ms", "s"])) }))),
  facts: req(arr(obj({ title: req(str), facts: req(arr(obj({ label: req(str), value: req(str), detail: opt(str) }))) }))),
  provenance: req(provenance), configRev: opt(nullableId),
}, (v, p, ctx) => {
  const spans = Array.isArray(v.spans) ? v.spans.filter(isRecord) : [];
  duplicates(spans.map(span => span.id), join(p, "spans"), "id", ctx);
  const groups = Array.isArray(v.spanGroups) ? v.spanGroups.filter(isRecord).map(group => group.id) : [];
  spans.forEach((span, i) => { if (typeof span.group === "string" && !groups.includes(span.group)) ctx.issue(join(join(p, "spans"), i) + ".group", `"${span.group}" is not one of spanGroups[].id`); });
});
const logLine = obj({
  id: req(id), at: req(time), clock: req(clockKind), level: req(oneOf(["info", "warn", "error"])), source: req(str),
  robotId: req(nullableId), configId: req(nullableId), message: req(str), provenance: req(provenance),
});
const activity = obj({
  id: req(id), at: req(time),
  kind: req(oneOf(["flagged", "flag-cleared", "run-queued", "run-started", "passed-gate", "below-gate", "promoted", "configuration-created", "robot-added", "role-changed", "reconnected"])),
  subject: req(obj({ type: req(oneOf(["robot", "run", "configuration"])), id: req(id) })), configId: req(nullableId), message: req(str), provenance: req(provenance),
});

const workspace = obj({
  schemaVersion: req((v, p, ctx) => { if (v !== WORKSPACE_SCHEMA_VERSION) ctx.issue(p, `expected ${WORKSPACE_SCHEMA_VERSION}; this website reads schema version ${WORKSPACE_SCHEMA_VERSION} only`); }),
  meta: req(obj({ id: req(id), name: req(str), label: req(str), description: req(text), updatedAt: req(time), sample: opt(bool) })),
  configurations: req(arr(configuration)), robots: req(arr(robot)), suites: req(arr(suite)), runs: req(arr(run)),
  rollouts: req(arr(rollout)), traces: req(arr(trace)), logs: req(arr(logLine)), activity: req(arr(activity)),
});

/* ---------- references between collections ---------- */

function duplicates(ids: unknown[], path: string, key: string, ctx: Context) {
  const seen = new Set<unknown>();
  ids.forEach((value, i) => {
    if (seen.has(value)) ctx.issue(`${path}[${i}].${key}`, `duplicate ${key} "${String(value)}"`);
    seen.add(value);
  });
}
const list = (value: unknown, key: string) => (Array.isArray(value) ? value : []).filter(isRecord).map(item => ({ item, key: item[key] }));

function references(doc: Data, ctx: Context) {
  const configurations = list(doc.configurations, "id"), robots = list(doc.robots, "id"), suites = list(doc.suites, "id"), runs = list(doc.runs, "id");
  for (const [name, items] of [["configurations", configurations], ["robots", robots], ["suites", suites], ["runs", runs], ["rollouts", list(doc.rollouts, "id")], ["traces", list(doc.traces, "id")], ["logs", list(doc.logs, "id")], ["activity", list(doc.activity, "id")]] as const) {
    duplicates(items.map(entry => entry.key), name, "id", ctx);
  }
  duplicates(runs.map(entry => entry.item.number), "runs", "number", ctx);
  const configById = new Map(configurations.map(entry => [entry.key, entry.item]));
  const revsOf = (configId: unknown) => {
    const config = configById.get(configId);
    return config && Array.isArray(config.revisions) ? config.revisions.filter(isRecord).map(r => r.rev) : [];
  };
  const robotIds = new Set(robots.map(entry => entry.key)), runIds = new Set(runs.map(entry => entry.key));
  const suiteById = new Map(suites.map(entry => [entry.key, entry.item]));
  configurations.forEach(({ item }, i) => {
    if (typeof item.suiteId === "string" && !suiteById.has(item.suiteId)) ctx.issue(`configurations[${i}].suiteId`, `unknown suite "${item.suiteId}"`);
  });
  robots.forEach(({ item }, i) => {
    if (typeof item.configId !== "string") return;
    if (!configById.has(item.configId)) ctx.issue(`robots[${i}].configId`, `unknown configuration "${item.configId}"`);
    else if (typeof item.rev === "string" && !revsOf(item.configId).includes(item.rev)) ctx.issue(`robots[${i}].rev`, `"${item.rev}" is not a revision of "${item.configId}"`);
  });
  runs.forEach(({ item }, i) => {
    if (!configById.has(item.configId)) ctx.issue(`runs[${i}].configId`, `unknown configuration "${String(item.configId)}"`);
    else if (!revsOf(item.configId).includes(item.rev)) ctx.issue(`runs[${i}].rev`, `"${String(item.rev)}" is not a revision of "${String(item.configId)}"`);
    if (!robotIds.has(item.robotId)) ctx.issue(`runs[${i}].robotId`, `unknown robot "${String(item.robotId)}"`);
    if (typeof item.baselineRunId === "string" && !runIds.has(item.baselineRunId)) ctx.issue(`runs[${i}].baselineRunId`, `unknown run "${item.baselineRunId}"`);
    if (typeof item.suiteId !== "string") return;
    const found = suiteById.get(item.suiteId);
    if (!found) return ctx.issue(`runs[${i}].suiteId`, `unknown suite "${item.suiteId}"`);
    const tasks = new Set((Array.isArray(found.tasks) ? found.tasks : []).filter(isRecord).map(task => task.id));
    const slices = new Set((Array.isArray(found.sliceFamilies) ? found.sliceFamilies : []).filter(isRecord).flatMap(family => (Array.isArray(family.slices) ? family.slices : []).filter(isRecord).map(slice => slice.id)));
    const gate = new Set((Array.isArray(found.gate) ? found.gate : []).filter(isRecord).map(criterion => criterion.id));
    (Array.isArray(item.perTask) ? item.perTask : []).filter(isRecord).forEach((task, j) => { if (!tasks.has(task.taskId)) ctx.issue(`runs[${i}].perTask[${j}].taskId`, `unknown task "${String(task.taskId)}" in suite "${item.suiteId}"`); });
    (Array.isArray(item.slices) ? item.slices : []).filter(isRecord).forEach((slice, j) => { if (!slices.has(slice.sliceId)) ctx.issue(`runs[${i}].slices[${j}].sliceId`, `unknown slice "${String(slice.sliceId)}" in suite "${item.suiteId}"`); });
    const results = isRecord(item.gate) && Array.isArray(item.gate.results) ? item.gate.results.filter(isRecord) : [];
    results.forEach((result, j) => { if (!gate.has(result.criterionId)) ctx.issue(`runs[${i}].gate.results[${j}].criterionId`, `unknown gate criterion "${String(result.criterionId)}"`); });
  });
  list(doc.rollouts, "id").forEach(({ item }, i) => { if (!runIds.has(item.runId)) ctx.issue(`rollouts[${i}].runId`, `unknown run "${String(item.runId)}"`); });
  list(doc.traces, "id").forEach(({ item }, i) => { if (!robotIds.has(item.robotId)) ctx.issue(`traces[${i}].robotId`, `unknown robot "${String(item.robotId)}"`); });
  list(doc.logs, "id").forEach(({ item }, i) => {
    if (typeof item.robotId === "string" && !robotIds.has(item.robotId)) ctx.issue(`logs[${i}].robotId`, `unknown robot "${item.robotId}"`);
    if (typeof item.configId === "string" && !configById.has(item.configId)) ctx.issue(`logs[${i}].configId`, `unknown configuration "${item.configId}"`);
  });
  list(doc.activity, "id").forEach(({ item }, i) => {
    if (typeof item.configId === "string" && !configById.has(item.configId)) ctx.issue(`activity[${i}].configId`, `unknown configuration "${item.configId}"`);
  });
}

/** Validates an unknown value (a parsed document body) as a schema-1 `ConvoyWorkspace`. */
export function validateWorkspace(value: unknown): ValidationResult {
  const ctx = new Context();
  workspace(value, "", ctx);
  if (!ctx.issues.length && isRecord(value)) references(value, ctx);
  return ctx.issues.length
    ? { ok: false, issues: ctx.issues, warnings: ctx.warnings }
    : { ok: true, workspace: value as unknown as ConvoyWorkspace, warnings: ctx.warnings };
}

/** One-line summary for notices: `robots[2].rev: "r9" is not a revision of "hybrid" (and 3 more)`. */
export function summarizeIssues(issues: readonly ValidationIssue[]): string {
  if (!issues.length) return "";
  const [first] = issues;
  return `${first.path ? `${first.path}: ` : ""}${first.message}${issues.length > 1 ? ` (and ${issues.length - 1} more)` : ""}`;
}
