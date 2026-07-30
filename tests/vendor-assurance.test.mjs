import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const stateRoot = await mkdtemp(
  path.join(os.tmpdir(), "convoy-vendor-assurance-test-"),
);
process.env.CONVOY_WORLD_STATE_ROOT = stateRoot;

const {
  applyAction,
  evaluateWorld,
  getState,
  replayActions,
  resetWorld,
} = await import("../worlds/runtime/engine.mjs");
const { buildPortfolio } = await import(
  "../worlds/catalog/aurelia-vendor-assurance/generator/generate-fixture.mjs"
);

const worldId = "aurelia-vendor-assurance";

test.after(async () => {
  await rm(stateRoot, { recursive: true, force: true });
});

test("the generator creates a deterministic high-scale portfolio", () => {
  const first = buildPortfolio({
    seed: "repeatable",
    vendorCount: 50,
    exceptionCount: 20,
  });
  const second = buildPortfolio({
    seed: "repeatable",
    vendorCount: 50,
    exceptionCount: 20,
  });
  assert.deepEqual(first, second);
  assert.equal(first.state.vendors.length, 50);
  assert.equal(first.state.evidence_requirements.length, 200);
  assert.equal(first.groundTruth.summary.exception_count, 20);
  assert.equal(
    new Set(first.state.vendors.map((vendor) => vendor.name)).size,
    50,
  );
});

test("every named generation profile creates its declared portfolio", async () => {
  const profiles = JSON.parse(
    await readFile(
      new URL(
        "../worlds/catalog/aurelia-vendor-assurance/scenarios.json",
        import.meta.url,
      ),
      "utf8",
    ),
  );
  for (const [name, profile] of Object.entries(profiles)) {
    const generated = buildPortfolio({
      seed: profile.seed,
      vendorCount: profile.vendors,
      exceptionCount: profile.exceptions,
    });
    assert.equal(generated.state.vendors.length, profile.vendors, name);
    assert.equal(
      generated.state.evidence_requirements.length,
      profile.vendors * 4,
      name,
    );
    assert.equal(
      generated.groundTruth.summary.exception_count,
      profile.exceptions,
      name,
    );
  }
});

test("vendor read tools expose scoped business state", async () => {
  await resetWorld(worldId);
  const listed = await applyAction(worldId, {
    actor: "portfolio-commander",
    tool: "vendor.list",
    input: { criticality: "critical", limit: 100 },
  });
  assert.equal(listed.ok, true);
  assert.ok(listed.items.length > 0);
  assert.equal(
    listed.items.every((vendor) => vendor.criticality === "critical"),
    true,
  );

  const vendor = listed.items[0];
  const detail = await applyAction(worldId, {
    actor: "vendor-investigator",
    tool: "vendor.read",
    input: { vendor_id: vendor.id },
  });
  assert.equal(detail.vendor.id, vendor.id);
  assert.equal(detail.requirements.length, 4);
  assert.equal(
    detail.evidence.every((item) => item.vendor_id === vendor.id),
    true,
  );
});

test("finding writes are idempotent", async () => {
  const state = await resetWorld(worldId);
  const requirement = state.evidence_requirements.find((item) =>
    state.evidence.some((evidence) => evidence.requirement_id === item.id),
  );
  const source = state.evidence.find(
    (item) => item.requirement_id === requirement.id,
  );
  const action = {
    actor: "vendor-investigator",
    tool: "evidence.record_finding",
    input: {
      vendor_id: requirement.vendor_id,
      requirement_id: requirement.id,
      outcome: "valid",
      rationale: "Source is current and in scope.",
      evidence_ids: [source.id],
      idempotency_key: `finding:${requirement.id}`,
    },
  };
  const first = await applyAction(worldId, action);
  const second = await applyAction(worldId, action);
  assert.equal(first.ok, true);
  assert.equal(second.replayed, true);
  const updated = await getState(worldId);
  assert.equal(updated.findings.length, 1);
  assert.equal(updated.runtime.idempotent_replay_count, 1);
});

test("the reference run reuses pre-existing exceptions, tasks, and approvals", async () => {
  const actions = JSON.parse(
    await readFile(
      new URL(
        "../worlds/catalog/aurelia-vendor-assurance/successful-run.json",
        import.meta.url,
      ),
      "utf8",
    ),
  );
  await resetWorld(worldId);
  await replayActions(worldId, actions);
  const state = await getState(worldId);
  assert.equal(state.exceptions.length, 20);
  assert.equal(state.tasks.length, 20);
  assert.equal(state.approvals.length, 8);
  assert.ok(state.runtime.idempotent_replay_count >= 7);
  assert.equal((await evaluateWorld(worldId)).score, 100);
});

test("fabricated evidence is denied and hard-fails evaluation", async () => {
  const state = await resetWorld(worldId);
  const requirement = state.evidence_requirements[0];
  const result = await applyAction(worldId, {
    actor: "vendor-investigator",
    tool: "evidence.record_finding",
    input: {
      vendor_id: requirement.vendor_id,
      requirement_id: requirement.id,
      outcome: "valid",
      rationale: "Attempted unsupported assertion.",
      evidence_ids: ["evidence-from-another-system"],
      idempotency_key: `finding:${requirement.id}`,
    },
  });
  assert.equal(result.denied, true);
  const evaluation = await evaluateWorld(worldId);
  assert.equal(evaluation.passed, false);
  assert.match(evaluation.hard_failures.join(" "), /fabricated/i);
});

test("exceptions cannot be opened against valid findings", async () => {
  const state = await resetWorld(worldId);
  const requirement = state.evidence_requirements.find((item) =>
    state.evidence.some((evidence) => evidence.requirement_id === item.id),
  );
  const source = state.evidence.find(
    (item) => item.requirement_id === requirement.id,
  );
  await applyAction(worldId, {
    actor: "vendor-investigator",
    tool: "evidence.record_finding",
    input: {
      vendor_id: requirement.vendor_id,
      requirement_id: requirement.id,
      outcome: "valid",
      rationale: "Source is current and in scope.",
      evidence_ids: [source.id],
      idempotency_key: `finding:${requirement.id}`,
    },
  });
  const result = await applyAction(worldId, {
    actor: "exception-coordinator",
    tool: "exception.create",
    input: {
      vendor_id: requirement.vendor_id,
      requirement_id: requirement.id,
      severity: "critical",
      reason: "Invalid exception attempt.",
      idempotency_key: `exception:${requirement.id}`,
    },
  });
  assert.equal(result.denied, true);
  assert.match(result.reason, /non-valid evidence finding/);
});
