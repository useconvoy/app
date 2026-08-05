#!/usr/bin/env node
/**
 * Record real control-plane SSE sequences into tests/fixtures/sse/*.json so
 * unit and component suites replay genuine event shapes instead of invented
 * ones. Requires the local runtime stack (scripts/local-stack.sh +
 * scripts/dev-runtime.sh). Scenarios cover the surfaces the console renders:
 * a plain landing run, a human gate, plan approval, pause/resume, and a
 * virtual-clock rehearsal with a scripted reply.
 */
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const ROOT = new URL("..", import.meta.url).pathname;
const OUT = join(ROOT, "tests/fixtures/sse");
const BASE = process.env.CONVOY_CONTROL_PLANE_URL ?? "http://localhost:8700";
const TOKEN = process.env.CONVOY_CONTROL_PLANE_TOKEN ?? "e2e-dev-token";
const TENANT = "tenant-fixtures";
const ACTOR = "fixtures@convoy.test";

const headers = {
  Authorization: `Bearer ${TOKEN}`,
  "X-Actor-Id": ACTOR,
  "X-Tenant-Id": TENANT,
  "Content-Type": "application/json",
};

async function api(path, body) {
  const response = await fetch(`${BASE}${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(`${path} -> ${response.status}: ${await response.text()}`);
  }
  return response.json();
}

/** Stream a run's events until a terminal type or `until` returns true. */
async function collect(runId, { until, timeoutMs = 90_000 } = {}) {
  const events = [];
  const terminal = new Set(["run_completed", "run_failed"]);
  const response = await fetch(`${BASE}/runs/${runId}/events?after=0`, { headers });
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let index;
    while ((index = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, index);
      buffer = buffer.slice(index + 2);
      const data = frame.split("\n").find((line) => line.startsWith("data: "));
      if (!data) continue;
      const event = JSON.parse(data.slice(6));
      events.push(event);
      if (terminal.has(event.type) || (until && until(event, events))) {
        reader.cancel().catch(() => {});
        return events;
      }
    }
  }
  reader.cancel().catch(() => {});
  return events;
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

mkdirSync(OUT, { recursive: true });
const save = (name, events) => {
  writeFileSync(join(OUT, `${name}.json`), JSON.stringify(events, null, 2) + "\n");
  console.log(`${name}: ${events.length} events (${events.at(-1)?.type})`);
};

// 1. Plain sandbox run that lands on its own.
{
  const { run_id } = await api("/runs", {
    goal: "Compare the access lists and note differences",
    environment_id: "stub-local",
    budget_usd: "75",
  });
  save("run-landed", await collect(run_id));
}

// 2. A human gate opens, gets answered, and the run lands.
{
  const { run_id } = await api("/runs", {
    goal: "Write exception memos and hold them for review",
    environment_id: "stub-local",
    budget_usd: "40",
    fixture_gates: {
      "step-2": { kind: "approval", prompt: "Look over the exception memos before they go out", on_timeout: "pause" },
    },
  });
  await collect(run_id, { until: (event) => event.type === "gate_opened" });
  await api(`/runs/${run_id}/steps/step-2/respond`, { response: "Approved, send them" });
  save("run-gated", await collect(run_id));
}

// 3. Plan approval: reject would end it, so approve the rendered version.
{
  const { run_id } = await api("/runs", {
    goal: "Refresh vendor documents on the production workspace",
    environment_id: "stub-local",
    budget_usd: "40",
    policy: { require_plan_approval: true },
  });
  const events = await collect(run_id, { until: (event) => event.type === "plan_created" });
  const version = events.findLast((event) => event.type === "plan_created")?.payload?.version ?? 1;
  await sleep(500);
  await api(`/runs/${run_id}/plan/approve`, { plan_version: version, approve: true });
  save("run-approved", await collect(run_id));
}

// 4. Pause mid-run, then resume to landing.
{
  const { run_id } = await api("/runs", {
    goal: "Chase outstanding attestations",
    environment_id: "stub-local",
    budget_usd: "30",
  });
  await collect(run_id, { until: (event) => event.type === "step_started" });
  await api(`/runs/${run_id}/pause`, {});
  await collect(run_id, { until: (event) => event.type === "paused" });
  await api(`/runs/${run_id}/resume`, {});
  save("run-paused-resumed", await collect(run_id));
}

// 5. Rehearsal on a virtual clock: a scripted reply is registered for a
// virtual instant, the clock advances past it, and the run lands. This is
// the canonical "recorded rehearsal" fixture the test plan names.
{
  const { run_id } = await api("/runs", {
    goal: "Rehearse the attestation chase across fast-forwarded days",
    environment_id: "stub-local-virtual",
    budget_usd: "30",
    fixture_gates: {
      "step-2": { kind: "input", prompt: "Has everyone confirmed?", on_timeout: "pause" },
    },
  });
  const opened = await collect(run_id, { until: (event) => event.type === "gate_opened" });
  const openedAt = opened.findLast((event) => event.type === "gate_opened");
  const virtualNow = new Date(openedAt.virtual_ts ?? openedAt.ts);
  const replyAt = new Date(virtualNow.getTime() + 2 * 24 * 3600 * 1000);
  const advanceTo = new Date(virtualNow.getTime() + 3 * 24 * 3600 * 1000);
  await api(`/runs/${run_id}/steps/step-2/respond`, {
    response: "Everyone confirmed after the reminder",
    at_virtual: replyAt.toISOString(),
  });
  await api(`/runs/${run_id}/clock/advance`, { to: advanceTo.toISOString() });
  save("run-rehearsal-virtual", await collect(run_id));
}

console.log("done");
