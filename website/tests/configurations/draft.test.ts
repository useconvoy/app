import test from "node:test";
import assert from "node:assert/strict";
import { WorkspaceStore } from "../../src/lib/configurations/client";
import {
  applyDraft, buildCatalog, checkSummary, compatibilityFor, createOutcome, describeAction, describeEscalation, describeThermal, describeTrigger, draftFromConfiguration,
  draftIssues, draftRevision, initialDraft, modelKey, namesModel, parseStep, residentMemory, setCloudModel, setPowerMode, setRouteMode, setSaveAs, startFrom, stepValue,
  toggleEdgeModel, updateRouting,
} from "../../src/lib/configurations/draft";
import type { ConfigurationDraft } from "../../src/lib/configurations/draft";
import { addRevision } from "../../src/lib/configurations/mutations";
import { createSampleWorkspace } from "../../src/lib/configurations/sample";
import type { CompatibilityCheck, ConvoyWorkspace } from "../../src/lib/configurations/types";
import { validateWorkspace } from "../../src/lib/configurations/validate";

const NOW = Date.parse("2026-10-01T09:41:20Z");
const LATER = NOW + 60_000;
const valid = (value: unknown) => { const result = validateWorkspace(value); assert.equal(result.ok, true, result.ok ? "" : JSON.stringify(result.issues)); };
const hybridOf = (ws: ConvoyWorkspace) => ws.configurations.find(config => config.id === "hybrid")!;
const byId = (checks: readonly CompatibilityCheck[], id: string) => checks.find(check => check.id === id);
const PLANNER = "planner:Qwen2.5-1.5B-Instruct Q4_K_M", FALLBACK = "fallback-policy:SmolVLA (450M)", EDGE_POLICY = "policy:SmolVLA (450M)", SKILLS = "skill-pack:Short-horizon skill pack × 2";
const POLICY_V31 = "policy:Station VLA policy v3.1", POLICY_V3 = "policy:Station VLA policy v3";
const noMeasured = (checks: readonly CompatibilityCheck[]) => assert.ok(checks.every(check => ["recorded", "sample", "not-reported"].includes(check.evidence.kind)), "stored checks never carry measured evidence");

function revisionDraft(ws: ConvoyWorkspace): ConfigurationDraft {
  return draftFromConfiguration(hybridOf(ws), "revision");
}

test("the catalog lists each robot, device and model in a role once, newest definition first", () => {
  const catalog = buildCatalog(createSampleWorkspace(NOW));
  assert.deepEqual(catalog.robots.map(item => item.key), ["Bimanual mobile manipulator"]);
  assert.deepEqual(catalog.twins.map(item => item.key), ["Bimanual sim twin · MuJoCo"]);
  assert.deepEqual(catalog.hardware.map(item => item.key), ["Jetson Orin Nano Super 8 GB"]);
  assert.deepEqual(catalog.edgeModels.map(item => item.key), [PLANNER, EDGE_POLICY, FALLBACK, SKILLS], "planner, policy, fallback, skill pack");
  assert.deepEqual(catalog.cloudModels.map(item => item.key), [POLICY_V31, POLICY_V3, "policy:Station VLA policy v2", "verifier:Station VLM verifier"]);
  assert.deepEqual([...new Set(catalog.routing.map(item => item.key))], ["edge-only", "hybrid", "cloud-only"]);
});

test("a draft starts from ?from as the next revision, otherwise as a copy of the recommended configuration", () => {
  const ws = createSampleWorkspace(NOW);
  const from = initialDraft(ws, "hybrid")!;
  assert.deepEqual([from.draft.saveAs, from.draft.baseId, from.draft.baseRev, from.draft.name, from.missing], ["revision", "hybrid", "r4", "Bimanual station · Hybrid", null]);
  const copy = initialDraft(ws, null)!;
  assert.deepEqual([copy.draft.saveAs, copy.draft.baseId, copy.draft.name, copy.missing], ["configuration", "hybrid", "", null]);
  assert.equal(initialDraft(ws, "not-there")!.missing, "not-there");
  assert.equal(initialDraft({ ...ws, configurations: [] }, null), null, "nothing to start from");
  assert.equal(parseStep("routing"), "routing");
  assert.equal(parseStep("bogus"), "robot");
});

