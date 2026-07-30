import { spawn } from "node:child_process";
import {
  appendFile,
  chmod,
  cp,
  mkdir,
  readFile,
  writeFile,
} from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { assertValid, validateRunSpec } from "../../contracts/validate.mjs";

const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
);

function safeName(value) {
  return value.toLowerCase().replace(/[^a-z0-9-]/g, "-").slice(0, 48);
}

function execute(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: root,
      stdio: options.capture ? ["ignore", "pipe", "pipe"] : "inherit",
      env: process.env,
    });
    let stdout = "";
    let stderr = "";
    if (options.capture) {
      child.stdout.on("data", (chunk) => {
        stdout += chunk;
      });
      child.stderr.on("data", (chunk) => {
        stderr += chunk;
      });
    }
    child.once("error", reject);
    child.once("exit", (code, signal) => {
      if (code === 0 || options.allowFailure) {
        resolve({ code, signal, stdout, stderr });
      } else {
        reject(
          new Error(
            `${command} ${args[0] ?? ""} exited ${code}${stderr ? `: ${stderr.trim()}` : ""}`,
          ),
        );
      }
    });
  });
}

async function docker(args, options) {
  return execute("docker", args, options);
}

async function assertDocker() {
  await docker(["info"], { capture: true });
}

export async function buildImages() {
  await assertDocker();
  await docker([
    "build",
    "--file",
    "worlds/runtime/Dockerfile",
    "--tag",
    "convoy/world-runtime:dev",
    ".",
  ]);
  await docker([
    "build",
    "--file",
    "runtime/supervisor/Dockerfile",
    "--tag",
    "convoy/agent-runtime:dev",
    ".",
  ]);
}

async function waitForWorld(containerName) {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    const result = await docker(
      [
        "exec",
        containerName,
        "node",
        "-e",
        "fetch('http://127.0.0.1:8788/health').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))",
      ],
      { capture: true, allowFailure: true },
    );
    if (result.code === 0) return;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("World container did not become healthy.");
}

async function appendHostEvent(eventsPath, spec, sequence, type, data) {
  await appendFile(
    eventsPath,
    `${JSON.stringify({
      apiVersion: "convoy.ai/v1alpha1",
      run_id: spec.metadata.id,
      sequence,
      timestamp: new Date().toISOString(),
      type,
      source: "executor",
      data,
    })}\n`,
    "utf8",
  );
}

