"use client";

import { useId, useState } from "react";

import { checkpointKinds, copy } from "@/lexicon";
import { friendlyDateTime } from "@/lib/format";

import { Button } from "./Button";

export interface PlanVersion {
  version: number;
  /** Steps as plain sentences. */
  steps: string[];
  approvedAt?: string | null;
}

export interface PlanVersionPanelProps {
  /** All known versions, any order; tabs render ascending. */
  versions: PlanVersion[];
  /**
   * Set after a 409: the plan moved while this one was on screen. Renders
   * the newer-version notice with a review CTA and removes the approve
   * affordance entirely; the caller never auto-retries.
   */
  newerVersion?: number;
  /** Always called with the version that was rendered, never a guess. */
  onApprove?: (version: number) => void;
  onReject?: (version: number, reason: string) => void;
  onReviewNewer?: (version: number) => void;
}

type DiffLine = { kind: "added" | "removed" | "same"; text: string };

/**
 * Order-preserving diff against the previous version: current steps render
 * in order (new ones marked +), then steps that disappeared marked -.
 */
function diffSteps(current: string[], previous: string[] | undefined): DiffLine[] {
  if (!previous) return current.map((text) => ({ kind: "same", text }));
  const lines: DiffLine[] = current.map((text) => ({
    kind: previous.includes(text) ? "same" : "added",
    text,
  }));
  for (const text of previous) {
    if (!current.includes(text)) lines.push({ kind: "removed", text });
  }
  return lines;
}

export function PlanVersionPanel({
  versions,
  newerVersion,
  onApprove,
  onReject,
  onReviewNewer,
}: PlanVersionPanelProps) {
  const ordered = [...versions].sort((a, b) => a.version - b.version);
  const latest = ordered[ordered.length - 1];
  const [selectedVersion, setSelectedVersion] = useState(latest?.version);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const baseId = useId();

  const selectedIndex = Math.max(
    ordered.findIndex((v) => v.version === selectedVersion),
    0,
  );
  const selected = ordered[selectedIndex];
  if (!selected) return null;

  const previous = selectedIndex > 0 ? ordered[selectedIndex - 1] : undefined;
  const lines = diffSteps(selected.steps, previous?.steps);
  const stale = newerVersion !== undefined;

  function moveSelection(offset: number) {
    const next = ordered[selectedIndex + offset];
    if (next) setSelectedVersion(next.version);
  }

  return (
    <section className="rounded-lg border border-line bg-card">
      <div
        role="tablist"
        aria-label="Plan versions"
        className="flex gap-1 border-b border-line-soft p-2"
        onKeyDown={(event) => {
          if (event.key === "ArrowLeft") moveSelection(-1);
          if (event.key === "ArrowRight") moveSelection(1);
        }}
      >
        {ordered.map((v) => {
          const isSelected = v.version === selected.version;
          return (
            <button
              key={v.version}
              type="button"
              role="tab"
              id={`${baseId}-tab-${v.version}`}
              aria-selected={isSelected}
              aria-controls={`${baseId}-panel`}
              tabIndex={isSelected ? 0 : -1}
              onClick={() => setSelectedVersion(v.version)}
              className={[
                "rounded px-2 py-1 font-mono text-xs uppercase tracking-wide",
                isSelected ? "bg-pine text-card" : "text-muted hover:bg-field",
              ].join(" ")}
            >
              v{v.version}
            </button>
          );
        })}
      </div>

      <div
        role="tabpanel"
        id={`${baseId}-panel`}
        aria-labelledby={`${baseId}-tab-${selected.version}`}
        className="p-4"
      >
        <ol className="m-0 list-none space-y-1 p-0">
          {lines.map((line, index) => (
            <li key={`${line.kind}-${index}`} className="flex gap-2">
              {line.kind === "same" ? (
                <span className="text-ink">{line.text}</span>
              ) : (
                <span
                  className={[
                    "font-mono text-sm",
                    line.kind === "added" ? "text-pass" : "text-fail line-through",
                  ].join(" ")}
                >
                  {line.kind === "added" ? "+ " : "- "}
                  {line.text}
                </span>
              )}
            </li>
          ))}
        </ol>

        {selected.approvedAt && (
          <p className="mt-3 font-mono text-xs uppercase tracking-wide text-pass">
            APPROVED · {friendlyDateTime(selected.approvedAt)}
          </p>
        )}

        {stale && (
          <div
            role="status"
            className="mt-4 flex flex-wrap items-center gap-3 rounded-md border border-hold-soft bg-hold-soft p-3 text-sm text-hold"
          >
            <span>{copy.planMoved}</span>
            <Button variant="secondary" onClick={() => onReviewNewer?.(newerVersion)}>
              {copy.reviewNewerPlan(newerVersion)}
            </Button>
          </div>
        )}

        {!stale && !selected.approvedAt && (
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <Button onClick={() => onApprove?.(selected.version)}>
              {checkpointKinds.awaiting_approval.verb}
            </Button>
            {!rejecting ? (
              <Button variant="secondary" onClick={() => setRejecting(true)}>
                {copy.sendBackAction}
              </Button>
            ) : (
              <span className="flex flex-wrap items-center gap-2">
                <label htmlFor={`${baseId}-reason`} className="text-sm text-muted">
                  What should change
                </label>
                <input
                  id={`${baseId}-reason`}
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  className="rounded-md border border-line bg-card px-2 py-1 text-sm text-ink"
                />
                <Button
                  variant="danger"
                  disabled={reason.trim().length === 0}
                  onClick={() => onReject?.(selected.version, reason.trim())}
                >
                  {copy.sendBackAction}
                </Button>
              </span>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
