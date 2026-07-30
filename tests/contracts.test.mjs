import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import {
  validateRunEvent,
  validateRunSpec,
  validateWorldSpec,
} from "../contracts/validate.mjs";

async function json(relativePath) {
  return JSON.parse(
    await readFile(new URL(`../${relativePath}`, import.meta.url), "utf8"),
  );
}

test("example RunSpecs satisfy the executable contract", async () => {
  for (const filename of [
    "examples/runs/renewal-recovery.json",
    "examples/runs/vendor-review.json",
    "examples/runs/vendor-assurance-portfolio.json",
  ]) {
    assert.deepEqual(validateRunSpec(await json(filename)), []);
  }
});

test("World manifests satisfy the executable contract", async () => {
  for (const worldId of [
    "aurelia-renewal-ops",
    "aurelia-vendor-review",
    "aurelia-vendor-assurance",
  ]) {
    assert.deepEqual(
      validateWorldSpec(
        await json(`worlds/catalog/${worldId}/world.json`),
      ),
      [],
    );
  }
});

test("RunEvent validation rejects ambiguous telemetry", () => {
  const valid = {
    apiVersion: "convoy.ai/v1alpha1",
    run_id: "run-123",
    sequence: 1,
    timestamp: "2026-07-29T00:00:00.000Z",
    type: "run.started",
    source: "supervisor",
    data: {},
  };
  assert.deepEqual(validateRunEvent(valid), []);
  assert.ok(
    validateRunEvent({ ...valid, sequence: 0, type: "surprise" }).length >= 2,
  );
});

test("RunSpec rejects raw credential-shaped secret references", async () => {
  const spec = await json("examples/runs/renewal-recovery.json");
  spec.secrets = [
    {
      name: "MODEL_API_KEY",
      source: "local-env",
      reference: "sk-raw-secret-material",
    },
  ];
  assert.match(validateRunSpec(spec).join("\n"), /must not contain secret values/);
});
