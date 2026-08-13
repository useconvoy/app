import type { Metadata } from "next";
import Link from "next/link";
import { notFound, redirect } from "next/navigation";

import { Button } from "@/components/Button";
import { StatusChip } from "@/components/StatusChip";
import { Select } from "@/components/ui/select";
import { environmentsClient, listEventRules, listSystemConnections } from "@/lib/api/environments";
import { isRehearsalTarget, listRuns } from "@/lib/api/runs";
import { suggestedEvents } from "@/lib/agents/events";
import { orgTenantId } from "@/lib/agents/queries";
import {
  SCHEDULE_PRESETS,
  parseStoredSchedule,
  presetForCron,
  scheduleDisplay,
} from "@/lib/agents/schedule";
import { friendlyDate, money } from "@/lib/format";
import { can } from "@/lib/permissions";
import { removeRoutine, runRoutineNow } from "@/lib/routines/actions";
import { requireRoutinesPage } from "@/lib/routines/gate";
import { getRoutineBinding } from "@/lib/routines/records";
import {
  addRoutineEventRule,
  clearRoutineSchedule,
  removeRoutineEventRule,
  setRoutineSchedule,
} from "@/lib/routines/trigger-actions";
import { missingSystems, systemDisplayNames } from "@/lib/workspaces/fit";

export const metadata: Metadata = { title: "Routine" };
export const dynamic = "force-dynamic";

