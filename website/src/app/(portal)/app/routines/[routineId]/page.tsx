import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { listMembers, listTeams } from "@/lib/orgs/queries";
import { can } from "@/lib/permissions";
import {
  assignApprover,
  removeApprover,
  runRoutineNow,
  updateTriggerSchedule,
} from "@/lib/routines/actions";
import { getRoutineDetail } from "@/lib/routines/data";
import { requireRoutinesPage } from "@/lib/routines/gate";
import { orgTenantId } from "@/lib/routines/queries";
import { RoutineDetail } from "./RoutineDetail";

export const metadata: Metadata = { title: "Routine" };

/**
 * Routine detail (UI-SPEC C3): everything about the routine today, read
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
  const [members, teams] = canAssignApprovers
    ? await Promise.all([listMembers(session.orgId), listTeams(session.orgId)])
    : [[], []];

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

  return (
    <RoutineDetail
      routine={data.routine}
      workspace={data.workspace ? { id: data.workspace.id, name: data.workspace.name } : null}
      systems={data.systems}
      checkpoints={data.checkpoints}
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
    />
  );
}