test("an unchanged draft maps back to its revision and reuses the stored compatibility check", () => {
  const ws = createSampleWorkspace(NOW);
  const r4 = hybridOf(ws).revisions[1];
  const draft = revisionDraft(ws);
  const checks = compatibilityFor(ws, draft);
  assert.deepEqual(checks, r4.compatibility, "same combination, same evidence");
  assert.equal(checkSummary(checks), "3 pass · 1 warning · 1 blocked · 1 not measured");
  const at = new Date(LATER).toISOString();
  const mapped = draftRevision(draft, "r5", at, checks);
  assert.deepEqual([mapped.rev, mapped.createdAt, mapped.note], ["r5", at, undefined]);
  assert.deepEqual({ ...mapped, rev: r4.rev, createdAt: r4.createdAt, note: r4.note }, r4, "robot, hardware, models, routing, safety, flag rules and checks carry over");
  assert.equal(residentMemory(draft).total, 3.7);
});

test("changing the draft recomputes only the affected checks, from declared values and recorded entries", () => {
  const ws = createSampleWorkspace(NOW);
  const catalog = buildCatalog(ws);
  const draft = revisionDraft(ws);
  const r4 = hybridOf(ws).revisions[1];

  const lighter = toggleEdgeModel(catalog, draft, SKILLS, false);
  const lighterChecks = compatibilityFor(ws, lighter);
  assert.deepEqual(byId(lighterChecks, "memory"), {
    id: "memory", verdict: "pass", title: "Memory with every edge model resident",
    detail: "3.0 of 7.4 GiB: planner 1.2, fallback policy 1.8 GiB. Estimated from each model’s declared resident memory.",
    evidence: { kind: "sample", source: "Resident-memory estimates declared for each model" },
  });
  assert.deepEqual(byId(lighterChecks, "policy-rate"), byId(r4.compatibility, "policy-rate"), "the motor policies did not change");

  const lowPower = compatibilityFor(ws, setPowerMode(draft, "15w"));
  assert.deepEqual(byId(lowPower, "power"), { id: "power", verdict: "pending", title: "Board power within the 15 W mode", detail: "Board input has not been recorded on the Jetson Orin Nano Super 8 GB against the 15 W cap.", evidence: { kind: "not-reported" } });
  const uncapped = byId(compatibilityFor(ws, setPowerMode(draft, "maxn-super")), "power")!;
  assert.deepEqual([uncapped.verdict, uncapped.evidence.kind], ["warn", "not-reported"]);
  assert.match(uncapped.detail, /no power cap/);

  const heavy = { ...draft, edgeModels: [...draft.edgeModels, { ...draft.edgeModels[2], id: "edge-large", name: "Large skill pack", shortName: "Large pack", residentGiB: 5 }] };
  assert.equal(byId(compatibilityFor(ws, heavy), "memory")!.verdict, "block");
  assert.deepEqual(draftIssues(ws, heavy).map(issue => [issue.step, issue.field]), [["edge", "edgeModels"]]);

  // A faster robot breaks the stored premise: the fallback's recorded timing run decides.
  const faster = { ...draft, robot: { ...draft.robot, controlRateHz: 30 } };
  const timing = byId(compatibilityFor(ws, faster), "policy-rate")!;
  assert.equal(timing.verdict, "warn");
  assert.deepEqual(timing.evidence, ws.runs.find(run => run.id === "run-20")!.provenance);
  assert.match(timing.detail, /^SmolVLA \(450M\): Run 20 \(Jetson timing · wall-clock\) did not qualify\. .* As the fallback it only covers link loss, at 0\.6× speed\.$/);
  for (const checks of [lighterChecks, lowPower, compatibilityFor(ws, heavy), compatibilityFor(ws, faster)]) noMeasured(checks);
});

