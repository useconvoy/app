/**
 * Feedback composer: pick a kind (helpful rating, comment, correction),
 * write the words, send. Client-side validation mirrors the server action's
 * rules so nobody posts an empty comment or an unrated star review; the
 * server re-checks everything. The submit action arrives as a prop bound
 * to a run by the mounting page, so this component never chooses the run.
 */
"use client";

import { useRef, useState } from "react";

import { Button } from "@/components/Button";
import { feedbackCopy, feedbackKindLabels, ratingLabels, type FeedbackKind } from "@/lexicon";

export interface FeedbackComposerProps {
  /**
   * Server action receiving the form data; bound to a run by the page.
   * Absent only in the disabled state, where nothing can be submitted.
   */
  action?: (formData: FormData) => void | Promise<void>;
  /** Render disabled with a plain note, e.g. when the routine never ran. */
  disabledNote?: string;
  /** Quiet note about where the feedback attaches. */
  attachNote?: string;
}

export function FeedbackComposer({ action, disabledNote, attachNote }: FeedbackComposerProps) {
  const [kind, setKind] = useState<FeedbackKind>("rating");
  const [rating, setRating] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const disabled = disabledNote !== undefined;

  function validate(event: React.FormEvent<HTMLFormElement>) {
    const data = new FormData(event.currentTarget);
    if (kind === "rating" && !data.get("rating")) {
      event.preventDefault();
      setError(feedbackCopy.ratingRequired);
      return;
    }
    if (kind !== "rating" && String(data.get("body") ?? "").trim().length === 0) {
      event.preventDefault();
      setError(feedbackCopy.bodyRequired);
      return;
    }
    setError(null);
  }

  return (
    <form
      ref={formRef}
      action={action}
      onSubmit={validate}
      className="space-y-4 rounded-lg border border-line bg-card p-5"
      aria-label={feedbackCopy.composerTitle}
    >
      <h3 className="text-sm font-medium text-ink">{feedbackCopy.composerTitle}</h3>

      <fieldset disabled={disabled} className="m-0 border-0 p-0">
        <legend className="text-xs font-medium uppercase tracking-wide text-muted">
          {feedbackCopy.kindLegend}
        </legend>
        <div className="mt-2 flex flex-wrap gap-3">
          {(Object.keys(feedbackKindLabels) as FeedbackKind[]).map((value) => (
            <label key={value} className="flex items-center gap-1.5 text-sm text-ink">
              <input
                type="radio"
                name="kind"
                value={value}
                checked={kind === value}
                onChange={() => {
                  setKind(value);
                  setError(null);
                }}
              />
              {feedbackKindLabels[value]}
            </label>
          ))}
        </div>
      </fieldset>

      {kind === "rating" && (
        <fieldset disabled={disabled} className="m-0 border-0 p-0">
          <legend className="text-xs font-medium uppercase tracking-wide text-muted">
            {feedbackCopy.ratingLegend}
          </legend>
          <div className="mt-2 flex flex-wrap gap-3">
            {[1, 2, 3, 4, 5].map((value) => (
              <label key={value} className="flex items-center gap-1.5 text-sm text-ink">
                <input
                  type="radio"
                  name="rating"
                  value={value}
                  checked={rating === value}
                  onChange={() => {
                    setRating(value);
                    setError(null);
                  }}
                  aria-label={`${value} ${value === 1 ? "star" : "stars"}: ${ratingLabels[value]}`}
                />
                <span aria-hidden="true" className="font-mono text-xs">
                  {value}
                </span>
              </label>
            ))}
          </div>
          {rating !== null && <p className="mt-1 text-xs text-muted">{ratingLabels[rating]}</p>}
        </fieldset>
      )}

      <div>
        <label htmlFor="feedback-body" className="text-xs font-medium uppercase tracking-wide text-muted">
          {feedbackCopy.bodyLabel}
        </label>
        <textarea
          id="feedback-body"
          name="body"
          rows={3}
          disabled={disabled}
          placeholder={
            kind === "correction"
              ? feedbackCopy.bodyPlaceholderCorrection
              : feedbackCopy.bodyPlaceholderComment
          }
          className="mt-1 w-full rounded-md border border-line bg-card px-2 py-1.5 text-sm text-ink disabled:opacity-50"
        />
      </div>

      {error && (
        <p role="alert" className="text-sm text-fail">
          {error}
        </p>
      )}
      {disabledNote && <p className="text-sm text-muted">{disabledNote}</p>}
      {!disabled && attachNote && <p className="text-xs text-muted">{attachNote}</p>}

      <Button type="submit" disabled={disabled}>
        {feedbackCopy.submit}
      </Button>
    </form>
  );
}
