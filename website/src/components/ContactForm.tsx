"use client";

import { useActionState, useEffect, useRef } from "react";

import { sendContactRequest, type ContactState } from "@/app/actions/contact";
import { CONTACT } from "@/content/homepage";
import { ArrowGlyph } from "./Hero";
import { CONTACT_FIELDS, EMPTY_VALUES, LIMITS, type ContactField } from "@/lib/contact/validate";

const INITIAL: ContactState = { status: "idle", errors: {}, values: EMPTY_VALUES };

/**
 * The technical intake form. Labels are visible, controls are 48px, errors
 * are announced and focused, and everything typed survives every outcome
 * except a confirmed send. While no verified delivery destination exists the
 * form runs in a preview state: it says so before and after submission and
 * never claims receipt.
 */
export function ContactForm({ deliveryAvailable }: { deliveryAvailable: boolean }) {
  const [state, formAction, pending] = useActionState(sendContactRequest, INITIAL);
  const summaryRef = useRef<HTMLDivElement>(null);
  const statusRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (state.status === "invalid") summaryRef.current?.focus();
    else if (state.status !== "idle") statusRef.current?.focus();
  }, [state]);

  const errorFields = CONTACT_FIELDS.filter((field) => state.errors[field]);
  const optionalHasError = errorFields.some((field) => field === "name" || field === "hardware");

  return (
    <form action={formAction} noValidate className="rounded-lg border border-subtle bg-surface p-5 sm:p-8 lg:p-10" aria-labelledby="contact-form-title">
      <h3 id="contact-form-title" className="type-h3 text-primary">
        {CONTACT.formTitle}
      </h3>

      {!deliveryAvailable ? (
        <div
          id="contact-availability"
          role="note"
          className="mt-5 flex gap-3 rounded bg-information-tint px-4 py-3 text-information"
        >
          <span aria-hidden="true" className="mt-[7px] h-2 w-2 flex-none rounded-full bg-current" />
          <span>
            <span className="type-small block font-medium">{CONTACT.states.unavailableTitle}</span>
            <span className="type-small block">{CONTACT.states.unavailable}</span>
          </span>
        </div>
      ) : null}

      {/* Outcome of the last submission. Focused after each action so it is announced. */}
      <div
        ref={statusRef}
        tabIndex={-1}
        role="status"
        aria-live="polite"
        className={state.status === "idle" || state.status === "invalid" ? "sr-only" : "mt-5 rounded border px-4 py-3 type-body " + statusTone(state.status)}
      >
        {state.status === "sent" ? CONTACT.states.success : null}
        {state.status === "failed" ? CONTACT.states.error : null}
        {state.status === "unavailable" ? CONTACT.states.unavailableAfterSubmit : null}
      </div>

      {state.status === "invalid" ? (
        <div
          ref={summaryRef}
          tabIndex={-1}
          role="alert"
          className="mt-5 rounded border border-error bg-error-tint px-4 py-3 text-primary"
        >
          <p className="type-label">{CONTACT.states.invalidSummary}</p>
          <ul className="mt-2 list-disc pl-5 type-small">
            {errorFields.map((field) => (
              <li key={field}>
                <a href={`#contact-${field}`} className="text-link">
                  {CONTACT.fields[field].label}: {state.errors[field]}
                </a>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="mt-6 grid gap-5">
        <Field field="email" state={state} type="email" autoComplete="email" inputMode="email" />
        <Field field="company" state={state} autoComplete="organization" />
        <Field field="blocker" state={state} multiline />

        <details className="disclosure border-t border-subtle pt-2" open={optionalHasError || undefined}>
          <summary className="type-label text-primary">
            <span>{CONTACT.optionalLabel}</span>
            <svg className="disclosure-mark text-accent" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
              <path d="M12 5v14M5 12h14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
          </summary>
          <div className="grid gap-5 pb-2">
            <Field field="name" state={state} autoComplete="name" />
            <Field field="hardware" state={state} />
          </div>
        </details>

        {/* Honeypot. Hidden from people and assistive technology; a bot that fills it is rejected. */}
        <div className="hidden" aria-hidden="true">
          <label htmlFor="contact-website">Website</label>
          <input id="contact-website" name="website" type="text" tabIndex={-1} autoComplete="off" defaultValue="" />
        </div>

        <p className="type-small text-muted">{CONTACT.privacyNote}</p>

        <button
          type="submit"
          className="btn btn-primary w-full sm:w-auto"
          disabled={pending}
          aria-describedby={deliveryAvailable ? undefined : "contact-availability"}
        >
          {pending ? "Sending…" : CONTACT.submit}
          <ArrowGlyph />
        </button>
      </div>
    </form>
  );
}

function statusTone(status: ContactState["status"]): string {
  if (status === "sent") return "border-success bg-success-tint";
  if (status === "failed") return "border-error bg-error-tint";
  return "border-warning bg-warning-tint";
}

function Field({
  field,
  state,
  type = "text",
  multiline = false,
  autoComplete,
  inputMode,
}: {
  field: ContactField;
  state: ContactState;
  type?: string;
  multiline?: boolean;
  autoComplete?: string;
  inputMode?: "email" | "text";
}) {
  const copy = CONTACT.fields[field];
  const id = `contact-${field}`;
  const error = state.errors[field];
  const errorId = `${id}-error`;
  const shared = {
    id,
    name: field,
    defaultValue: state.values[field],
    maxLength: LIMITS[field],
    "aria-required": copy.required || undefined,
    "aria-invalid": error ? true : undefined,
    "aria-describedby": error ? errorId : undefined,
    autoComplete,
    className: "field-input",
  } as const;

  return (
    <div>
      <label htmlFor={id} className="type-label block text-primary">
        {copy.label}
        {copy.required ? (
          <span className="text-accent" aria-hidden="true">
            {" "}
            *
          </span>
        ) : (
          <span className="type-small font-normal text-muted"> (optional)</span>
        )}
      </label>
      {multiline ? (
        <textarea {...shared} rows={5} className="field-input mt-2 min-h-[144px] resize-y" />
      ) : (
        <input {...shared} type={type} inputMode={inputMode} className="field-input mt-2" />
      )}
      {error ? (
        <p id={errorId} className="type-small mt-2 font-medium text-error">
          {error}
        </p>
      ) : null}
    </div>
  );
}
