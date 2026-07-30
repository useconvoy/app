import { cp, mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const moduleDirectory = path.dirname(fileURLToPath(import.meta.url));
export const worldsRoot =
  process.env.CONVOY_WORLDS_ROOT ?? path.resolve(moduleDirectory, "..");
export const runtimeRoot =
  process.env.CONVOY_WORLD_STATE_ROOT ?? path.resolve(worldsRoot, ".runtime");

function catalogDirectory(worldId) {
  return path.join(worldsRoot, "catalog", worldId);
}

function runtimeDirectory(worldId) {
  return path.join(runtimeRoot, worldId);
}

function statePath(worldId) {
  return path.join(runtimeDirectory(worldId), "state.json");
}

async function readJson(filePath) {
  return JSON.parse(await readFile(filePath, "utf8"));
}

async function writeJson(filePath, value) {
  await writeFile(filePath, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

export async function listWorlds() {
  const entries = await readdir(path.join(worldsRoot, "catalog"), {
    withFileTypes: true,
  });
  return Promise.all(
    entries
      .filter((entry) => entry.isDirectory())
      .map(async (entry) => {
        const manifest = await getManifest(entry.name);
        return {
          id: manifest.metadata.id,
          name: manifest.metadata.name,
          version: manifest.metadata.version,
          description: manifest.metadata.description,
          scenario: manifest.scenario,
          services: manifest.services,
        };
      }),
  );
}

export async function getManifest(worldId) {
  return readJson(path.join(catalogDirectory(worldId), "world.json"));
}

export async function resetWorld(worldId) {
  await getManifest(worldId);
  const destination = runtimeDirectory(worldId);
  await mkdir(destination, { recursive: true });
  await cp(
    path.join(catalogDirectory(worldId), "initial-state.json"),
    statePath(worldId),
  );
  const state = await readJson(statePath(worldId));
  state.runtime = {
    reset_at: new Date().toISOString(),
    action_count: 0,
    denied_action_count: 0,
    fabricated_evidence_count: 0,
    idempotent_replay_count: 0,
  };
  await writeJson(statePath(worldId), state);
  return state;
}

export async function getState(worldId) {
  try {
    return await readJson(statePath(worldId));
  } catch (error) {
    if (error?.code === "ENOENT") return resetWorld(worldId);
    throw error;
  }
}

function requireFields(input, fields) {
  const missing = fields.filter((field) => input[field] === undefined);
  if (missing.length > 0) {
    throw new Error(`Missing required input: ${missing.join(", ")}`);
  }
}

function recordEvent(state, event) {
  state.audit_log.push({
    id: `evt_${String(state.audit_log.length + 1).padStart(4, "0")}`,
    timestamp: new Date().toISOString(),
    ...event,
  });
}

function deny(state, actor, tool, input, reason) {
  state.runtime.denied_action_count += 1;
  recordEvent(state, {
    actor,
    tool,
    input,
    status: "denied",
    reason,
  });
  return { ok: false, denied: true, reason };
}

function hasApprovedGate(state, type, targetId) {
  return state.approvals.some(
    (approval) =>
      approval.type === type &&
      approval.target_id === targetId &&
      approval.status === "approved",
  );
}

function nextObjectId(prefix, collection) {
  return `${prefix}_${String(collection.length + 1).padStart(4, "0")}`;
}

function existingIdempotent(collection, key) {
  if (!key) return null;
  return collection.find((item) => item.idempotency_key === key) ?? null;
}

export async function applyAction(worldId, action) {
  const manifest = await getManifest(worldId);
  const state = await getState(worldId);
  const actor = action.actor;
  const tool = action.tool;
  const input = action.input ?? {};
  if (!actor || !tool) throw new Error("Actions require actor and tool.");
  if (!manifest.tools.some((candidate) => candidate.name === tool)) {
    throw new Error(`Unknown tool: ${tool}`);
  }

  state.runtime.action_count += 1;
  let result;

  switch (tool) {
    case "vendor.list": {
      const vendors = (state.vendors ?? []).filter((vendor) => {
        if (input.criticality && vendor.criticality !== input.criticality) {
          return false;
        }
        if (input.review_status && vendor.review_status !== input.review_status) {
          return false;
        }
        return true;
      });
      result = {
        ok: true,
        items: vendors.slice(0, Math.min(Number(input.limit ?? 100), 250)),
        total: vendors.length,
      };
      break;
    }

    case "vendor.read": {
      requireFields(input, ["vendor_id"]);
      const vendor = state.vendors?.find((item) => item.id === input.vendor_id);
      if (!vendor) throw new Error(`Unknown vendor: ${input.vendor_id}`);
      result = {
        ok: true,
        vendor,
        requirements: (state.evidence_requirements ?? []).filter(
          (item) => item.vendor_id === vendor.id,
        ),
        evidence: (state.evidence ?? []).filter(
          (item) => item.vendor_id === vendor.id,
        ),
        findings: (state.findings ?? []).filter(
          (item) => item.vendor_id === vendor.id,
        ),
        exceptions: (state.exceptions ?? []).filter(
          (item) => item.vendor_id === vendor.id,
        ),
        tasks: (state.tasks ?? []).filter(
          (item) => item.vendor_id === vendor.id,
        ),
      };
      break;
    }

    case "evidence.search": {
      const evidence = (state.evidence ?? []).filter((item) => {
        if (input.vendor_id && item.vendor_id !== input.vendor_id) return false;
        if (input.requirement_id && item.requirement_id !== input.requirement_id) {
          return false;
        }
        if (input.domain && item.domain !== input.domain) return false;
        return true;
      });
      result = {
        ok: true,
        items: evidence.slice(0, Math.min(Number(input.limit ?? 100), 500)),
        total: evidence.length,
      };
      break;
    }

    case "evidence.record_finding": {
      requireFields(input, [
        "vendor_id",
        "requirement_id",
        "outcome",
        "rationale",
        "idempotency_key",
      ]);
      const allowedOutcomes = new Set([
        "valid",
        "missing",
        "stale",
        "contradictory",
      ]);
      if (!allowedOutcomes.has(input.outcome)) {
        throw new Error(`Unsupported evidence outcome: ${input.outcome}`);
      }
      const vendor = state.vendors?.find((item) => item.id === input.vendor_id);
      const requirement = state.evidence_requirements?.find(
        (item) => item.id === input.requirement_id,
      );
      if (!vendor) throw new Error(`Unknown vendor: ${input.vendor_id}`);
      if (!requirement || requirement.vendor_id !== vendor.id) {
        throw new Error(`Unknown vendor requirement: ${input.requirement_id}`);
      }
      const previous = existingIdempotent(
        state.findings ?? [],
        input.idempotency_key,
      );
      if (previous) {
        state.runtime.idempotent_replay_count += 1;
        result = { ok: true, replayed: true, object: previous };
        break;
      }
      const evidenceIds = input.evidence_ids ?? [];
      if (!Array.isArray(evidenceIds)) {
        throw new Error("evidence_ids must be an array.");
      }
      const referencedEvidence = evidenceIds.map((evidenceId) =>
        state.evidence?.find((item) => item.id === evidenceId),
      );
      if (
        referencedEvidence.some(
          (item) =>
            !item ||
            item.vendor_id !== vendor.id ||
            item.requirement_id !== requirement.id,
        )
      ) {
        state.runtime.fabricated_evidence_count += 1;
        result = deny(
          state,
          actor,
          tool,
          input,
          "Findings may only reference evidence belonging to the requirement.",
        );
        break;
      }
      if (input.outcome === "missing" && evidenceIds.length > 0) {
        result = deny(
          state,
          actor,
          tool,
          input,
          "A missing-evidence finding cannot cite an evidence artifact.",
        );
        break;
      }
      if (input.outcome !== "missing" && evidenceIds.length === 0) {
        result = deny(
          state,
          actor,
          tool,
          input,
          "Non-missing findings require at least one source evidence artifact.",
        );
        break;
      }
      const finding = {
        id: nextObjectId("finding", state.findings),
        recorded_by: actor,
        recorded_at: new Date().toISOString(),
        ...input,
      };
      state.findings.push(finding);
      result = { ok: true, object: finding };
      break;
    }

    case "exception.create": {
      requireFields(input, [
        "vendor_id",
        "requirement_id",
        "severity",
        "reason",
        "idempotency_key",
      ]);
      const previous = existingIdempotent(
        state.exceptions ?? [],
        input.idempotency_key,
      );
      if (previous) {
        state.runtime.idempotent_replay_count += 1;
        result = { ok: true, replayed: true, object: previous };
        break;
      }
      const finding = state.findings?.find(
        (item) =>
          item.vendor_id === input.vendor_id &&
          item.requirement_id === input.requirement_id,
      );
      if (!finding || finding.outcome === "valid") {
        result = deny(
          state,
          actor,
          tool,
          input,
          "An exception requires a recorded non-valid evidence finding.",
        );
        break;
      }
      const exception = {
        id: nextObjectId("exception", state.exceptions),
        status: "open",
        created_by: actor,
        created_at: new Date().toISOString(),
        ...input,
      };
      state.exceptions.push(exception);
      result = { ok: true, object: exception };
      break;
    }

    case "chat.send": {
      requireFields(input, ["channel_id", "text"]);
      const channel = state.channels.find((item) => item.id === input.channel_id);
      if (!channel) throw new Error(`Unknown channel: ${input.channel_id}`);
      if (
        channel.kind === "external" &&
        !hasApprovedGate(state, "external_outreach", channel.id)
      ) {
        result = deny(
          state,
          actor,
          tool,
          input,
          "External outreach requires an approved human gate.",
        );
        break;
      }
      const message = {
        id: nextObjectId("msg", state.messages),
        author: actor,
        created_at: new Date().toISOString(),
        ...input,
      };
      state.messages.push(message);
      result = { ok: true, object: message };
      break;
    }

    case "crm.update_account": {
      requireFields(input, ["account_id", "changes"]);
      const account = state.accounts?.find((item) => item.id === input.account_id);
      if (!account) throw new Error(`Unknown account: ${input.account_id}`);
      const threshold = manifest.policies?.forecast_change_human_gate_usd;
      const movement = Math.abs(Number(input.changes.forecast_delta ?? 0));
      if (
        threshold &&
        movement > threshold &&
        !hasApprovedGate(state, "forecast_change", account.id)
      ) {
        result = deny(
          state,
          actor,
          tool,
          input,
          `Forecast movement above $${threshold.toLocaleString()} requires approval.`,
        );
        break;
      }
      Object.assign(account, input.changes, {
        updated_at: new Date().toISOString(),
        updated_by: actor,
      });
      result = { ok: true, object: account };
      break;
    }

    case "work.create_task": {
      requireFields(input, ["title", "owner", "due_date"]);
      if (!input.account_id && !input.vendor_id) {
        throw new Error("A task requires account_id or vendor_id.");
      }
      const previous = existingIdempotent(
        state.tasks,
        input.idempotency_key,
      );
      if (previous) {
        state.runtime.idempotent_replay_count += 1;
        result = { ok: true, replayed: true, object: previous };
        break;
      }
      const task = {
        id: nextObjectId("task", state.tasks),
        status: "open",
        created_by: actor,
        created_at: new Date().toISOString(),
        ...input,
      };
      state.tasks.push(task);
      result = { ok: true, object: task };
      break;
    }

    case "drive.write_document": {
      requireFields(input, ["title", "body"]);
      const previous = existingIdempotent(
        state.documents,
        input.idempotency_key,
      );
      if (previous) {
        state.runtime.idempotent_replay_count += 1;
        result = { ok: true, replayed: true, object: previous };
        break;
      }
      const document = {
        id: nextObjectId("doc", state.documents),
        created_by: actor,
        updated_at: new Date().toISOString(),
        ...input,
      };
      state.documents.push(document);
      result = { ok: true, object: document };
      break;
    }

    case "evidence.attach": {
      requireFields(input, ["vendor_id", "category", "source", "summary"]);
      if (!state.vendors?.some((vendor) => vendor.id === input.vendor_id)) {
        throw new Error(`Unknown vendor: ${input.vendor_id}`);
      }
      const evidence = {
        id: nextObjectId("evidence", state.evidence),
        attached_by: actor,
        attached_at: new Date().toISOString(),
        ...input,
      };
      state.evidence.push(evidence);
      result = { ok: true, object: evidence };
      break;
    }

    case "approval.request": {
      requireFields(input, ["type", "target_id", "reason"]);
      const previous = existingIdempotent(
        state.approvals,
        input.idempotency_key,
      );
      if (previous) {
        state.runtime.idempotent_replay_count += 1;
        result = { ok: true, replayed: true, object: previous };
        break;
      }
      const approval = {
        id: nextObjectId("approval", state.approvals),
        requested_by: actor,
        requested_at: new Date().toISOString(),
        status: "pending",
        ...input,
      };
      state.approvals.push(approval);
      result = { ok: true, object: approval };
      break;
    }

    case "approval.resolve": {
      requireFields(input, ["approval_id", "decision"]);
      if (!manifest.human_actors.includes(actor)) {
        result = deny(
          state,
          actor,
          tool,
          input,
          "Only a designated human can resolve an approval.",
        );
        break;
      }
      const approval = state.approvals.find(
        (item) => item.id === input.approval_id,
      );
      if (!approval) throw new Error(`Unknown approval: ${input.approval_id}`);
      if (!["approved", "rejected"].includes(input.decision)) {
        throw new Error("Approval decision must be approved or rejected.");
      }
      Object.assign(approval, {
        status: input.decision,
        resolved_by: actor,
        resolved_at: new Date().toISOString(),
      });
      result = { ok: true, object: approval };
      break;
    }

    case "decision.record": {
      requireFields(input, ["vendor_id", "decision", "rationale"]);
      if (!manifest.human_actors.includes(actor)) {
        result = deny(
          state,
          actor,
          tool,
          input,
          "Only a designated human can record a vendor decision.",
        );
        break;
      }
      if (!hasApprovedGate(state, "vendor_decision", input.vendor_id)) {
        result = deny(
          state,
          actor,
          tool,
          input,
          "Vendor decisions require an approved human gate.",
        );
        break;
      }
      const decision = {
        id: nextObjectId("decision", state.decisions),
        recorded_by: actor,
        recorded_at: new Date().toISOString(),
        ...input,
      };
      state.decisions.push(decision);
      result = { ok: true, object: decision };
      break;
    }

    default:
      throw new Error(`Tool not implemented: ${tool}`);
  }

  if (!result.denied) {
    recordEvent(state, {
      actor,
      tool,
      input,
      status: "succeeded",
      object_id: result.object?.id,
    });
  }
  await writeJson(statePath(worldId), state);
  return result;
}

export async function replayActions(worldId, actions) {
  const results = [];
  for (const action of actions) {
    results.push(await applyAction(worldId, action));
  }
  return results;
}

export async function evaluateWorld(worldId) {
  const manifest = await getManifest(worldId);
  const state = await getState(worldId);
  let groundTruth = null;
  try {
    groundTruth = await readJson(
      path.join(catalogDirectory(worldId), "ground-truth.json"),
    );
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
  const evaluatorUrl = pathToFileURL(
    path.join(catalogDirectory(worldId), manifest.evaluation.module),
  );
  const { evaluate } = await import(evaluatorUrl.href);
  const result = await evaluate({ manifest, state, groundTruth });
  const hardFailures = result.hard_failures ?? [];
  return {
    world_id: worldId,
    scenario: manifest.scenario,
    pass_score: manifest.evaluation.pass_score,
    ...result,
    hard_failures: hardFailures,
    passed:
      result.score >= manifest.evaluation.pass_score &&
      hardFailures.length === 0,
    runtime: state.runtime,
  };
}
