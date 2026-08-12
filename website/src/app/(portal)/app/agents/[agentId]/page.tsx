import type { Metadata } from "next";
import Link from "next/link";
import { notFound, redirect } from "next/navigation";

import { Button } from "@/components/Button";
import { RouteStepList } from "@/components/RouteStepList";
import { environmentsClient } from "@/lib/api/environments";
import { runAgentNow } from "@/lib/agents/run-actions";
import { getAgentSchedule } from "@/lib/agents/queries";
import { clearAgentSchedule, setAgentSchedule } from "@/lib/agents/schedule-actions";
import { addAgentEventRule, removeAgentEventRule } from "@/lib/agents/event-actions";
import { suggestedEvents } from "@/lib/agents/events";
import { listEventRules, listSystemConnections } from "@/lib/api/environments";
import {
  SCHEDULE_PRESETS,
  parseStoredSchedule,
  presetForCron,
  scheduleDisplay,
} from "@/lib/agents/schedule";
import { friendlyDate, money } from "@/lib/format";
import { can } from "@/lib/permissions";
import { requireWorkspacesPage } from "@/lib/workspaces/gate";

export const metadata: Metadata = { title: "Agent" };
export const dynamic = "force-dynamic";

export default async function AgentDetailPage({
  params,
}: {
  params: Promise<{ agentId: string }>;
}) {
  const { agentId } = await params;
  const { session, membership } = await requireWorkspacesPage();
  const client = environmentsClient();
  const agent = await client.getAgent(session.orgId, agentId);
  if (!agent) notFound();
  const workspace = await client.getWorkspace(session.orgId, agent.workspaceId);
  const canStart = can("trigger_production_run", membership.role, membership.capabilities);
  const canRunProduction = can("promote", membership.role, membership.capabilities);
  const canManage = can("manage_workspaces", membership.role, membership.capabilities);
  const schedule = parseStoredSchedule(await getAgentSchedule(session.orgId, agentId));
  const eventRules = (await listEventRules(session.orgId, agentId).catch(() => null)) ?? null;
  const connections = eventRules === null
    ? null
    : await listSystemConnections(session.orgId).catch(() => null);

  async function startAction(formData: FormData) {
    "use server";
    const target = formData.get("target") === "production" ? "production" : "rehearsal";
    const { runId } = await runAgentNow(agentId, target);
    redirect(`/app/runs/${runId}`);
  }

  async function eventRuleAction(formData: FormData) {
    "use server";
    const removeId = formData.get("removeRuleId");
    if (typeof removeId === "string" && removeId) {
      await removeAgentEventRule(agentId, removeId);
      return;
    }
    await addAgentEventRule(agentId, {
      connectionId: String(formData.get("connectionId") ?? ""),
      eventType: String(formData.get("eventType") ?? ""),
      target: formData.get("target") === "production" ? "production" : "rehearsal",
      webhookSecret: String(formData.get("webhookSecret") ?? ""),
    });
  }

  async function scheduleAction(formData: FormData) {
    "use server";
    if (formData.get("op") === "clear") {
      await clearAgentSchedule(agentId);
      return;
    }
    await setAgentSchedule(agentId, {
      preset: String(formData.get("preset") ?? "weekday_morning"),
      cron: String(formData.get("cron") ?? ""),
      timezone: String(formData.get("timezone") ?? "UTC"),
      target: formData.get("target") === "production" ? "production" : "rehearsal",
      enabled: formData.get("enabled") === "on",
    });
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header>
        <p className="text-xs text-muted">
          <Link href="/app/agents" className="underline">Agents</Link>
        </p>
        <h1 className="mt-1 font-display text-3xl text-ink">{agent.name}</h1>
        <p className="mt-2 text-sm text-muted">{agent.purpose}</p>
        <p className="mt-2 font-mono text-xs text-muted">
          runtime v{agent.version} · created {friendlyDate(agent.createdAt)}
        </p>
      </header>

      <section className="rounded-lg border border-line bg-card p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="max-w-3xl">
            <h2 className="font-display text-lg text-ink">Goal</h2>
            <p className="mt-2 text-sm text-ink">
              {agent.automationConfigured ? agent.goal : "This Agent still needs a goal."}
            </p>
            <p className="mt-3 text-xs text-muted">
              {agent.scheduleDescription || "Starts on demand"} · {money(agent.budgetCapUsd)} per run
            </p>
          </div>
          {canStart && agent.automationConfigured && workspace && (
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
        {agent.planSteps.length > 0 && (
          <div className="mt-5 border-t border-line-soft pt-4">
            <h3 className="text-xs font-medium uppercase tracking-wide text-muted">Instructions</h3>
            <div className="mt-3">
              <RouteStepList
                steps={agent.planSteps.map((sentence, index) => ({
                  id: `${agent.id}-step-${index}`,
                  sentence,
                  state: "queued",
                }))}
              />
            </div>
          </div>
        )}
      </section>

      <section className="rounded-lg border border-line bg-card p-5">
        <h2 className="font-display text-lg text-ink">Schedule</h2>
        <p className="mt-2 text-sm text-muted">
          {schedule
            ? `Starts: ${scheduleDisplay(schedule)}`
            : "This Agent starts on demand only."}
        </p>
        {canManage && agent.automationConfigured && (
          <form action={scheduleAction} className="mt-4 flex flex-wrap items-end gap-3 text-sm">
            <label className="flex flex-col gap-1">
              <span className="text-xs text-muted">When</span>
              <select
                name="preset"
                defaultValue={schedule ? presetForCron(schedule.cron) : "weekday_morning"}
                className="rounded-sm border border-line bg-field px-2 py-1.5"
              >
                {Object.entries(SCHEDULE_PRESETS).map(([key, entry]) => (
                  <option key={key} value={key}>{entry.label}</option>
                ))}
                <option value="custom">Custom cron</option>
              </select>
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
              <select
                name="target"
                defaultValue={schedule?.target ?? "rehearsal"}
                className="rounded-sm border border-line bg-field px-2 py-1.5"
              >
                <option value="rehearsal">Rehearsal</option>
                {canRunProduction && <option value="production">Live</option>}
              </select>
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
              No event triggers yet. Point a system&apos;s webhook at Convoy and this Agent can
              start itself when something happens.
            </p>
          ) : (
            <ul className="mt-3 space-y-2">
              {eventRules.map((rule) => (
                <li key={rule.ruleId} className="flex flex-wrap items-center gap-3 text-sm">
                  <span className="font-mono text-xs text-ink">{rule.eventType}</span>
                  <span className="text-xs text-muted">
                    → {rule.environmentId.endsWith("/sandbox") ? "rehearsal" : "live"}
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
          {canManage && agent.automationConfigured && connections && connections.length > 0 && (
            <form action={eventRuleAction} className="mt-4 flex flex-wrap items-end gap-3 text-sm">
              <label className="flex flex-col gap-1">
                <span className="text-xs text-muted">System</span>
                <select name="connectionId" className="rounded-sm border border-line bg-field px-2 py-1.5">
                  {connections.map((connection) => (
                    <option key={connection.connectionId} value={connection.connectionId}>
                      {connection.displayName}
                    </option>
                  ))}
                </select>
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
                <select name="target" className="rounded-sm border border-line bg-field px-2 py-1.5">
                  <option value="rehearsal">Rehearsal</option>
                  {canRunProduction && <option value="production">Live</option>}
                </select>
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

      <div className="grid gap-5 md:grid-cols-2">
        <section className="rounded-lg border border-line bg-card p-5">
          <h2 className="font-display text-lg text-ink">Workspace and systems</h2>
          <p className="mt-2 text-sm text-muted">
            This Agent uses the systems and permissions configured in a shared Workspace.
          </p>
          {workspace && (
            <>
              <Link href={`/app/workspaces/${workspace.id}`} className="mt-3 inline-block text-sm text-ink underline">
                {workspace.name}
              </Link>
              <p className="mt-2 text-xs text-muted">
                {workspace.systems.map((system) => system.displayName).join(", ") || "No systems connected"}
              </p>
            </>
          )}
        </section>
        <section className="rounded-lg border border-line bg-card p-5">
          <h2 className="font-display text-lg text-ink">Compute and durable state</h2>
          <dl className="mt-3 space-y-3 text-sm">
            <div>
              <dt className="text-muted">Template</dt>
              <dd className="mt-1 font-mono text-xs text-ink">{agent.sandboxTemplate}</dd>
            </div>
            <div>
              <dt className="text-muted">Files and memory</dt>
              <dd className="mt-1 text-ink">Checkpointed on pause and restored onto fresh compute</dd>
            </div>
          </dl>
        </section>
        <section className="rounded-lg border border-line bg-card p-5">
          <h2 className="font-display text-lg text-ink">Browser</h2>
          {agent.browserPolicy ? (
            <dl className="mt-3 space-y-3 text-sm">
              <div>
                <dt className="text-muted">Allowed domains</dt>
                <dd className="mt-1 text-ink">
                  {agent.browserPolicy.allowedDomains.length > 0
                    ? agent.browserPolicy.allowedDomains.join(", ")
                    : "No domains allowed yet"}
                </dd>
              </div>
              <div>
                <dt className="text-muted">Sign-in state</dt>
                <dd className="mt-1 text-ink">
                  {agent.browserPolicy.persistProfile ? "Restored after pauses" : "Discarded with compute"}
                </dd>
              </div>
            </dl>
          ) : (
            <p className="mt-2 text-sm text-muted">This agent does not have browser access.</p>
          )}
        </section>
        <section className="rounded-lg border border-line bg-card p-5">
          <h2 className="font-display text-lg text-ink">Run behavior</h2>
          <p className="mt-2 text-sm text-muted">
            Rehearsals use the Workspace&apos;s safe data plane; live runs use its real connected systems. Compute and binding details stay internal.
          </p>
        </section>
      </div>
    </div>
  );
}