test("route changes take routing from the workspace's configurations with that route and reuse their evidence", () => {
  const ws = createSampleWorkspace(NOW);
  const catalog = buildCatalog(ws);
  const edited = updateRouting(revisionDraft(ws), "escalation", { stallS: 20 });

  const cloud = setRouteMode(catalog, toggleEdgeModel(catalog, toggleEdgeModel(catalog, edited, FALLBACK, false), SKILLS, false), "cloud-only");
  assert.equal(cloud.routing.summary, "Cloud acts, no edge fallback");
  assert.deepEqual(cloud.routing.fallbackTrigger, { cloudRttP95Ms: null, windowS: null, packetLossPct: null, missedDeadlines: null });
  assert.deepEqual(cloud.routing.fallbackAction, { validityMs: 800, blendInto: null, speedFactor: null, otherwise: "pause-and-escalate" });
  assert.equal(cloud.routing.escalation.stallS, 20, "escalation stays as edited");
  // v3.1 and the verifier were never run cloud only: computed from the declared chunk contract.
  const computed = compatibilityFor(ws, cloud);
  assert.deepEqual(byId(computed, "policy-rate"), { id: "policy-rate", verdict: "pass", title: "Action policy at the control rate", detail: "The cloud policy streams 25-action chunks at 25 Hz for the robot’s 25 Hz control rate; nothing on the Jetson Orin Nano Super 8 GB runs at the control rate.", evidence: { kind: "sample" } });
  assert.equal(byId(computed, "cloud-rtt")!.title, "Cloud round trip within the 800 ms action validity");
  const cloudOnly = ws.configurations.find(config => config.id === "cloud-only")!.revisions[1];
  assert.deepEqual(byId(computed, "memory"), byId(cloudOnly.compatibility, "memory"), "planner only, as stored for cloud only");
  // With the cloud-only revision's own models, its stored checks apply.
  const matching = setCloudModel(catalog, setCloudModel(catalog, cloud, "policy", POLICY_V3), "verifier", null);
  assert.deepEqual(byId(compatibilityFor(ws, matching), "policy-rate"), byId(cloudOnly.compatibility, "policy-rate"));
  assert.deepEqual(byId(compatibilityFor(ws, matching), "cloud-rtt"), byId(cloudOnly.compatibility, "cloud-rtt"));
  assert.deepEqual(draftIssues(ws, matching), []);

  const edge = setCloudModel(catalog, setCloudModel(catalog, setRouteMode(catalog, toggleEdgeModel(catalog, cloud, EDGE_POLICY, true), "edge-only"), "policy", null), "verifier", null);
  const edgeChecks = compatibilityFor(ws, edge);
  assert.deepEqual(edgeChecks.map(check => check.id), ["planner-fit", "planner-4b", "policy-rate", "power", "memory"], "no cloud round trip on the edge");
  const edgeOnly = ws.configurations.find(config => config.id === "edge-only")!.revisions[0];
  assert.deepEqual(byId(edgeChecks, "policy-rate"), byId(edgeOnly.compatibility, "policy-rate"));
  assert.equal(edge.routing.summary, "Everything on the edge");

  // Without a configuration on that route, the routing starts empty and must be filled in.
  const noEdge = { ...ws, configurations: ws.configurations.filter(config => config.id !== "edge-only") };
  const generic = setRouteMode(buildCatalog(noEdge), revisionDraft(noEdge), "edge-only");
  assert.equal(generic.routing.defaultRoute, "The edge planner and the on-device policy run every skill on the edge device.");
  assert.equal(generic.routing.fallbackAction.validityMs, null);
});

test("evidence recorded on another device is never reused; recorded board power is compared with the cap", () => {
  const ws = createSampleWorkspace(NOW);
  const draft = revisionDraft(ws);
  const elsewhere = { ...draft, edgeHardware: { ...draft.edgeHardware, name: "Other edge board" } };
  const checks = compatibilityFor(ws, elsewhere);
  assert.deepEqual(checks.map(check => [check.id, check.verdict]), [["planner-fit", "pending"], ["policy-rate", "pending"], ["cloud-rtt", "pending"], ["power", "pending"], ["memory", "pass"]]);
  assert.ok(checks.filter(check => check.id !== "memory").every(check => check.evidence.kind === "not-reported"));

  const recorded = createSampleWorkspace(NOW);
  const robot = recorded.robots.find(item => item.id === "unit-13")!;
  robot.telemetry = { ...robot.telemetry!, provenance: { kind: "recorded", at: "2026-09-20T08:00:00Z", source: "Bench log" }, latest: { ...robot.telemetry!.latest, boardPowerW: 11, boardPowerPeakW: 12 }, recent: {}, day: {} };
  const power = byId(compatibilityFor(recorded, setPowerMode(revisionDraft(recorded), "15w")), "power")!;
  assert.deepEqual(power, { id: "power", verdict: "pass", title: "Board power within the 15 W mode", detail: "Recorded board input peaked at 12.0 W on Unit 13, 80 % of the 15 W cap.", evidence: robot.telemetry.provenance });
  noMeasured(checks);
});

