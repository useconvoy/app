/**
 * Routine detail, presentational (UI-SPEC C3): what it does, systems it
 * uses, plan template, checkpoints + approvers, triggers with the D13
 * Run now action, run history, and the W5 placeholders. Interactivity
 * rides plain forms whose actions the server page binds; this component
 * stays renderable with test doubles.
 */
import Link from "next/link";

import { Button } from "@/components/Button";
import { Chip } from "@/components/Chip";
import { EmptyState } from "@/components/EmptyState";
import { FeedbackComposer } from "@/components/FeedbackComposer";
import { FeedbackStream, type FeedbackStreamItem } from "@/components/FeedbackStream";
import { RouteStepList } from "@/components/RouteStepList";
import { StatusChip } from "@/components/StatusChip";
import { SystemRow } from "@/components/SystemRow";
import { TrendLine, type TrendPoint } from "@/components/TrendLine";
import type { SystemGrant } from "@/lib/api/environments";
import type { ChangelogEntry } from "@/lib/api/learning";
import { friendlyDate, money } from "@/lib/format";
import type { RoutineRunSummary } from "@/lib/routines/data";
import type { ApproverAssignment } from "@/lib/routines/queries";
import type { RoutineTriggers } from "@/lib/routines/triggers";
import { copy, feedbackCopy, improveCopy, triggerKindLabels } from "@/lexicon";

type FormAction = (formData: FormData) => void | Promise<void>;

export interface RoutineDetailProps {
  routine: {
    id: string;
    name: string;
    descriptor: string;
    budgetCapUsd: number;
    planSteps: string[];
  };
  workspace: { id: string; name: string } | null;
  systems: SystemGrant[];
  checkpoints: Array<{ title: string; behavior: string }>;
  approvers: ApproverAssignment[];
  triggers: RoutineTriggers;
  runs: RoutineRunSummary[];
  /** People and teams the approver picker offers. */
  assignablePeople: Array<{ id: string; name: string }>;
  assignableTeams: Array<{ id: string; name: string }>;
  canAssignApprovers: boolean;
  canEditTriggers: boolean;
  canRunNow: boolean;
  /** Where Run now goes for this viewer: rehearsal copy unless promoter. */
  runTarget: "rehearsal" | "production";
  assignAction: FormAction;
  removeAction: FormAction;
  runNowAction: FormAction;
  scheduleAction: FormAction;
  /** W5 read surfaces; optional so the component renders empty without them. */
  trend?: TrendPoint[];
  changelog?: ChangelogEntry[];
  feedback?: FeedbackStreamItem[];
  canGiveFeedback?: boolean;
  /** Submit action bound to the routine's latest run; absent when it never ran. */
  feedbackAction?: FormAction;
}

