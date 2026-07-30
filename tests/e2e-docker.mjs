import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { runLocal } from "../runtime/local/executor.mjs";
import { validateRunEvent } from "../contracts/validate.mjs";

for (const specPath of [
  "examples/runs/renewal-recovery.json",
  "examples/runs/vendor-review.json",
  "examples/runs/vendor-assurance-portfolio.json",
]) {
  const run = await runLocal(specPath);
  assert.equal(run.docker_exit_code, 0);
  assert.equal(run.result.status, "run.succeeded");
  assert.equal(run.result.mission.score, 100);
  const events = (
    await readFile(path.join(run.run_directory, "output", "events.jsonl"), "utf8")
  )
    .trim()
    .split(/\r?\n/)
    .map((line) => JSON.parse(line));
  assert.deepEqual(
    events.map((event) => event.sequence),
    events.map((_, index) => index + 1),
  );
  for (const event of events) {
    assert.deepEqual(validateRunEvent(event), []);
  }
  console.log(`${specPath}: ${run.result.mission.score}/100`);
}