export default async function RoutineDetailPage({
  params,
}: {
  params: Promise<{ routineId: string }>;
}) {
  const { routineId } = await params;
  const { session, membership } = await requireRoutinesPage();
  const routine = await getRoutineBinding(session.orgId, routineId);
  if (!routine) notFound();
  const [agent, workspace] = await Promise.all([
    environmentsClient().getAgent(session.orgId, routine.agentId),
    environmentsClient().getWorkspace(session.orgId, routine.workspaceId),
  ]);
  const canStart = can("trigger_production_run", membership.role, membership.capabilities);
  const canRunProduction = can("promote", membership.role, membership.capabilities);
  const canManage = can("manage_workspaces", membership.role, membership.capabilities);
  const schedule = parseStoredSchedule(routine.schedule);
  const missing = agent && workspace ? missingSystems(agent.systems, workspace) : [];
  const names = workspace ? systemDisplayNames([workspace]) : {};
  const runnable = agent !== null && workspace !== null && missing.length === 0;

  const eventRules =
    (await listEventRules(session.orgId, routine.id).catch(() => null)) ?? null;
  const connections = eventRules === null
    ? null
    : await listSystemConnections(session.orgId).catch(() => null);

  let recentRuns: Array<{
    id: string;
    status: string;
    rehearsal: boolean;
    spentUsd: string | null;
    createdAt: string;
  }> = [];
  try {
    const actor = { actorId: session.userId, tenantId: await orgTenantId(session.orgId) };
    recentRuns = (await listRuns(actor, { agentId: routine.id, limit: 20 })).map((run) => ({
      id: run.run_id,
      status: run.status,
      rehearsal: isRehearsalTarget(run.environment_id),
      spentUsd: run.budget?.spent_usd ?? null,
      createdAt: run.created_at,
    }));
  } catch {
    recentRuns = [];
  }

  async function startAction(formData: FormData) {
    "use server";
    const target = formData.get("target") === "production" ? "production" : "rehearsal";
    const { runId } = await runRoutineNow(routineId, target);
    redirect(`/app/runs/${runId}`);
  }

  async function removeAction() {
    "use server";
    await removeRoutine(routineId);
    redirect("/app/routines");
  }

  async function scheduleAction(formData: FormData) {
    "use server";
    if (formData.get("op") === "clear") {
      await clearRoutineSchedule(routineId);
      return;
    }
    await setRoutineSchedule(routineId, {
      preset: String(formData.get("preset") ?? "weekday_morning"),
      cron: String(formData.get("cron") ?? ""),
      timezone: String(formData.get("timezone") ?? "UTC"),
      target: formData.get("target") === "production" ? "production" : "rehearsal",
      enabled: formData.get("enabled") === "on",
    });
  }

  async function eventRuleAction(formData: FormData) {
    "use server";
    const removeId = formData.get("removeRuleId");
    if (typeof removeId === "string" && removeId) {
      await removeRoutineEventRule(routineId, removeId);
      return;
    }
    await addRoutineEventRule(routineId, {
      connectionId: String(formData.get("connectionId") ?? ""),
      eventType: String(formData.get("eventType") ?? ""),
      target: formData.get("target") === "production" ? "production" : "rehearsal",
      webhookSecret: String(formData.get("webhookSecret") ?? ""),
    });
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header>
        <p className="text-xs text-muted">
          <Link href="/app/routines" className="underline">Routines</Link>
        </p>
        <h1 className="mt-1 font-display text-3xl text-ink">{routine.name}</h1>
        <p className="mt-2 text-sm text-muted">
          {agent ? (
            <Link href={`/app/agents/${agent.id}`} className="text-ink underline">
              {agent.name}
            </Link>
          ) : (
            "Removed agent"
          )}
          {" running in "}
          {workspace ? (
            <Link href={`/app/workspaces/${workspace.id}`} className="text-ink underline">
              {workspace.name}
            </Link>
          ) : (
            "a removed workspace"
          )}
        </p>
        <p className="mt-2 font-mono text-xs text-muted">created {friendlyDate(routine.createdAt.toISOString())}</p>
      </header>

      {agent && workspace && missing.length > 0 && (
        <p role="status" className="rounded-md border border-hold-soft bg-hold-soft p-3 text-sm text-hold-text">
          {workspace.name} no longer connects {missing.map((id) => names[id] ?? id).join(", ")}.
          Reconnect them on the workspace before this routine can run.
        </p>
      )}

      <section className="rounded-lg border border-line bg-card p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="max-w-3xl">
            <h2 className="font-display text-lg text-ink">What it does</h2>
            <p className="mt-2 text-sm text-ink">{agent?.goal ?? "The agent behind this routine was removed."}</p>
            {agent && (
              <p className="mt-3 text-xs text-muted">{money(agent.budgetCapUsd)} per run</p>
            )}
          </div>
          {canStart && runnable && (
            <form action={startAction} className="flex flex-wrap gap-2">
              <Button type="submit" name="target" value="rehearsal">Run rehearsal</Button>
              {canRunProduction && (
                <Button type="submit" name="target" value="production" variant="secondary">
                  Run live
                </Button>
              )}
            </form>
          )}
        </div>
      </section>

      <section className="rounded-lg border border-line bg-card p-5">
        <h2 className="font-display text-lg text-ink">Schedule</h2>
        <p className="mt-2 text-sm text-muted">
          {schedule
            ? `Starts: ${scheduleDisplay(schedule)}`
            : "This routine starts on demand only."}
        </p>
        {canManage && runnable && (
          <form action={scheduleAction} className="mt-4 flex flex-wrap items-end gap-3 text-sm">
            <label className="flex flex-col gap-1">
              <span className="text-xs text-muted">When</span>
              <Select
                name="preset"
                defaultValue={schedule ? presetForCron(schedule.cron) : "weekday_morning"}
                className="w-52"
                aria-label="When the routine starts"
                options={[
                  ...Object.entries(SCHEDULE_PRESETS).map(([key, entry]) => ({
                    value: key,
                    label: entry.label,
                  })),
                  { value: "custom", label: "Custom cron" },
                ]}
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-xs text-muted">Custom cron (if selected)</span>
              <input
                name="cron"
                defaultValue={schedule?.cron ?? ""}
                placeholder="0 9 * * 1-5"
                className="w-36 rounded-sm border border-line bg-field px-2 py-1.5 font-mono text-xs"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-xs text-muted">Timezone</span>
              <input
                name="timezone"
                defaultValue={schedule?.timezone ?? "UTC"}
                className="w-40 rounded-sm border border-line bg-field px-2 py-1.5"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-xs text-muted">Runs as</span>
              <Select
                name="target"
                defaultValue={schedule?.target ?? "rehearsal"}
                className="w-36"
                aria-label="Schedule target"
                options={[
                  { value: "rehearsal", label: "Rehearsal" },
                  ...(canRunProduction ? [{ value: "production", label: "Live" }] : []),
                ]}
              />
            </label>
            <label className="flex items-center gap-2 pb-1.5">
              <input type="checkbox" name="enabled" defaultChecked={schedule?.enabled ?? true} />
              <span className="text-xs text-muted">Enabled</span>
            </label>
            <div className="flex gap-2 pb-0.5">
              <Button type="submit" name="op" value="save">Save schedule</Button>
              {schedule && (
                <Button type="submit" name="op" value="clear" variant="secondary">
                  Remove
                </Button>
              )}
            </div>
          </form>
        )}
      </section>

      {eventRules !== null && (
        <section className="rounded-lg border border-line bg-card p-5">
          <h2 className="font-display text-lg text-ink">Starts when</h2>
          {eventRules.length === 0 ? (
            <p className="mt-2 text-sm text-muted">
              No event triggers yet. Point a system&apos;s webhook at Convoy and this routine can
              start itself when something happens.
            </p>
          ) : (
            <ul className="mt-3 space-y-2">
              {eventRules.map((rule) => (
                <li key={rule.ruleId} className="flex flex-wrap items-center gap-3 text-sm">
                  <span className="font-mono text-xs text-ink">{rule.eventType}</span>
                  <span className="text-xs text-muted">
                    {rule.environmentId.endsWith("/sandbox") ? "as rehearsal" : "as live"}
                  </span>
                  {canManage && (
                    <form action={eventRuleAction}>
                      <input type="hidden" name="removeRuleId" value={rule.ruleId} />
                      <Button type="submit" variant="secondary">Remove</Button>
                    </form>
                  )}
                </li>
              ))}
            </ul>
          )}
          {canManage && runnable && connections && connections.length > 0 && (
            <form action={eventRuleAction} className="mt-4 flex flex-wrap items-end gap-3 text-sm">
              <label className="flex flex-col gap-1">
                <span className="text-xs text-muted">System</span>
                <Select
                  name="connectionId"
                  className="w-44"
                  aria-label="System the event comes from"
                  options={connections.map((connection) => ({
                    value: connection.connectionId,
                    label: connection.displayName,
                  }))}
                />
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-xs text-muted">Event</span>
                <input
                  name="eventType"
                  placeholder={suggestedEvents(connections[0]?.provider ?? "").join(", ") || "eventType"}
                  className="w-44 rounded-sm border border-line bg-field px-2 py-1.5 font-mono text-xs"
                  list="event-suggestions"
                />
                <datalist id="event-suggestions">
                  {connections.flatMap((connection) =>
                    suggestedEvents(connection.provider).map((event) => (
                      <option key={`${connection.connectionId}-${event}`} value={event} />
                    )),
                  )}
                </datalist>
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-xs text-muted">Runs as</span>
                <Select
                  name="target"
                  className="w-36"
                  aria-label="Event trigger target"
                  options={[
                    { value: "rehearsal", label: "Rehearsal" },
                    ...(canRunProduction ? [{ value: "production", label: "Live" }] : []),
                  ]}
                />
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-xs text-muted">Webhook signing secret (first time)</span>
                <input
                  name="webhookSecret"
                  type="password"
                  className="w-48 rounded-sm border border-line bg-field px-2 py-1.5"
                />
              </label>
              <div className="pb-0.5">
                <Button type="submit">Add trigger</Button>
              </div>
            </form>
          )}
        </section>
      )}

      <section className="rounded-lg border border-line bg-card p-5">
        <h2 className="font-display text-lg text-ink">Recent runs</h2>
        {recentRuns.length === 0 ? (
          <p className="mt-2 text-sm text-muted">This routine has not run yet.</p>
        ) : (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full border-separate border-spacing-0 text-sm">
              <thead>
                <tr className="text-left font-mono text-xs uppercase tracking-wide text-muted">
                  <th className="border-b border-line px-3 py-2 font-medium">Started</th>
                  <th className="border-b border-line px-3 py-2 font-medium">Status</th>
                  <th className="border-b border-line px-3 py-2 font-medium">Spent</th>
                </tr>
              </thead>
              <tbody>
                {recentRuns.map((run) => (
                  <tr key={run.id}>
                    <td className="border-b border-line-soft px-3 py-2">
                      <Link href={`/app/runs/${run.id}`} className="text-ink underline-offset-2 hover:underline">
                        {friendlyDate(run.createdAt)}
                      </Link>
                    </td>
                    <td className="border-b border-line-soft px-3 py-2">
                      <StatusChip status={run.status} rehearsal={run.rehearsal} />
                    </td>
                    <td className="border-b border-line-soft px-3 py-2 font-mono text-xs text-ink">
                      {money(run.spentUsd)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {canManage && (
        <section className="rounded-lg border border-line bg-card p-5">
          <h2 className="font-display text-lg text-ink">Remove this routine</h2>
          <p className="mt-2 text-sm text-muted">
            Removing a routine also removes its schedule and event triggers. The Agent, the
            Workspace, and past runs stay.
          </p>
          <form action={removeAction} className="mt-3">
            <Button type="submit" variant="secondary">Remove routine</Button>
          </form>
        </section>
      )}
    </div>
  );
}
