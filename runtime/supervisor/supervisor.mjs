import { appendFile, mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { spawn } from "node:child_process";
import { assertValid, validateRunSpec } from "../../contracts/validate.mjs";

const specPath = process.env.CONVOY_RUN_SPEC ?? "/convoy/run.json";
const outputDirectory = process.env.CONVOY_OUTPUT_DIR ?? "/convoy/output";
const eventsPath = path.join(outputDirectory, "events.jsonl");
const resultPath = path.join(outputDirectory, "result.json");
const missionResultPath = path.join(outputDirectory, "mission-result.json");
const spec = JSON.parse(await readFile(specPath, "utf8"));
assertValid(validateRunSpec(spec), "RunSpec");
await mkdir(outputDirectory, { recursive: true });

let sequence = Number(process.env.CONVOY_EVENT_SEQUENCE_START ?? 0);
let finalState = "run.failed";
let timedOut = false;
let terminated = false;
let child;
let eventWrites = Promise.resolve();

function emit(type, source, data = {}) {
  eventWrites = eventWrites.then(async () => {
    sequence += 1;
    const event = {
      apiVersion: "convoy.ai/v1alpha1",
      run_id: spec.metadata.id,
      sequence,
      timestamp: new Date().toISOString(),
      type,
      source,
      data,
    };
    await appendFile(eventsPath, `${JSON.stringify(event)}\n`, "utf8");
    process.stdout.write(`${JSON.stringify(event)}\n`);
  });
  return eventWrites;
}

async function streamLines(stream, type) {
  let pending = "";
  stream.setEncoding("utf8");
  for await (const chunk of stream) {
    pending += chunk;
    const lines = pending.split(/\r?\n/);
    pending = lines.pop() ?? "";
    for (const line of lines) {
      if (line) await emit(type, "agent", { line });
    }
  }
  if (pending) await emit(type, "agent", { line: pending });
}

function stop(signal) {
  terminated = true;
  child?.kill(signal);
}

process.on("SIGTERM", () => stop("SIGTERM"));
process.on("SIGINT", () => stop("SIGINT"));

await emit("run.started", "supervisor", {
  image: spec.runtime.image,
  command: spec.runtime.command,
  world_id: spec.world.id,
});

const heartbeat = setInterval(() => {
  void emit("run.heartbeat", "supervisor", {
    state: "running",
  });
}, Number(process.env.CONVOY_HEARTBEAT_MS ?? 5000));

const timeout = setTimeout(() => {
  timedOut = true;
  child?.kill("SIGTERM");
  setTimeout(() => child?.kill("SIGKILL"), 5000).unref();
}, spec.runtime.timeout_seconds * 1000);

try {
  child = spawn(spec.runtime.command[0], spec.runtime.command.slice(1), {
    cwd: "/workspace",
    env: {
      ...process.env,
      ...spec.runtime.environment,
      CONVOY_RUN_ID: spec.metadata.id,
      CONVOY_WORLD_ID: spec.world.id,
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  const streams = [
    streamLines(child.stdout, "agent.stdout"),
    streamLines(child.stderr, "agent.stderr"),
  ];
  const exit = await new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("exit", (code, signal) => resolve({ code, signal }));
  });
  await Promise.all(streams);

  let mission = null;
  try {
    mission = JSON.parse(await readFile(missionResultPath, "utf8"));
  } catch {
    // A missing mission result is handled as a failed run below.
  }

  const succeeded =
    exit.code === 0 && mission?.passed === true && !timedOut && !terminated;
  finalState = timedOut
    ? "run.timed_out"
    : terminated
      ? "run.cancelled"
      : succeeded
        ? "run.succeeded"
        : "run.failed";

  await emit(finalState, "supervisor", {
    exit_code: exit.code,
    signal: exit.signal,
    mission_score: mission?.score ?? null,
    mission_passed: mission?.passed ?? false,
  });
  await writeFile(
    resultPath,
    `${JSON.stringify(
      {
        run_id: spec.metadata.id,
        status: finalState,
        exit_code: exit.code,
        signal: exit.signal,
        mission,
        completed_at: new Date().toISOString(),
      },
      null,
      2,
    )}\n`,
    "utf8",
  );
  process.exitCode = succeeded ? 0 : 1;
} catch (error) {
  await emit("run.failed", "supervisor", { error: error.message });
  await writeFile(
    resultPath,
    `${JSON.stringify(
      {
        run_id: spec.metadata.id,
        status: "run.failed",
        error: error.message,
        completed_at: new Date().toISOString(),
      },
      null,
      2,
    )}\n`,
    "utf8",
  );
  process.exitCode = 1;
} finally {
  clearInterval(heartbeat);
  clearTimeout(timeout);
}
