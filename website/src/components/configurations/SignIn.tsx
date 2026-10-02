"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { api, errorText } from "@/lib/platform/client";

/** The wordmark above the sign-in form; it leads to `/app`. */
export function Brand() { return <Link className="console-brand" href="/app">Convoy</Link>; }

/** The workspace sign-in form, shown by the session gate (`Session.tsx`) when there is no session. */
export function Login({ initialError, onLogin }: { initialError: string | null; onLogin: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(initialError);
  const lock = useRef(false);
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (lock.current) return;
    lock.current = true; setBusy(true); setError(null);
    const form = event.currentTarget;
    const data = new FormData(form);
    try { await api("auth/login", { email: data.get("email"), password: data.get("password") }); form.reset(); await onLogin(); }
    catch (cause) { setError(errorText(cause)); }
    finally { lock.current = false; setBusy(false); }
  }
  return <main className="console-auth"><Brand /><section className="console-card">
    <p className="console-eyebrow">Your Convoy workspace</p><h1>One workspace for your robots.</h1>
    <p>Sign in to connect a device, test its model, and manage robot applications.</p>
    <form onSubmit={event => void submit(event)}>
      <label>Email<input name="email" type="email" autoComplete="username" required /></label>
      <label>Password<input name="password" type="password" autoComplete="current-password" required /></label>
      {error && <p className="console-alert" role="alert">{error}</p>}
      <button className="btn btn-primary" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
    </form><p className="console-note">Use your Convoy account for devices, applications, and simulation results.</p>
  </section></main>;
}
