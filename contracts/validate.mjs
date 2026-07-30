const idPattern = /^[a-z][a-z0-9-]{2,62}$/;
const executors = new Set([
  "local-docker",
  "agentcore",
  "ecs-ec2",
  "firecracker",
]);
const networkModes = new Set([
  "world-only",
  "restricted-egress",
  "open-egress",
  "none",
]);
const eventTypes = new Set([
  "run.accepted",
  "run.preparing",
  "run.started",
  "run.heartbeat",
  "agent.stdout",
  "agent.stderr",
  "world.action",
  "world.gate",
  "world.evaluated",
  "artifact.created",
  "run.succeeded",
  "run.failed",
  "run.timed_out",
  "run.cancelled",
]);
const eventSources = new Set([
  "control-plane",
  "executor",
  "supervisor",
  "agent",
  "world",
]);

function object(value) {
  return value && typeof value === "object" && !Array.isArray(value);
}

export function validateRunSpec(spec) {
  const errors = [];
  const requireValue = (condition, message) => {
    if (!condition) errors.push(message);
  };

  requireValue(object(spec), "RunSpec must be an object.");
  if (!object(spec)) return errors;
  requireValue(spec.apiVersion === "convoy.ai/v1alpha1", "Unsupported apiVersion.");
  requireValue(spec.kind === "Run", "kind must be Run.");
  requireValue(object(spec.metadata), "metadata is required.");
  requireValue(idPattern.test(spec.metadata?.id ?? ""), "metadata.id is invalid.");
  requireValue(Boolean(spec.metadata?.mission_id), "metadata.mission_id is required.");
  requireValue(object(spec.runtime), "runtime is required.");
  requireValue(
    executors.has(spec.runtime?.executor),
    "runtime.executor is unsupported.",
  );
  requireValue(Boolean(spec.runtime?.image), "runtime.image is required.");
  requireValue(
    Array.isArray(spec.runtime?.command) && spec.runtime.command.length > 0,
    "runtime.command must contain at least one argument.",
  );
  requireValue(
    Number.isInteger(spec.runtime?.timeout_seconds) &&
      spec.runtime.timeout_seconds >= 30 &&
      spec.runtime.timeout_seconds <= 28800,
    "runtime.timeout_seconds must be between 30 and 28800.",
  );
  requireValue(
    Number(spec.runtime?.resources?.cpu) > 0,
    "runtime.resources.cpu must be positive.",
  );
  requireValue(
    Number.isInteger(spec.runtime?.resources?.memory_mb) &&
      spec.runtime.resources.memory_mb >= 128,
    "runtime.resources.memory_mb must be at least 128.",
  );
  requireValue(
    Number.isInteger(spec.runtime?.resources?.pids) &&
      spec.runtime.resources.pids >= 16,
    "runtime.resources.pids must be at least 16.",
  );
  requireValue(
    networkModes.has(spec.runtime?.network?.mode),
    "runtime.network.mode is unsupported.",
  );
  requireValue(object(spec.world), "world is required.");
  requireValue(idPattern.test(spec.world?.id ?? ""), "world.id is invalid.");
  requireValue(Boolean(spec.world?.image), "world.image is required.");
  requireValue(
    Boolean(spec.world?.scenario_actions),
    "world.scenario_actions is required.",
  );
  requireValue(object(spec.artifacts), "artifacts is required.");
  requireValue(
    Array.isArray(spec.artifacts?.paths) &&
      spec.artifacts.paths.every((item) =>
        item.startsWith("/convoy/output/"),
      ),
    "artifact paths must live under /convoy/output/.",
  );
  for (const secret of spec.secrets ?? []) {
    requireValue(
      /^[A-Z][A-Z0-9_]*$/.test(secret.name ?? ""),
      "secret names must be uppercase environment variable names.",
    );
    requireValue(Boolean(secret.reference), "secret reference is required.");
    requireValue(
      !String(secret.reference ?? "").match(/^(sk-|ghp_|AKIA)/),
      "secret references must not contain secret values.",
    );
  }
  return errors;
}

export function validateWorldSpec(spec) {
  const errors = [];
  const requireValue = (condition, message) => {
    if (!condition) errors.push(message);
  };
  requireValue(object(spec), "WorldSpec must be an object.");
  if (!object(spec)) return errors;
  requireValue(spec.apiVersion === "convoy.ai/v1alpha1", "Unsupported apiVersion.");
  requireValue(spec.kind === "World", "kind must be World.");
  requireValue(idPattern.test(spec.metadata?.id ?? ""), "metadata.id is invalid.");
  requireValue(Boolean(spec.metadata?.name), "metadata.name is required.");
  requireValue(
    /^\d+\.\d+\.\d+$/.test(spec.metadata?.version ?? ""),
    "metadata.version must be semver.",
  );
  requireValue(Boolean(spec.scenario?.instruction), "scenario.instruction is required.");
  requireValue(
    Array.isArray(spec.services) && spec.services.length > 0,
    "services must not be empty.",
  );
  requireValue(
    Array.isArray(spec.human_actors) && spec.human_actors.length > 0,
    "human_actors must not be empty.",
  );
  requireValue(
    Array.isArray(spec.tools) && spec.tools.length > 0,
    "tools must not be empty.",
  );
  const toolNames = new Set();
  for (const tool of spec.tools ?? []) {
    requireValue(
      /^[a-z]+\.[a-z_]+$/.test(tool.name ?? ""),
      `Invalid tool name: ${tool.name}`,
    );
    requireValue(!toolNames.has(tool.name), `Duplicate tool: ${tool.name}`);
    toolNames.add(tool.name);
  }
  requireValue(
    String(spec.evaluation?.module ?? "").startsWith("./"),
    "evaluation.module must be relative.",
  );
  requireValue(
    Number.isInteger(spec.evaluation?.pass_score) &&
      spec.evaluation.pass_score >= 0 &&
      spec.evaluation.pass_score <= 100,
    "evaluation.pass_score must be between 0 and 100.",
  );
  return errors;
}

export function validateRunEvent(event) {
  const errors = [];
  const requireValue = (condition, message) => {
    if (!condition) errors.push(message);
  };
  requireValue(object(event), "RunEvent must be an object.");
  if (!object(event)) return errors;
  requireValue(
    event.apiVersion === "convoy.ai/v1alpha1",
    "Unsupported apiVersion.",
  );
  requireValue(Boolean(event.run_id), "run_id is required.");
  requireValue(
    Number.isInteger(event.sequence) && event.sequence >= 1,
    "sequence must be a positive integer.",
  );
  requireValue(
    typeof event.timestamp === "string" &&
      !Number.isNaN(Date.parse(event.timestamp)),
    "timestamp must be an ISO date-time.",
  );
  requireValue(eventTypes.has(event.type), "type is unsupported.");
  requireValue(eventSources.has(event.source), "source is unsupported.");
  requireValue(object(event.data), "data must be an object.");
  return errors;
}

export function assertValid(errors, label) {
  if (errors.length > 0) {
    throw new Error(`${label} is invalid:\n- ${errors.join("\n- ")}`);
  }
}
