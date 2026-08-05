"use client";

/**
 * The human controls on a run (W2, UI-SPEC C2): pause/resume/land, the
 * steer composer, plan approval, gate responses, and the rehearsal clock.
 *
 * Discipline (CLAUDE.md rule 7): every button fires a server action that
 * returns 202-shaped results; nothing here flips run state locally. The
 * steer composer is the single optimistic surface: it renders "Guidance
 * sent" immediately and the steer_received event confirms it. Approvals
 * and answers render only from confirmed events arriving over SSE. A 409
 * shows the newer state in a plain sentence, never an auto-retry.
 *
 * The action handlers arrive as props from the server page so the page,
 * not the component, owns which session-bound actions are wired in.
 */
import { useRouter } from "next/navigation";
import { useId, useState } from "react";

import { Button } from "@/components/Button";
import { PlanVersionPanel } from "@/components/PlanVersionPanel";
import {
  checkpointKinds,
  copy,
  timeoutBehaviorLabels,
  type TimeoutBehavior,
} from "@/lexicon";
import type {
  CommandResult,
  RunCommandHandlers,
  SteerMode,
} from "@/lib/runs/command-types";
import type { RunStreamEvent } from "@/lib/runs/status";

const TERMINAL_STATUSES = new Set(["completed", "landed", "failed", "budget_exhausted"]);

export interface BlockedStep {
  stepId: string;
  prompt: string;
  /** What happens if nobody answers in time, from the gate_opened event. */
  onTimeout?: TimeoutBehavior | null;
}

export interface RunControlsProps {
  runId: string;
  status: string;
  rehearsal: boolean;
  /** Latest virtual timestamp; null when the run is not on a virtual clock. */
  virtualNow: string | null;
  /** Steps currently holding at a checkpoint, from confirmed events. */
  blockedSteps: readonly BlockedStep[];
  /** The rendered plan version; approval always submits exactly this. */
  planVersion: number | null;
  /** The plan's steps as plain sentences, for the approval panel. */
  planSteps: readonly string[];
  events: readonly RunStreamEvent[];
  commands?: Partial<RunCommandHandlers>;
}