export async function runLocal(specFile) {
  await assertDocker();
  const absoluteSpecPath = path.resolve(process.cwd(), specFile);
  const spec = JSON.parse(await readFile(absoluteSpecPath, "utf8"));
  assertValid(validateRunSpec(spec), absoluteSpecPath);
  if (spec.runtime.executor !== "local-docker") {
    throw new Error(
      `The local executor cannot run ${spec.runtime.executor} specs.`,
    );
  }
  if (!["world-only", "open-egress"].includes(spec.runtime.network.mode)) {
    throw new Error(
      `The local executor currently supports world-only or open-egress networking, not ${spec.runtime.network.mode}.`,
    );
  }

  const actionPath = path.resolve(root, spec.world.scenario_actions);
  if (!actionPath.startsWith(`${root}${path.sep}`)) {
    throw new Error("world.scenario_actions must stay inside the repository.");
  }
  await readFile(actionPath, "utf8");

  const suffix = new Date().toISOString().replace(/[:.]/g, "-");
  const resourceBase = safeName(`${spec.metadata.id}-${suffix}`);
  const runDirectory = path.join(
    root,
    ".convoy",
    "runs",
    `${spec.metadata.id}-${suffix}`,
  );
  const outputDirectory = path.join(runDirectory, "output");
  const workspaceDirectory = path.join(runDirectory, "workspace");
  const resolvedSpecPath = path.join(runDirectory, "run.json");
  const resolvedActionsPath = path.join(runDirectory, "actions.json");
  const eventsPath = path.join(outputDirectory, "events.jsonl");
  const networkName = `convoy-${resourceBase}`;
  const configVolume = `${resourceBase}-config`;
  const outputVolume = `${resourceBase}-output`;
  const workspaceVolume = `${resourceBase}-workspace`;
  const worldVolume = `${resourceBase}-world-data`;
  const initContainer = `${resourceBase}-init`;
  const worldContainer = `${resourceBase}-world`;
  const agentContainer = `${resourceBase}-agent`;
  const keep = process.env.CONVOY_KEEP_RESOURCES === "1";

  await Promise.all([
    mkdir(outputDirectory, { recursive: true }),
    mkdir(workspaceDirectory, { recursive: true }),
  ]);
  await Promise.all([
    chmod(outputDirectory, 0o777),
    chmod(workspaceDirectory, 0o777),
  ]);
  await writeFile(
    resolvedSpecPath,
    `${JSON.stringify(spec, null, 2)}\n`,
    "utf8",
  );
  await cp(actionPath, resolvedActionsPath);
  await appendHostEvent(eventsPath, spec, 1, "run.accepted", {
    executor: "local-docker",
    resolved_spec: resolvedSpecPath,
  });
  await appendHostEvent(eventsPath, spec, 2, "run.preparing", {
    world_id: spec.world.id,
    network_mode: spec.runtime.network.mode,
  });
  await chmod(eventsPath, 0o666);

  let runResult;
  try {
    for (const volume of [
      configVolume,
      outputVolume,
      workspaceVolume,
      worldVolume,
    ]) {
      await docker(["volume", "create", volume], { capture: true });
    }

    await docker([
      "create",
      "--name",
      initContainer,
      "--user",
      "root",
      "--mount",
      `type=volume,src=${configVolume},dst=/convoy/config`,
      "--mount",
      `type=volume,src=${outputVolume},dst=/convoy/output`,
      "--entrypoint",
      "sh",
      spec.runtime.image,
      "-c",
      "chown -R 10001:10001 /convoy/config /convoy/output",
    ], { capture: true });
    await docker(["cp", resolvedSpecPath, `${initContainer}:/convoy/config/run.json`]);
    await docker([
      "cp",
      resolvedActionsPath,
      `${initContainer}:/convoy/config/actions.json`,
    ]);
    await docker(["cp", eventsPath, `${initContainer}:/convoy/output/events.jsonl`]);
    await docker(["start", "--attach", initContainer], { capture: true });

    const networkArguments = ["network", "create"];
    if (spec.runtime.network.mode === "world-only") networkArguments.push("--internal");
    networkArguments.push(networkName);
    await docker(networkArguments, { capture: true });

    await docker([
      "run",
      "--detach",
      "--name",
      worldContainer,
      "--network",
      networkName,
      "--network-alias",
      "world",
      "--read-only",
      "--tmpfs",
      "/tmp:rw,noexec,nosuid,size=32m",
      "--cap-drop",
      "ALL",
      "--security-opt",
      "no-new-privileges",
      "--cpus",
      "0.5",
      "--memory",
      "256m",
      "--pids-limit",
      "128",
      "--mount",
      `type=volume,src=${worldVolume},dst=/data`,
      spec.world.image,
    ], { capture: true });
    await waitForWorld(worldContainer);

    const agentArguments = [
      "create",
      "--name",
      agentContainer,
      "--network",
      networkName,
      "--read-only",
      "--tmpfs",
      "/tmp:rw,noexec,nosuid,size=64m",
      "--cap-drop",
      "ALL",
      "--security-opt",
      "no-new-privileges",
      "--cpus",
      String(spec.runtime.resources.cpu),
      "--memory",
      `${spec.runtime.resources.memory_mb}m`,
      "--pids-limit",
      String(spec.runtime.resources.pids),
      "--mount",
      `type=volume,src=${configVolume},dst=/convoy/config,readonly`,
      "--mount",
      `type=volume,src=${outputVolume},dst=/convoy/output`,
      "--mount",
      `type=volume,src=${workspaceVolume},dst=/workspace`,
      "--env",
      "CONVOY_RUN_SPEC=/convoy/config/run.json",
      "--env",
      "CONVOY_WORLD_URL=http://world:8788",
      "--env",
      "CONVOY_SCENARIO_ACTIONS=/convoy/config/actions.json",
      "--env",
      "CONVOY_EVENT_SEQUENCE_START=2",
    ];
    for (const [name, value] of Object.entries(spec.runtime.environment ?? {})) {
      agentArguments.push("--env", `${name}=${value}`);
    }
    agentArguments.push(spec.runtime.image);

    await docker(agentArguments, { capture: true });
    const agentRun = await docker(
      ["start", "--attach", agentContainer],
      { allowFailure: true },
    );
    await docker(
      ["cp", `${agentContainer}:/convoy/output/.`, outputDirectory],
      { capture: true, allowFailure: true },
    );
    await docker(
      ["cp", `${agentContainer}:/workspace/.`, workspaceDirectory],
      { capture: true, allowFailure: true },
    );
    let result = null;
    try {
      result = JSON.parse(
        await readFile(path.join(outputDirectory, "result.json"), "utf8"),
      );
    } catch {
      // Preserve the Docker result below when the supervisor could not start.
    }
    runResult = {
      docker_exit_code: agentRun.code,
      run_directory: runDirectory,
      result,
    };
    if (agentRun.code !== 0 || result?.status !== "run.succeeded") {
      throw new Error(
        `Agent run failed; artifacts retained at ${runDirectory}.`,
      );
    }
    return runResult;
  } finally {
    if (!keep) {
      await docker(["rm", "--force", initContainer], {
        capture: true,
        allowFailure: true,
      });
      await docker(["rm", "--force", agentContainer], {
        capture: true,
        allowFailure: true,
      });
      await docker(["rm", "--force", worldContainer], {
        capture: true,
        allowFailure: true,
      });
      await docker(["network", "rm", networkName], {
        capture: true,
        allowFailure: true,
      });
      for (const volume of [
        configVolume,
        outputVolume,
        workspaceVolume,
        worldVolume,
      ]) {
        await docker(["volume", "rm", "--force", volume], {
          capture: true,
          allowFailure: true,
        });
      }
    }
  }
}
