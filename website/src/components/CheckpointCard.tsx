import type { ReactNode } from "react";

import {
  checkpointKinds,
  copy,
  heldAgeLabel,
  terms,
  timeoutBehaviorLabels,
  type CheckpointKind,
  type TimeoutBehavior,
} from "@/lexicon";
import { dueDateLabel } from "@/lib/format";

import { Button } from "./Button";
import { Chip } from "./Chip";
import { KeyHint } from "./KeyHint";

export interface CheckpointAssignee {
  name: string;
  kind: "person" | "team";
}

export interface CheckpointCardProps {
  /**
   * One of the three stuck states; picks the one typed verb (paused ->
   * resume, awaiting approval -> approve plan, blocked on human ->
   * respond). Never a merged or generic action.
   */
  kind: CheckpointKind;
  /** What is being asked, as a plain sentence. */
  prompt: string;
  /** Which run this belongs to, in plain language. */
  runContext: string;
  heldMinutes: number;
  deadline?: string | Date;
  onTimeout?: TimeoutBehavior;
  /** Approve-plan cards always show the version the action will submit. */
  planVersion?: number;
  assignees?: CheckpointAssignee[];
  /** Set once answered; replaces the action with responder attribution. */
  answeredBy?: string;
  rehearsal?: boolean;
  onAction?: () => void;
  /**
   * Inline detail below the card body: deadline countdown, the respond
   * field, or a reject-reason affordance. Renders above the
   * action row so the one typed verb stays the card's last word.
   */
  children?: ReactNode;
}

export function CheckpointCard({
  kind,
  prompt,
  runContext,
  heldMinutes,
  deadline,
  onTimeout,
  planVersion,
  assignees = [],
  answeredBy,
  rehearsal = false,
  onAction,
  children,
}: CheckpointCardProps) {
  const checkpoint = checkpointKinds[kind];
  return (
    <article
      aria-label={prompt}
      className={[
        "rounded-lg border bg-card p-4",
        rehearsal ? "border-dashed border-graphite" : "border-line",
      ].join(" ")}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Chip tone={rehearsal ? "graphite" : "hold"} dashed={rehearsal}>
          {checkpoint.label}
        </Chip>
        {rehearsal && (
          <Chip tone="graphite" dashed mono>
            {terms.sandbox}
          </Chip>
        )}
        <span className="font-mono text-xs uppercase tracking-wide text-muted">
          {heldAgeLabel(heldMinutes)}
        </span>
        {deadline && (
          <span className="font-mono text-xs uppercase tracking-wide text-hold-text">
            {dueDateLabel(deadline)}
          </span>
        )}
      </div>

      <h3 className="mt-3 text-base font-medium text-ink">{prompt}</h3>
      <p className="mt-1 text-sm text-muted">{runContext}</p>
      {onTimeout && <p className="mt-1 text-sm text-muted">{timeoutBehaviorLabels[onTimeout]}</p>}

      {assignees.length > 0 && (
        <ul className="mt-3 flex list-none flex-wrap gap-1.5 p-0" aria-label="Assigned to">
          {assignees.map((assignee) => (
            <li key={`${assignee.kind}-${assignee.name}`}>
              <Chip>{assignee.kind === "team" ? `${assignee.name} · team` : assignee.name}</Chip>
            </li>
          ))}
        </ul>
      )}

      {children && <div className="mt-3">{children}</div>}

      {answeredBy ? (
        <p className="mt-4 text-sm text-muted">{copy.answeredBy(answeredBy)}</p>
      ) : (
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <Button onClick={onAction}>{checkpoint.verb}</Button>
          {kind === "awaiting_approval" && planVersion !== undefined && (
            <span className="font-mono text-xs text-muted">{copy.actsOnVersion(planVersion)}</span>
          )}
          <KeyHint
            hints={[
              { keyName: "A", label: "approve" },
              { keyName: "E", label: "edit" },
              { keyName: "R", label: "reject" },
            ]}
          />
        </div>
      )}
    </article>
  );
}
