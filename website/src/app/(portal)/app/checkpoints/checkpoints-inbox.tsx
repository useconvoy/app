"use client";

/**
 * The Checkpoints inbox: one list, three typed verbs, never merged.
 * Every card carries exactly one action; plan
 * approvals submit the version rendered on the card and a 409 flips the
 * card to its newer-version state with a link to the run, never a retry.
 * Answers and resumes render as cleared only after the run confirms via a
 * refetch; the inbox itself never flips run state.
 *
 * Keyboard triage: j/k move a roving focus between cards, A fires
 * the approve-verb of the focused card (approve plan / resume / focus the
 * answer), E opens the edit affordance (reason or answer field), R opens
 * reject on plan cards with the reason focused. Keys are ignored while
 * typing. The mapping is documented in a hint line the list points at via
 * aria-describedby.
 */
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { CheckpointCard } from "@/components/CheckpointCard";
import { EmptyState } from "@/components/EmptyState";
import { copy, dueInLabel } from "@/lexicon";
import type { CheckpointItem } from "@/lib/checkpoints/data";
import { failureSentence, type CommandResult } from "@/lib/runs/command-types";

export interface InboxCommands {
  resumeRun: (runId: string) => Promise<CommandResult>;
  approvePlan: (
    runId: string,
    planVersion: number,
    approve: boolean,
    reason?: string,
  ) => Promise<CommandResult>;
  respondToGate: (runId: string, stepId: string, response: string) => Promise<CommandResult>;
}

interface ItemState {
  pending: boolean;
  /** Accepted; the card clears on the confirming refetch. */
  sent: boolean;
  /** Plan approval 409: show the newer-version flip, never retry. */
  conflict: boolean;
  rejectOpen: boolean;
  message: string | null;
}

const IDLE: ItemState = {
  pending: false,
  sent: false,
  conflict: false,
  rejectOpen: false,
  message: null,
};

