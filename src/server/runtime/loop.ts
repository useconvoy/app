import { db, persist } from "../db";
import { emit } from "../events";
import { id, now } from "../ids";
import { callTool, executeApprovedCall } from "../gateway";
import { planNextAction } from "./planner";
import { isLiveModelEnabled, advanceClaudeRun, resumeClaudeRun } from "./claude";
import type { Run, TriggerType } from "../types";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export function startRun(opts: {
  deploymentId: string;
  triggerType: TriggerType;
  triggerPayload: Record<string, unknown>;
  scenarioId?: string;
}): Run {
  const d = db();
  const dep = d.deployments.find((x) => x.id === opts.deploymentId);
  if (!dep || dep.status !== "active") throw new Error("No active deployment");
  const agent = d.agents.find((a) => a.id === dep.agentId)!;
  if (agent.paused) throw new Error(`${agent.name} is paused fleet-wide (kill switch)`);
  const run: Run = {
    id: id("run"),
    deploymentId: dep.id,
    agentId: dep.agentId,
    agentVersionId: dep.agentVersionId,
    environmentId: dep.environmentId,
    triggerType: opts.triggerType,
    triggerPayload: opts.triggerPayload,
    scenarioId: opts.scenarioId,
    state: "queued",
    startedAt: now(),
  };
  d.runs.push(run);
  d.auditEvents.push({
    id: id("aud"),
    ts: now(),
    actor: "system",
    type: "run.started",
    message: `Run started for ${agent.name} (${opts.triggerType} trigger)`,
    refs: { runId: run.id, agentId: agent.id, environmentId: dep.environmentId },
  });
  persist();
  emit({ type: "run_updated", runId: run.id, payload: run });
  return run;
}

/**
 * Drive a run forward until it completes or pauses on an approval gate.
 * Stateless with respect to memory: the next action is always derived from the
 * persisted trace, so this is safely re-entrant after pause/resume.
 */
export async function advanceRun(runId: string): Promise<void> {
  const d = db();
  const run = d.runs.find((r) => r.id === runId);
  if (!run) return;
  if (run.state !== "queued" && run.state !== "running") return;
  run.state = "running";
  persist();
  emit({ type: "run_updated", runId: run.id, payload: run });

  if (isLiveModelEnabled()) {
    await advanceClaudeRun(runId);
    return;
  }

  const version = d.agentVersions.find((v) => v.id === run.agentVersionId)!;
  // Test runs execute fast; interactive runs are paced so the live trace reads.
  const pace = run.triggerType === "test" ? 25 : 650;

  for (let guard = 0; guard < 40; guard++) {
    const agent = d.agents.find((a) => a.id === run.agentId)!;
    if (agent.paused) {
      finishRun(run, "killed", "Run stopped: agent paused fleet-wide (kill switch).");
      return;
    }
    const action = planNextAction(run, version);
    if (action.type === "complete") {
      finishRun(run, "succeeded", action.summary);
      return;
    }
    if (action.type === "fail") {
      finishRun(run, "failed", action.summary);
      return;
    }
    await sleep(pace);
    const result = callTool(run.id, action.tool, action.args);
    if (result.status === "pending_approval") return; // run is paused; approval decision resumes it
    if (result.status === "killed") {
      finishRun(run, "killed", "Run stopped mid-flight: agent paused fleet-wide (kill switch).");
      return;
    }
    if (result.status === "denied") {
      finishRun(run, "failed", `Run stopped: ${action.tool} denied by policy — ${result.reason}`);
      return;
    }
  }
  finishRun(run, "failed", "Run exceeded maximum step count.");
}

export function finishRun(run: Run, state: Run["state"], summary: string): void {
  const d = db();
  run.state = state;
  run.summary = summary;
  run.endedAt = now();
  d.auditEvents.push({
    id: id("aud"),
    ts: now(),
    actor: `agent:${run.agentId}`,
    type: `run.${state}`,
    message: summary,
    refs: { runId: run.id, agentId: run.agentId, environmentId: run.environmentId },
  });
  persist();
  emit({ type: "run_updated", runId: run.id, payload: run });
}

/** Human decision on a pending approval — the resume/terminate path (spec §6.2 steps 5-6). */
export async function decideApproval(approvalId: string, decision: "approve" | "reject", approver: string): Promise<void> {
  const d = db();
  const approval = d.approvals.find((a) => a.id === approvalId);
  if (!approval || approval.status !== "pending") return;
  const run = d.runs.find((r) => r.id === approval.runId)!;
  const tc = d.toolCalls.find((t) => t.id === approval.toolCallId)!;

  approval.status = decision === "approve" ? "approved" : "rejected";
  approval.approver = approver;
  approval.decidedAt = now();
  d.auditEvents.push({
    id: id("aud"),
    ts: now(),
    actor: approver,
    type: `approval.${approval.status}`,
    message: `${decision === "approve" ? "Approved" : "Rejected"}: ${approval.title}`,
    refs: { runId: run.id, approvalId: approval.id, toolCallId: tc.id, agentId: run.agentId, environmentId: run.environmentId },
  });
  persist();
  emit({ type: "approval_decided", runId: run.id, payload: approval });

  if (decision === "reject") {
    tc.status = "rejected";
    tc.result = { rejected: true, by: approver };
    persist();
    emit({ type: "tool_call", runId: run.id, payload: tc });
    finishRun(run, "rejected", `Run terminated: '${approval.title}' rejected by ${approver}.`);
    return;
  }

  // Approve → execute the gated call with approver identity on the trace, then resume the loop.
  executeApprovedCall(tc.id, approver);
  run.state = "running";
  persist();
  emit({ type: "run_updated", runId: run.id, payload: run });
  if (isLiveModelEnabled() && run.pendingModelToolUseId) {
    await resumeClaudeRun(run.id);
  } else {
    await advanceRun(run.id);
  }
}
