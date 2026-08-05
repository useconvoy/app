"use client";

import { useActionState } from "react";

import { requestDemo, type DemoFormState } from "./actions";

const INITIAL_STATE: DemoFormState = { status: "idle", errors: {} };

function Field({
  id,
  label,
  error,
  children,
}: {
  id: string;
  label: string;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label htmlFor={id} className="block text-sm font-medium text-ink">
        {label}
      </label>
      <div className="mt-1.5">{children}</div>
      {error && (
        <p id={`${id}-error`} className="mt-1.5 text-sm text-fail">
          {error}
        </p>
      )}
    </div>
  );
}

const INPUT_CLASSES =
  "w-full rounded-md border border-line bg-card px-3 py-2 text-sm text-ink placeholder:text-muted";

export function DemoRequestForm() {
  const [state, formAction, pending] = useActionState(
    requestDemo,
    INITIAL_STATE,
  );

  if (state.status === "submitted") {
    return (
      <div
        role="status"
        className="rounded-lg border border-pass bg-pass-soft p-8"
      >
        <h2 className="font-display text-xl font-medium text-ink">
          Thank you{state.name ? `, ${state.name}` : ""}. We received your
          request.
        </h2>
        <p className="mt-3 text-sm leading-relaxed text-ink">
          A person, not a routine, reads every request. We will reply from a
          real inbox within two working days to find a time that suits you.
        </p>
      </div>
    );
  }

  return (
    <form action={formAction} noValidate className="space-y-5">
      {state.status === "invalid" && (
        <p role="alert" className="rounded-md border border-fail bg-fail-soft px-4 py-3 text-sm text-ink">
          Please fix the fields below and send again.
        </p>
      )}
      <Field id="demo-name" label="Your name" error={state.errors.name}>
        <input
          id="demo-name"
          name="name"
          type="text"
          autoComplete="name"
          required
          aria-invalid={state.errors.name ? true : undefined}
          aria-describedby={state.errors.name ? "demo-name-error" : undefined}
          className={INPUT_CLASSES}
        />
      </Field>
      <Field id="demo-email" label="Work email" error={state.errors.email}>
        <input
          id="demo-email"
          name="email"
          type="email"
          autoComplete="email"
          required
          aria-invalid={state.errors.email ? true : undefined}
          aria-describedby={state.errors.email ? "demo-email-error" : undefined}
          className={INPUT_CLASSES}
        />
      </Field>
      <Field id="demo-company" label="Company" error={state.errors.company}>
        <input
          id="demo-company"
          name="company"
          type="text"
          autoComplete="organization"
          required
          aria-invalid={state.errors.company ? true : undefined}
          aria-describedby={
            state.errors.company ? "demo-company-error" : undefined
          }
          className={INPUT_CLASSES}
        />
      </Field>
      <Field
        id="demo-handoff"
        label="What routine work do you want to hand off?"
        error={state.errors.handoff}
      >
        <textarea
          id="demo-handoff"
          name="handoff"
          rows={4}
          placeholder="For example: our quarterly access review, or chasing vendors for updated documents."
          aria-invalid={state.errors.handoff ? true : undefined}
          aria-describedby={
            state.errors.handoff ? "demo-handoff-error" : undefined
          }
          className={INPUT_CLASSES}
        />
      </Field>
      <button
        type="submit"
        disabled={pending}
        className="inline-flex items-center rounded-md bg-pine px-5 py-2.5 text-sm font-medium text-card hover:bg-pine-deep disabled:opacity-60"
      >
        {pending ? "Sending…" : "Send request"}
      </button>
    </form>
  );
}
