import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { scoreTrend } from "@/lib/api/evals";
import type { ChangelogEntry } from "@/lib/api/learning";
import { submitFeedbackForm } from "@/lib/feedback/actions";
import { listFeedbackForRoutine } from "@/lib/feedback/queries";
import { listMembers, listTeams } from "@/lib/orgs/queries";
import { can } from "@/lib/permissions";
import {
  assignApprover,
  removeApprover,
  runRoutineNow,
  setRoutineWorkspace,
  updateTriggerSchedule,
} from "@/lib/routines/actions";
import { getRoutineDetail } from "@/lib/routines/data";
import { requireRoutinesPage } from "@/lib/routines/gate";
import { orgTenantId } from "@/lib/routines/queries";
import { missingSystems, systemDisplayNames } from "@/lib/workspaces/fit";
import { improveCopy } from "@/lexicon";
import { RoutineDetail } from "./RoutineDetail";

export const metadata: Metadata = { title: "Routine" };
export const dynamic = "force-dynamic";

/**
 * Routine detail: everything about the routine today, read
 * rich. The page binds server actions and re-derives every permission
 * server-side; RoutineDetail just renders.
 */
export default async function RoutineDetailPage({
  params,
}: {
  params: Promise<{ routineId: string }>;
}) {
  const { routineId } = await params;
  const { session, membership } = await requireRoutinesPage();
  const tenantId = await orgTenantId(session.orgId);
  const data = await getRoutineDetail(
    session.orgId,
    { actorId: session.userId, tenantId },
    routineId,
  );
  if (!data) notFound();

  const canAssignApprovers = can("edit_routines_rehearsal", membership.role, membership.capabilities);
  const canRunNow = can("trigger_production_run", membership.role, membership.capabilities);
  const canEditTriggers = can("promote", membership.role, membership.capabilities);
  const canGiveFeedback = can("give_feedback", membership.role, membership.capabilities);
  const [members, teams] = canAssignApprovers
    ? await Promise.all([listMembers(session.orgId), listTeams(session.orgId)])
    : [[], []];

  // The trend is real score history; the changelog's baseline entry is the
  // routine's own arrival. Shipped improvements will append here once the
  // learning service exists.
  const [trend, feedback] = await Promise.all([
    scoreTrend(session.orgId, routineId),
    listFeedbackForRoutine(session.orgId, routineId),
  ]);
  const changelog: ChangelogEntry[] = [
    {
      routineId: data.routine.id,
      at: data.routine.createdAt.toISOString(),
      note: data.routine.sourceEntryId
        ? improveCopy.changelogInstalledNote
        : improveCopy.changelogCreatedNote,
    },
  ];

  // Feedback composed on the routine page attaches to the latest run;
  // without any runs the composer renders disabled with a plain note.
  const latestRunId = data.runs[0]?.runId ?? null;
  const feedbackAction = latestRunId
    ? submitFeedbackForm.bind(null, latestRunId, null)
    : undefined;

  async function assignAction(formData: FormData) {
    "use server";
    const [assigneeType = "", assigneeId = ""] = String(formData.get("assignee") ?? "").split(":");
    await assignApprover(routineId, assigneeType, assigneeId);
  }

  async function removeAction(formData: FormData) {
    "use server";
    await removeApprover(routineId, String(formData.get("assignmentId") ?? ""));
  }

  async function runNowAction() {
    "use server";
    await runRoutineNow(routineId);
  }

  async function scheduleAction(formData: FormData) {
    "use server";
    await updateTriggerSchedule(routineId, String(formData.get("schedule") ?? ""));
  }

  async function changeWorkspaceAction(formData: FormData) {
    "use server";
    await setRoutineWorkspace(routineId, String(formData.get("workspaceId") ?? ""));
  }

  const canChangeWorkspace = can("manage_workspaces", membership.role, membership.capabilities);
  const names = systemDisplayNames(data.workspaces);
  const workspaceOptions = data.workspaces.map((candidate) => ({
    id: candidate.id,
    name: candidate.name,
    missing: missingSystems(data.routine.systems, candidate).map((id) => names[id] ?? id),
  }));
  const staleNotice = data.staleAssignment
    ? `${data.staleAssignment.workspace.name} no longer connects ${data.staleAssignment.missingSystems
        .map((id) => names[id] ?? id)
        .join(", ")}. Runs use ${data.workspace?.name ?? "no workspace"} until this routine is moved.`
    : null;

  return (
    <RoutineDetail
      routine={data.routine}
      workspace={data.workspace ? { id: data.workspace.id, name: data.workspace.name } : null}
      workspaceOptions={workspaceOptions}
      staleNotice={staleNotice}
      canChangeWorkspace={canChangeWorkspace}
      changeWorkspaceAction={changeWorkspaceAction}
      systems={data.systems}
      checkpoints={[]}
      approvers={data.approvers}
      triggers={data.triggers}
      runs={data.runs}
      assignablePeople={members
        .filter((member) => member.status === "active" && member.role !== "viewer")
        .map((member) => ({ id: member.userId, name: member.name }))}
      assignableTeams={teams.map((team) => ({ id: team.id, name: team.name }))}
      canAssignApprovers={canAssignApprovers}
      canEditTriggers={canEditTriggers}
      canRunNow={canRunNow}
      runTarget={canEditTriggers ? "production" : "rehearsal"}
      assignAction={assignAction}
      removeAction={removeAction}
      runNowAction={runNowAction}
      scheduleAction={scheduleAction}
      trend={trend}
      changelog={changelog}
      feedback={feedback}
      canGiveFeedback={canGiveFeedback}
      feedbackAction={feedbackAction}
    />
  );
}