test("issues block an incomplete draft, by step, with the step that fixes them", () => {
  const ws = createSampleWorkspace(NOW);
  const catalog = buildCatalog(ws);
  const copy = initialDraft(ws, null)!.draft;
  assert.deepEqual(draftIssues(ws, copy), [{ step: "robot", field: "name", message: "Name the configuration." }]);
  assert.match(draftIssues(ws, { ...copy, name: " bimanual station · cloud ONLY " })[0].message, /already called/);
  assert.deepEqual(draftIssues(ws, revisionDraft(ws)), [], "a revision keeps its configuration's name");
  assert.throws(() => applyDraft(ws, copy, LATER), /not complete: Name the configuration/);

  const noFallback = toggleEdgeModel(catalog, { ...copy, name: "Lab hybrid" }, FALLBACK, false);
  assert.deepEqual(draftIssues(ws, noFallback).map(issue => [issue.step, issue.field, issue.fix]), [["routing", "mode", "edge"]]);
  const otherTarget = updateRouting({ ...copy, name: "Lab hybrid" }, "fallbackAction", { blendInto: "Skill pack" });
  assert.deepEqual(draftIssues(ws, otherTarget).map(issue => [issue.field, issue.fix]), [["blendInto", "edge"]]);
  const edgeWithCloud = setRouteMode(catalog, { ...copy, name: "Lab edge" }, "edge-only");
  assert.deepEqual(draftIssues(ws, edgeWithCloud).map(issue => issue.fix), ["edge", "cloud"]);

  const numbers = updateRouting(updateRouting(updateRouting({ ...copy, name: "Lab numbers" }, "fallbackTrigger", { packetLossPct: 120, missedDeadlines: 1.5 }), "fallbackAction", { speedFactor: 1.5, validityMs: null }), "escalation", { channel: " " });
  assert.deepEqual(draftIssues(ws, numbers).map(issue => issue.field), ["packetLossPct", "missedDeadlines", "speedFactor", "validityMs", "channel"]);
  const silent = updateRouting({ ...copy, name: "Lab silent" }, "fallbackTrigger", { cloudRttP95Ms: null, packetLossPct: null, missedDeadlines: null });
  assert.deepEqual(draftIssues(ws, silent).map(issue => issue.message), ["Set at least one condition that hands control to the edge."]);
  assert.ok(namesModel("SmolVLA on the Jetson", { name: "SmolVLA (450M)", shortName: "SmolVLA fallback" }));
  assert.ok(!namesModel("Skill pack", { name: "SmolVLA (450M)", shortName: "SmolVLA fallback" }));
});

test("saving a revision adds it under test; production and every robot keep their revision", () => {
  const ws = createSampleWorkspace(NOW);
  const before = JSON.stringify(ws);
  const draft = { ...revisionDraft(ws), note: "Lighter skill pack", purpose: "Plans on the edge and acts from a cloud policy." };
  const { workspace, configId, rev } = applyDraft(ws, draft, LATER);
  valid(workspace);
  assert.equal(JSON.stringify(ws), before, "inputs are not mutated");
  assert.deepEqual([configId, rev], ["hybrid", "r5"]);
  const hybrid = hybridOf(workspace);
  assert.deepEqual([hybrid.revisions.map(item => item.rev), hybrid.candidateRev, hybrid.productionRev, hybrid.status], [["r3", "r4", "r5"], "r5", "r3", "production"]);
  assert.equal(hybrid.revisions[2].note, "Lighter skill pack");
  assert.equal(hybrid.purpose, "Plans on the edge and acts from a cloud policy.");
  assert.deepEqual(hybrid.revisions[2].compatibility, hybridOf(ws).revisions[1].compatibility);
  assert.deepEqual(workspace.robots.map(robot => robot.rev), ws.robots.map(robot => robot.rev));
  assert.deepEqual([workspace.activity[0].kind, workspace.activity[0].message, workspace.activity[0].provenance.kind], ["configuration-created", "Bimanual station · Hybrid r5 created from r4", "recorded"]);
  assert.equal(workspace.meta.updatedAt, new Date(LATER).toISOString());
  assert.throws(() => addRevision(workspace, "hybrid", { revision: hybrid.revisions[2] }, LATER), /already has a revision r5/);
  assert.deepEqual(createOutcome(ws, draft), [
    "r5 becomes the revision under test; production stays on r3 until you promote r5.",
    "Robots stay on r3 and r4 until you move them to r5.",
    "Evaluation: queue Bimanual station suite v1 (360 episodes per run) on a test robot; nothing runs on its own.",
    "Production needs a passing gate: overall success ≥ 70 %, each slice family ≥ 55 %, critical safety events: none, and 3 more criteria.",
  ]);
});

