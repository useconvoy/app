import { readFile, writeFile } from "node:fs/promises";

const worldUrl = process.env.CONVOY_WORLD_URL ?? "http://world:8788";
const worldId = process.env.CONVOY_WORLD_ID;
const actionsPath =
  process.env.CONVOY_SCENARIO_ACTIONS ?? "/convoy/scenario/actions.json";
const outputPath = "/convoy/output/mission-result.json";

if (!worldId) throw new Error("CONVOY_WORLD_ID is required.");

async function request(pathname, options = {}) {
  const response = await fetch(`${worldUrl}${pathname}`, {
    ...options,
    headers: {
      "content-type": "application/json",
      ...options.headers,
    },
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error ?? `World request failed: ${response.status}`);
  }
  return payload;
}

const actions = JSON.parse(await readFile(actionsPath, "utf8"));
await request(`/v1/worlds/${worldId}/reset`, { method: "POST" });
console.log(`Reset ${worldId}; replaying ${actions.length} typed actions.`);

for (const [index, action] of actions.entries()) {
  await request(`/v1/worlds/${worldId}/actions`, {
    method: "POST",
    body: JSON.stringify(action),
  });
  console.log(`${index + 1}/${actions.length} ${action.actor} -> ${action.tool}`);
}

const evaluation = await request(`/v1/worlds/${worldId}/evaluate`, {
  method: "POST",
});
await writeFile(outputPath, `${JSON.stringify(evaluation, null, 2)}\n`, "utf8");
console.log(
  `World evaluation: ${evaluation.score}/100 (${evaluation.passed ? "passed" : "failed"}).`,
);
process.exitCode = evaluation.passed ? 0 : 1;