export function CheckpointsInbox({
  items,
  commands,
}: {
  items: CheckpointItem[];
  commands?: Partial<InboxCommands>;
}) {
  const router = useRouter();
  const [states, setStates] = useState<Record<string, ItemState>>({});
  const [focusIndex, setFocusIndex] = useState(0);
  // The countdown re-renders each minute so "due in 3h 12m" stays live.
  const [now, setNow] = useState(() => Date.now());
  const itemRefs = useRef<Array<HTMLLIElement | null>>([]);
  const answerRefs = useRef(new Map<string, HTMLTextAreaElement>());

  useEffect(() => {
    const tick = setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(tick);
  }, []);

  function stateOf(key: string): ItemState {
    return states[key] ?? IDLE;
  }

  function patch(key: string, partial: Partial<ItemState>) {
    setStates((current) => ({ ...current, [key]: { ...(current[key] ?? IDLE), ...partial } }));
  }

  /** Accepted: say so, then refetch so the confirmed state clears the card. */
  function accepted(key: string) {
    patch(key, { pending: false, sent: true, message: copy.actionSent });
    setTimeout(() => router.refresh(), 1500);
  }

  async function fire(
    item: CheckpointItem,
    call: (() => Promise<CommandResult>) | undefined,
  ): Promise<void> {
    if (!call || stateOf(item.key).pending || stateOf(item.key).sent) return;
    patch(item.key, { pending: true, message: null });
    const result = await call();
    if (result.kind === "accepted") {
      accepted(item.key);
    } else if (result.kind === "conflict" && item.kind === "awaiting_approval") {
      patch(item.key, { pending: false, conflict: true });
    } else {
      patch(item.key, { pending: false, message: failureSentence(result) });
    }
  }

  function approve(item: CheckpointItem) {
    if (item.planVersion === null) return;
    // Always the version rendered on this card, never a fresher guess.
    void fire(item, () => commands!.approvePlan!(item.runId, item.planVersion!, true));
  }

  function reject(item: CheckpointItem, reason: string) {
    if (item.planVersion === null || reason.trim().length === 0) return;
    void fire(item, () => commands!.approvePlan!(item.runId, item.planVersion!, false, reason.trim()));
  }

  function respond(item: CheckpointItem) {
    const field = answerRefs.current.get(item.key);
    if (!field) return;
    if (field.value.trim().length === 0) {
      field.focus();
      return;
    }
    void fire(item, () => commands!.respondToGate!(item.runId, item.stepId!, field.value.trim()));
  }

  function primaryAction(item: CheckpointItem) {
    switch (item.kind) {
      case "paused":
        void fire(item, () => commands!.resumeRun!(item.runId));
        break;
      case "awaiting_approval":
        approve(item);
        break;
      case "blocked_on_human":
        respond(item);
        break;
      case "promotion":
        router.push(`/app/promotions/${item.promotionRequestId}`);
        break;
    }
  }

  function editAction(item: CheckpointItem) {
    if (item.kind === "awaiting_approval") {
      patch(item.key, { rejectOpen: true });
    } else if (item.kind === "blocked_on_human") {
      answerRefs.current.get(item.key)?.focus();
    }
  }

  function rejectAction(item: CheckpointItem) {
    if (item.kind === "awaiting_approval") {
      patch(item.key, { rejectOpen: true });
    }
  }

  function moveFocus(offset: number) {
    const next = Math.min(Math.max(focusIndex + offset, 0), items.length - 1);
    setFocusIndex(next);
    itemRefs.current[next]?.focus();
  }

  function onKeyDown(event: React.KeyboardEvent) {
    const target = event.target as HTMLElement;
    // Typing wins: triage keys never fire from inside a field.
    if (/^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName) || target.isContentEditable) return;
    const item = items[focusIndex];
    switch (event.key.toLowerCase()) {
      case "j":
        event.preventDefault();
        moveFocus(1);
        break;
      case "k":
        event.preventDefault();
        moveFocus(-1);
        break;
      case "a":
        if (item) {
          event.preventDefault();
          primaryAction(item);
        }
        break;
      case "e":
        if (item) {
          event.preventDefault();
          editAction(item);
        }
        break;
      case "r":
        if (item) {
          event.preventDefault();
          rejectAction(item);
        }
        break;
      default:
        break;
    }
  }

  if (items.length === 0) {
    return <EmptyState title={copy.allQuiet} />;
  }

  return (
    <div onKeyDown={onKeyDown}>
      <p id="checkpoints-keys" className="mb-3 text-xs text-muted">
        {copy.triageKeysHint}
      </p>
      <ul
        aria-describedby="checkpoints-keys"
        className="m-0 flex list-none flex-col gap-3 p-0"
      >
        {items.map((item, index) => {
          const state = stateOf(item.key);
          return (
            <li
              key={item.key}
              ref={(element) => {
                itemRefs.current[index] = element;
              }}
              tabIndex={index === focusIndex ? 0 : -1}
              onFocus={() => setFocusIndex(index)}
              aria-label={item.prompt}
              className="rounded-lg outline-offset-2"
            >
              {state.conflict ? (
                <ConflictCard item={item} />
              ) : (
                <InboxCard
                  item={item}
                  state={state}
                  now={now}
                  onPrimary={() => primaryAction(item)}
                  onReject={(reason) => reject(item, reason)}
                  registerAnswerField={(element) => {
                    if (element) answerRefs.current.set(item.key, element);
                    else answerRefs.current.delete(item.key);
                  }}
                />
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** The plan moved: show the newer version's home, never auto-retry. */
function ConflictCard({ item }: { item: CheckpointItem }) {
  return (
    <div
      role="status"
      className="flex flex-wrap items-center gap-3 rounded-lg border border-hold-soft bg-hold-soft p-4 text-sm text-hold-text"
    >
      <span>{copy.planMoved}</span>
      <Link
        href={`/app/runs/${item.runId}#approve-plan`}
        className="font-medium text-ink underline underline-offset-2"
      >
        {copy.reviewNewerOnRun}
      </Link>
    </div>
  );
}

function InboxCard({
  item,
  state,
  now,
  onPrimary,
  onReject,
  registerAnswerField,
}: {
  item: CheckpointItem;
  state: ItemState;
  now: number;
  onPrimary: () => void;
  onReject: (reason: string) => void;
  registerAnswerField: (element: HTMLTextAreaElement | null) => void;
}) {
  const [reason, setReason] = useState("");
  const minutesLeft = item.deadline
    ? Math.floor((new Date(item.deadline).getTime() - now) / 60_000)
    : null;
  const countdown = minutesLeft !== null && minutesLeft < 24 * 60;

  return (
    <CheckpointCard
      kind={item.kind}
      prompt={item.prompt}
      runContext={item.teamHeld ? `${item.runContext} · ${copy.heldForYourTeam}` : item.runContext}
      heldMinutes={item.heldMinutes}
      deadline={item.deadline ?? undefined}
      onTimeout={item.onTimeout ?? undefined}
      planVersion={item.planVersion ?? undefined}
      assignees={item.assignees}
      rehearsal={item.rehearsal}
      onAction={onPrimary}
    >
      {countdown && (
        <p className="font-mono text-xs uppercase tracking-wide text-hold-text">
          {dueInLabel(minutesLeft)}
        </p>
      )}
      {item.kind === "blocked_on_human" && (
        <label className="mt-2 block text-sm text-muted">
          {copy.responseLabel}
          <textarea
            ref={registerAnswerField}
            rows={2}
            className="mt-1 w-full rounded-md border border-line bg-card px-3 py-2 text-sm text-ink"
          />
        </label>
      )}
      {item.kind === "awaiting_approval" && state.rejectOpen && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <label className="flex flex-wrap items-center gap-2 text-sm text-muted">
            {copy.rejectReasonLabel}
            <input
              autoFocus
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              className="rounded-md border border-line bg-card px-2 py-1 text-sm text-ink"
            />
          </label>
          <button
            type="button"
            disabled={reason.trim().length === 0}
            onClick={() => onReject(reason)}
            className="rounded-md border border-fail bg-card px-2.5 py-1 text-sm font-medium text-fail disabled:cursor-not-allowed disabled:opacity-50"
          >
            {copy.sendBackAction}
          </button>
        </div>
      )}
      {item.kind === "promotion" && (
        <p className="text-sm text-muted">
          <Link
            href={`/app/promotions/${item.promotionRequestId}`}
            className="text-ink underline underline-offset-2"
          >
            {copy.promotionReviewTitle}
          </Link>
        </p>
      )}
      {state.sent && (
        <p role="status" className="text-sm text-pass">
          {copy.actionSent}
        </p>
      )}
      {state.message && !state.sent && (
        <p role="status" className="text-sm text-hold-text">
          {state.message}
        </p>
      )}
    </CheckpointCard>
  );
}