test("saving a copy creates a draft configuration with revision r1; edge only stores no cloud models", () => {
  const ws = createSampleWorkspace(NOW);
  const catalog = buildCatalog(ws);
  const copy = toggleEdgeModel(catalog, { ...initialDraft(ws, null)!.draft, name: "Bimanual station · Lighter edge" }, SKILLS, false);
  const { workspace, configId, rev } = applyDraft(ws, copy, LATER);
  valid(workspace);
  assert.deepEqual([configId, rev], ["bimanual-station-lighter-edge", "r1"]);
  const created = workspace.configurations.find(config => config.id === configId)!;
  assert.deepEqual([created.status, created.candidateRev, created.productionRev, created.suiteId, created.revisions.length], ["draft", "r1", null, "station-suite-v1", 1]);
  assert.deepEqual(created.revisions[0].edgeModels.map(modelKey), [PLANNER, FALLBACK]);
  assert.equal(created.revisions[0].edgeHardware.edgeBudgetGiB, 3);
  assert.equal(workspace.activity[0].message, "Bimanual station · Lighter edge r1 created as a draft");
  assert.equal(hybridOf(workspace).revisions.length, 2, "the template is untouched");

  const edge = setCloudModel(catalog, setCloudModel(catalog, setRouteMode(catalog, toggleEdgeModel(catalog, toggleEdgeModel(catalog, { ...copy, name: "Lab edge" }, FALLBACK, false), EDGE_POLICY, true), "edge-only"), "policy", null), "verifier", null);
  const saved = applyDraft(ws, edge, LATER).workspace;
  valid(saved);
  assert.deepEqual(saved.configurations.find(config => config.name === "Lab edge")!.revisions[0].cloudModels, []);

  const back = setSaveAs(ws, setSaveAs(ws, revisionDraft(ws), "configuration"), "revision");
  assert.equal(setSaveAs(ws, revisionDraft(ws), "configuration").name, "", "a copy needs its own name");
  assert.equal(back.name, "Bimanual station · Hybrid");
  const moved = startFrom(ws, { ...copy, purpose: "Edited purpose" }, "cloud-only");
  assert.deepEqual([moved.baseId, moved.name, moved.purpose, moved.edgeModels.map(modelKey)], ["cloud-only", "Bimanual station · Lighter edge", "Edited purpose", [PLANNER]]);
});

test("the store saves an applied draft as the first document when none exists", async () => {
  const puts: unknown[] = [];
  const fetch = (async (_input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method !== "PUT") return new Response(JSON.stringify({ error: "missing" }), { status: 404 });
    const body = JSON.parse(String(init.body)) as { schema_version: number; body: unknown };
    puts.push(body);
    return new Response(JSON.stringify({ name: "configurations", schema_version: 1, body: body.body, size_bytes: 1, updated_at: "2026-10-01T09:42:20Z" }), { status: 200 });
  }) as typeof globalThis.fetch;
  const store = new WorkspaceStore({ fetch, now: () => NOW, createKey: () => "key-1" });
  await store.load();
  const ready = store.getSnapshot();
  assert.deepEqual([ready.source, ready.reason, ready.canSave], ["sample", "missing", true]);
  const created: { id: string | null } = { id: null };
  const result = await store.save(current => { const out = applyDraft(current, revisionDraft(current), LATER); created.id = out.configId; return out.workspace; });
  assert.equal(result.ok, true);
  assert.equal(created.id, "hybrid");
  assert.equal(puts.length, 1);
  assert.equal(store.getSnapshot().source, "document");
  assert.equal(hybridOf(store.getSnapshot().workspace!).candidateRev, "r5");
});

test("step values and routing read as short sentences", () => {
  const ws = createSampleWorkspace(NOW);
  const draft = revisionDraft(ws);
  const checks = compatibilityFor(ws, draft);
  assert.deepEqual((["robot", "hardware", "edge", "cloud", "routing", "review"] as const).map(step => stepValue(draft, step, checks)), [
    "Bimanual mobile manipulator · Bimanual sim twin", "Jetson Orin Nano Super 8 GB · 25 W", "Qwen2.5-1.5B planner · SmolVLA fallback · Skill pack (proposed)",
    "Cloud VLA policy v3.1 · Cloud VLM verifier", "Fallback at RTT p95 > 350 ms · S1–S6", "3 pass · 1 warning · 1 blocked · 1 not measured",
  ]);
  assert.equal(describeTrigger(draft.routing), "RTT p95 > 350 ms over 3 s · loss > 5 % · 3 missed deadlines");
  assert.equal(describeAction(draft.routing), "SmolVLA on the Jetson at 0.6× · else safe hold · actions valid 800 ms");
  assert.equal(describeEscalation(draft.routing), "Stall > 10 s · 2 failed grasps · verifier anomaly: escalate · Operator console");
  assert.equal(describeThermal(draft.routing), "SoC ≥ 90 °C or ≥ 4 over-current events / 10 min · Unload the fallback policy and stay on the cloud path");
});