function localInputValue(iso: string): string {
  const date = new Date(iso);
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}`;
}

function toIso(localValue: string): string | null {
  const date = new Date(localValue);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

function plusDays(iso: string, days: number): string {
  return new Date(new Date(iso).getTime() + days * 86_400_000).toISOString();
}

/** A conflict or refusal, as one plain sentence for a status line. */
function failureSentence(result: CommandResult): string | null {
  if (result.kind === "conflict") return result.detail;
  if (result.kind === "refused") return result.message;
  if (result.kind === "notFound") return "That run cannot be found.";
  return null;
}

export function RunControls({
  runId,
  status,
  rehearsal,
  virtualNow,
  blockedSteps,
  planVersion,
  planSteps,
  events,
  commands,
}: RunControlsProps) {
  const terminal = TERMINAL_STATUSES.has(status);
  if (terminal) return null;

  return (
    <section
      aria-label="Controls"
      data-slot="run-controls"
      className={[
        "flex flex-col gap-5 rounded-lg border bg-card p-5",
        rehearsal ? "border-dashed border-graphite" : "border-line",
      ].join(" ")}
    >
      <PauseResumeLand runId={runId} status={status} commands={commands} />
      {status === "awaiting_approval" && planVersion !== null && (
        <PlanApproval
          runId={runId}
          planVersion={planVersion}
          planSteps={planSteps}
          commands={commands}
        />
      )}
      {blockedSteps.map((step) => (
        <GateRespondForm
          key={step.stepId}
          runId={runId}
          step={step}
          rehearsal={rehearsal}
          virtualNow={virtualNow}
          commands={commands}
        />
      ))}
      <SteerComposer runId={runId} events={events} commands={commands} />
      {rehearsal && virtualNow !== null && (
        <AdvanceClock runId={runId} virtualNow={virtualNow} commands={commands} />
      )}
    </section>
  );
}

/** Pause/Resume (state-appropriate) and Land. Anchored for deep links. */
function PauseResumeLand({
  runId,
  status,
  commands,
}: {
  runId: string;
  status: string;
  commands?: Partial<RunCommandHandlers>;
}) {
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function fire(action: ((runId: string) => Promise<CommandResult>) | undefined) {
    if (!action || pending) return;
    setPending(true);
    setMessage(null);
    const result = await action(runId);
    setMessage(failureSentence(result));
    setPending(false);
  }

  return (
    <div id="resume" className="flex flex-wrap items-center gap-2">
      {status === "paused" ? (
        <Button disabled={pending} onClick={() => void fire(commands?.resumeRun)}>
          {checkpointKinds.paused.verb}
        </Button>
      ) : (
        <Button
          variant="secondary"
          disabled={pending}
          onClick={() => void fire(commands?.pauseRun)}
        >
          {copy.pauseAction}
        </Button>
      )}
      <Button variant="secondary" disabled={pending} onClick={() => void fire(commands?.landRun)}>
        {copy.landAction}
      </Button>
      {message && (
        <p role="status" className="w-full text-sm text-hold">
          {message}
        </p>
      )}
    </div>
  );
}

/**
 * Plan approval: submits exactly the rendered version. On conflict the
 * panel flips to its stale-version state and the page refetches so the
 * newer version renders; there is no retry path.
 */
function PlanApproval({
  runId,
  planVersion,
  planSteps,
  commands,
}: {
  runId: string;
  planVersion: number;
  planSteps: readonly string[];
  commands?: Partial<RunCommandHandlers>;
}) {
  const router = useRouter();
  const [newerVersion, setNewerVersion] = useState<number | undefined>(undefined);
  const [message, setMessage] = useState<string | null>(null);
  const [seenVersion, setSeenVersion] = useState(planVersion);

  // A newly arrived plan version supersedes the stale notice (state
  // adjusted during render, per the React docs, not in an effect).
  if (seenVersion !== planVersion) {
    setSeenVersion(planVersion);
    setNewerVersion(undefined);
    setMessage(null);
  }

  async function decide(version: number, approve: boolean, reason?: string) {
    if (!commands?.approvePlan) return;
    setMessage(null);
    const result = await commands.approvePlan(runId, version, approve, reason);
    if (result.kind === "conflict") {
      setNewerVersion(version + 1);
      router.refresh();
      return;
    }
    setMessage(failureSentence(result));
  }

  return (
    <div id="approve-plan">
      <PlanVersionPanel
        versions={[{ version: planVersion, steps: [...planSteps] }]}
        newerVersion={newerVersion}
        onApprove={(version) => void decide(version, true)}
        onReject={(version, reason) => void decide(version, false, reason)}
        onReviewNewer={() => router.refresh()}
      />
      {message && (
        <p role="status" className="mt-2 text-sm text-hold">
          {message}
        </p>
      )}
    </div>
  );
}

/**
 * Inline answer for one held step. Answers render only once the
 * gate_answered event lands; this form just sends and reports refusals.
 * Rehearsal runs may schedule the answer at a rehearsal moment
 * (at_virtual), which the runtime delivers when virtual time reaches it.
 */
function GateRespondForm({
  runId,
  step,
  rehearsal,
  virtualNow,
  commands,
}: {
  runId: string;
  step: BlockedStep;
  rehearsal: boolean;
  virtualNow: string | null;
  commands?: Partial<RunCommandHandlers>;
}) {
  const baseId = useId();
  const [response, setResponse] = useState("");
  const [scheduled, setScheduled] = useState(false);
  const [atValue, setAtValue] = useState(() => (virtualNow ? localInputValue(virtualNow) : ""));
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function send() {
    if (!commands?.respondToGate || pending || response.trim().length === 0) return;
    setPending(true);
    setMessage(null);
    const atVirtual = scheduled && atValue ? (toIso(atValue) ?? undefined) : undefined;
    const result = await commands.respondToGate(runId, step.stepId, response.trim(), atVirtual);
    setMessage(failureSentence(result));
    setPending(false);
  }

  return (
    <div
      id={`respond-${step.stepId}`}
      className="rounded-md border border-hold-soft bg-card p-4"
    >
      <p className="text-sm font-medium text-ink">{step.prompt}</p>
      {step.onTimeout && (
        <p className="mt-1 text-sm text-muted">{timeoutBehaviorLabels[step.onTimeout]}</p>
      )}
      <label htmlFor={`${baseId}-response`} className="mt-3 block text-sm text-muted">
        {copy.responseLabel}
      </label>
      <textarea
        id={`${baseId}-response`}
        value={response}
        onChange={(event) => setResponse(event.target.value)}
        rows={3}
        className="mt-1 w-full rounded-md border border-line bg-card px-3 py-2 text-sm text-ink"
      />
      {rehearsal && (
        <div className="mt-2">
          <label className="flex items-center gap-2 text-sm text-muted">
            <input
              type="checkbox"
              checked={scheduled}
              onChange={(event) => {
                setScheduled(event.target.checked);
                // The form can mount before the stream has delivered the
                // virtual clock; seed the moment when the toggle opens.
                if (event.target.checked && atValue === "" && virtualNow) {
                  setAtValue(localInputValue(virtualNow));
                }
              }}
            />
            {copy.answerAtVirtual}
          </label>
          {scheduled && (
            <label className="mt-2 flex flex-wrap items-center gap-2 text-sm text-muted">
              {copy.answerAtVirtualLabel}
              <input
                type="datetime-local"
                value={atValue}
                onChange={(event) => setAtValue(event.target.value)}
                className="rounded-md border border-line bg-card px-2 py-1 font-mono text-xs text-ink"
              />
            </label>
          )}
        </div>
      )}
      <div className="mt-3 flex items-center gap-3">
        <Button disabled={pending || response.trim().length === 0} onClick={() => void send()}>
          {checkpointKinds.blocked_on_human.verb}
        </Button>
      </div>
      {message && (
        <p role="status" className="mt-2 text-sm text-hold">
          {message}
        </p>
      )}
    </div>
  );
}

interface SentSteer {
  localId: number;
  mode: SteerMode;
  body: string;
  steerId: string | null;
  failed: string | null;
}

/**
 * The steer composer, the ONE optimistic surface: "Guidance sent" renders
 * the moment the send starts; the steer_received event (matched by steer
 * id) is the confirmation that it reached the run.
 */
function SteerComposer({
  runId,
  events,
  commands,
}: {
  runId: string;
  events: readonly RunStreamEvent[];
  commands?: Partial<RunCommandHandlers>;
}) {
  const baseId = useId();
  const [mode, setMode] = useState<SteerMode>("note");
  const [body, setBody] = useState("");
  const [sent, setSent] = useState<SentSteer[]>([]);

  const confirmedSteerIds = new Set(
    events
      .filter((event) => event.type === "steer_received")
      .map((event) => event.payload["steer_id"])
      .filter((id): id is string => typeof id === "string"),
  );

  function send() {
    const trimmed = body.trim();
    if (!commands?.steerRun || trimmed.length === 0) return;
    const localId = Date.now() + Math.random();
    // Optimistic: the entry renders "Guidance sent" before the wire answers.
    setSent((current) => [
      ...current,
      { localId, mode, body: trimmed, steerId: null, failed: null },
    ]);
    setBody("");
    void commands.steerRun(runId, mode, trimmed).then((result) => {
      setSent((current) =>
        current.map((entry) =>
          entry.localId === localId
            ? result.kind === "accepted"
              ? { ...entry, steerId: result.steerId }
              : { ...entry, failed: failureSentence(result) ?? copy.platformRefused }
            : entry,
        ),
      );
    });
  }

  return (
    <div>
      <fieldset>
        <legend className="text-sm font-medium text-ink">{copy.steerBodyLabel}</legend>
        <p className="mt-1 text-sm text-muted">{copy.steerExplainer}</p>
        <div className="mt-2 flex gap-4" role="radiogroup" aria-label={copy.steerBodyLabel}>
          <label className="flex items-center gap-1.5 text-sm text-ink">
            <input
              type="radio"
              name={`${baseId}-mode`}
              checked={mode === "note"}
              onChange={() => setMode("note")}
            />
            {copy.steerNote}
          </label>
          <label className="flex items-center gap-1.5 text-sm text-ink">
            <input
              type="radio"
              name={`${baseId}-mode`}
              checked={mode === "redirect"}
              onChange={() => setMode("redirect")}
            />
            {copy.steerRedirect}
          </label>
        </div>
      </fieldset>
      <textarea
        aria-label={copy.steerBodyLabel}
        value={body}
        onChange={(event) => setBody(event.target.value)}
        rows={2}
        className="mt-2 w-full rounded-md border border-line bg-card px-3 py-2 text-sm text-ink"
      />
      <div className="mt-2">
        <Button disabled={body.trim().length === 0} onClick={send}>
          {copy.sendGuidance}
        </Button>
      </div>
      {sent.length > 0 && (
        <ul className="mt-3 m-0 list-none space-y-1 p-0">
          {sent.map((entry) => (
            <li key={entry.localId} className="flex flex-wrap items-baseline gap-2 text-sm">
              {entry.failed ? (
                <span role="status" className="text-hold">
                  {entry.failed}
                </span>
              ) : (
                <span role="status" className="text-pass">
                  {copy.guidanceSent}
                  {entry.steerId && confirmedSteerIds.has(entry.steerId) && (
                    <span className="ml-2 font-mono text-xs uppercase tracking-wide text-muted">
                      received
                    </span>
                  )}
                </span>
              )}
              <span className="min-w-0 flex-1 truncate text-muted">{entry.body}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * The rehearsal clock (C2): move virtual time forward. Seeded from the
 * run's current virtual moment; the quick buttons jump straight ahead.
 * The runtime's 409 (real clock, backwards move) surfaces as a sentence.
 */
function AdvanceClock({
  runId,
  virtualNow,
  commands,
}: {
  runId: string;
  virtualNow: string;
  commands?: Partial<RunCommandHandlers>;
}) {
  const baseId = useId();
  const [value, setValue] = useState(() => localInputValue(virtualNow));
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function advance(iso: string | null) {
    if (!commands?.advanceClock || pending || !iso) return;
    setPending(true);
    setMessage(null);
    const result = await commands.advanceClock(runId, iso);
    setMessage(failureSentence(result));
    setPending(false);
  }

  return (
    <div className="border-t border-line-soft pt-4">
      <label htmlFor={`${baseId}-to`} className="block text-sm font-medium text-ink">
        {copy.advanceClock}
      </label>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <span className="text-sm text-muted">{copy.advanceClockLabel}</span>
        <input
          id={`${baseId}-to`}
          type="datetime-local"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          className="rounded-md border border-line bg-card px-2 py-1 font-mono text-xs text-ink"
        />
        <Button
          variant="secondary"
          disabled={pending}
          onClick={() => void advance(toIso(value))}
        >
          {copy.advanceClock}
        </Button>
        <Button
          variant="secondary"
          disabled={pending}
          onClick={() => void advance(plusDays(virtualNow, 1))}
        >
          {copy.plusOneDay}
        </Button>
        <Button
          variant="secondary"
          disabled={pending}
          onClick={() => void advance(plusDays(virtualNow, 7))}
        >
          {copy.plusOneWeek}
        </Button>
      </div>
      {message && (
        <p role="status" className="mt-2 text-sm text-hold">
          {message}
        </p>
      )}
    </div>
  );
}