export function RoutineDetail({
  routine,
  workspace,
  systems,
  checkpoints,
  approvers,
  triggers,
  runs,
  assignablePeople,
  assignableTeams,
  canAssignApprovers,
  canEditTriggers,
  canRunNow,
  runTarget,
  assignAction,
  removeAction,
  runNowAction,
  scheduleAction,
  trend = [],
  changelog = [],
  feedback = [],
  canGiveFeedback = false,
  feedbackAction,
}: RoutineDetailProps) {
  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <header>
        <p className="text-xs text-muted">
          <Link href="/app/routines" className="underline">
            Routines
          </Link>
        </p>
        <h1 className="mt-1 font-display text-3xl text-ink">{routine.name}</h1>
        <p className="mt-2 text-sm text-muted">{routine.descriptor}</p>
        <p className="mt-3 font-mono text-xs uppercase text-muted">
          {copy.upToPerRun(money(routine.budgetCapUsd))}
          {workspace && <> · {workspace.name}</>}
        </p>
      </header>

      <Section title="Systems it uses">
        {systems.length > 0 ? (
          <ul className="m-0 list-none rounded-lg border border-line bg-card px-4 py-1">
            {systems.map((grant) => (
              <SystemRow
                key={grant.systemId}
                name={grant.displayName}
                grant={grant.scope}
                standInFor={grant.standIn ? grant.displayName : undefined}
              />
            ))}
          </ul>
        ) : (
          <EmptyState
            title="No workspace connects these systems yet"
            body="Connect the systems this routine needs in a workspace."
          />
        )}
      </Section>

      <Section title="The plan">
        <div className="rounded-lg border border-line bg-card p-5">
          <RouteStepList
            steps={routine.planSteps.map((sentence, index) => ({
              id: `${routine.id}-step-${index}`,
              sentence,
              state: "queued",
            }))}
          />
        </div>
      </Section>

      <Section title="Checkpoints and approvers">
        <div className="space-y-4 rounded-lg border border-line bg-card p-5">
          {checkpoints.length > 0 ? (
            <ul className="m-0 list-none space-y-3 p-0">
              {checkpoints.map((checkpoint) => (
                <li key={checkpoint.title}>
                  <p className="text-sm text-ink">{checkpoint.title}</p>
                  <p className="text-xs text-muted">{checkpoint.behavior}</p>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted">This routine runs without holding for anyone.</p>
          )}
          <div>
            <h3 className="text-xs font-medium uppercase tracking-wide text-muted">Approvers</h3>
            {approvers.length > 0 ? (
              <ul className="m-0 mt-2 flex list-none flex-wrap gap-2 p-0">
                {approvers.map((approver) => (
                  <li key={approver.id} className="flex items-center gap-1">
                    <Chip tone={approver.assigneeType === "team" ? "pass" : "neutral"}>
                      {approver.assigneeType === "team" ? `Team ${approver.name}` : approver.name}
                    </Chip>
                    {canAssignApprovers && (
                      <form action={removeAction}>
                        <input type="hidden" name="assignmentId" value={approver.id} />
                        <button
                          type="submit"
                          className="text-xs text-muted underline hover:text-ink"
                          aria-label={`Remove ${approver.name}`}
                        >
                          Remove
                        </button>
                      </form>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-2 text-sm text-muted">No approvers assigned yet.</p>
            )}
            {canAssignApprovers && (
              <form action={assignAction} className="mt-3 flex flex-wrap items-center gap-2">
                <label htmlFor="assignee-picker" className="text-sm text-muted">
                  Assign
                </label>
                <select
                  id="assignee-picker"
                  name="assignee"
                  className="rounded-md border border-line bg-card px-2 py-1.5 text-sm text-ink"
                >
                  <optgroup label="People">
                    {assignablePeople.map((person) => (
                      <option key={person.id} value={`user:${person.id}`}>
                        {person.name}
                      </option>
                    ))}
                  </optgroup>
                  <optgroup label="Teams">
                    {assignableTeams.map((team) => (
                      <option key={team.id} value={`team:${team.id}`}>
                        {team.name}
                      </option>
                    ))}
                  </optgroup>
                </select>
                <Button type="submit" variant="secondary">
                  Assign approver
                </Button>
              </form>
            )}
          </div>
        </div>
      </Section>

      <Section title="Triggers">
        <div className="space-y-4 rounded-lg border border-line bg-card p-5">
          <ul className="m-0 list-none space-y-3 p-0">
            {triggers.schedule && (
              <TriggerRow kind={triggerKindLabels.schedule} sentence={triggers.schedule.description} />
            )}
            {triggers.manual && (
              <TriggerRow
                kind={triggerKindLabels.manual}
                sentence="People with access can start this routine with Run now."
              />
            )}
            {triggers.event && (
              <TriggerRow kind={triggerKindLabels.event} sentence={triggers.event.description} />
            )}
          </ul>
          {canEditTriggers && (
            <form action={scheduleAction} className="flex flex-wrap items-center gap-2">
              <label htmlFor="schedule-text" className="text-sm text-muted">
                Schedule
              </label>
              <input
                id="schedule-text"
                name="schedule"
                type="text"
                defaultValue={triggers.schedule?.description ?? ""}
                placeholder="Mondays at 9am"
                className="w-64 rounded-md border border-line bg-card px-2 py-1.5 text-sm text-ink"
              />
              <Button type="submit" variant="secondary">
                Save schedule
              </Button>
            </form>
          )}
          {canRunNow && triggers.manual && (
            <div className="border-t border-line-soft pt-4">
              <form action={runNowAction}>
                <Button type="submit">{copy.runNow}</Button>
              </form>
              <p className="mt-2 text-xs text-muted">
                {runTarget === "production" ? copy.runNowProductionNote : copy.runNowRehearsalNote}
              </p>
            </div>
          )}
        </div>
        <aside className="mt-3 rounded-lg border border-dashed border-graphite bg-graphite-soft p-4 text-sm text-graphite">
          {copy.rehearsalCopyExplainer}
        </aside>
      </Section>

      <Section title="Run history">
        {runs.length > 0 ? (
          <ul className="m-0 list-none rounded-lg border border-line bg-card px-4 py-1">
            {runs.map((run) => (
              <li
                key={run.runId}
                className="flex flex-wrap items-center justify-between gap-3 border-b border-line-soft py-3 last:border-b-0"
              >
                <StatusChip status={run.status} rehearsal={run.rehearsal} />
                <span className="font-mono text-xs text-muted">
                  {money(run.spentUsd)} spent
                </span>
                <Link href={`/app/runs/${run.runId}`} className="text-sm text-ink underline">
                  View run
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState title={copy.noRunsYet} body="Runs of this routine will appear here." />
        )}
      </Section>

      <Section title="Test score trend">
        {trend.length > 0 ? (
          <div className="rounded-lg border border-line bg-card p-5">
            <TrendLine points={trend} title={routine.name} />
          </div>
        ) : (
          <EmptyState title={improveCopy.noScoresYet} body={improveCopy.noScoresBody} />
        )}
      </Section>

      <Section title="Changelog">
        {changelog.length > 0 ? (
          <ul className="m-0 list-none space-y-1.5 rounded-lg border border-line bg-card p-5">
            {changelog.map((entry) => (
              <li key={`${entry.at}:${entry.note}`} className="flex flex-wrap gap-3 text-sm">
                <span className="font-mono text-xs uppercase text-muted">{friendlyDate(entry.at)}</span>
                <span className="text-ink">{entry.note}</span>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState title={improveCopy.changelogEmpty} body={improveCopy.changelogEmptyBody} />
        )}
      </Section>

      <Section title="Feedback">
        <div className="space-y-4">
          <FeedbackStream
            items={feedback}
            emptyBody="Feedback left on this routine's runs will appear here."
          />
          {canGiveFeedback &&
            (feedbackAction ? (
              <FeedbackComposer action={feedbackAction} attachNote={feedbackCopy.attachesToLatestRun} />
            ) : (
              /* No runs yet: the composer renders disabled with a plain note. */
              <FeedbackComposer disabledNote={feedbackCopy.noRunsYetNote} />
            ))}
        </div>
      </Section>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section aria-label={title}>
      <h2 className="font-display text-lg text-ink">{title}</h2>
      <div className="mt-3">{children}</div>
    </section>
  );
}

function TriggerRow({ kind, sentence }: { kind: string; sentence: string }) {
  return (
    <li className="flex flex-wrap items-baseline gap-2">
      <Chip mono>{kind}</Chip>
      <span className="text-sm text-ink">{sentence}</span>
    </li>
  );
}
