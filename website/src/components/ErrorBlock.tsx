import type { ReactNode } from "react";

export interface ErrorBlockProps {
  /** What happened, in plain language, e.g. "This page could not load runs." */
  whatHappened: string;
  /** What to do about it, e.g. "Try again in a moment." */
  whatToDo: string;
  action?: ReactNode;
}

/** Errors say what happened and what to do; never a bare failure (C5). */
export function ErrorBlock({ whatHappened, whatToDo, action }: ErrorBlockProps) {
  return (
    <div role="alert" className="rounded-lg border border-fail-soft bg-fail-soft p-4">
      <p className="text-sm font-medium text-fail">{whatHappened}</p>
      <p className="mt-1 text-sm text-ink">{whatToDo}</p>
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}
