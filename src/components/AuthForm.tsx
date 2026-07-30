"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

export function AuthForm({ mode }: { mode: "login" | "register" }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const response = await fetch(
      mode === "register" ? "/api/auth/register" : "/api/auth/login",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...(mode === "register" ? { name } : {}),
          email,
          password,
        }),
      },
    );
    const result = await response.json();
    setBusy(false);
    if (!response.ok) {
      setError(result.error ?? "Unable to continue.");
      return;
    }
    const requested = searchParams.get("next");
    router.push(
      requested && requested.startsWith("/")
        ? requested
        : result.needsWorkspace
          ? "/workspaces/new"
          : "/missions",
    );
    router.refresh();
  };

  return (
    <form className="auth-card" onSubmit={submit}>
      <div className="auth-brand">
        <span className="brand-mark">C</span>
        <span>Convoy Labs</span>
      </div>
      <div>
        <h1>{mode === "register" ? "Create your account" : "Welcome back"}</h1>
        <p>
          {mode === "register"
            ? "Create a governed workspace and launch durable agent missions."
            : "Sign in to your Convoy workspaces and missions."}
        </p>
      </div>
      {mode === "register" && (
        <div>
          <label className="label" htmlFor="auth-name">
            Your name
          </label>
          <input
            id="auth-name"
            className="input"
            autoComplete="name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            required
            minLength={2}
          />
        </div>
      )}
      <div>
        <label className="label" htmlFor="auth-email">
          Email
        </label>
        <input
          id="auth-email"
          className="input"
          type="email"
          autoComplete="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          required
        />
      </div>
      <div>
        <label className="label" htmlFor="auth-password">
          Password
        </label>
        <input
          id="auth-password"
          className="input"
          type="password"
          autoComplete={
            mode === "register" ? "new-password" : "current-password"
          }
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          required
          minLength={mode === "register" ? 10 : 1}
        />
        {mode === "register" && (
          <span className="hint">Use at least 10 characters.</span>
        )}
      </div>
      {error && <div className="notice notice-danger">{error}</div>}
      <button
        className="btn btn-primary"
        type="submit"
        disabled={
          busy ||
          !email ||
          !password ||
          (mode === "register" && !name)
        }
      >
        {busy
          ? "Working…"
          : mode === "register"
            ? "Create account"
            : "Sign in"}
      </button>
      <p className="auth-switch">
        {mode === "register" ? (
          <>
            Already have an account? <Link href="/login">Sign in</Link>
          </>
        ) : (
          <>
            New to Convoy? <Link href="/register">Create an account</Link>
          </>
        )}
      </p>
    </form>
  );
}
