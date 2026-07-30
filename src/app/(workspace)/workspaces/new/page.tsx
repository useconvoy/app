"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export default function NewWorkspacePage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const response = await fetch("/api/workspaces", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    const result = await response.json();
    setBusy(false);
    if (!response.ok) {
      setError(result.error ?? "Unable to create workspace.");
      return;
    }
    router.push(`/missions?workspaceId=${result.workspace.id}`);
    router.refresh();
  };

  return (
    <div style={{ maxWidth: 680 }}>
      <div className="page-head">
        <div>
          <h1 className="page-title">Create a workspace</h1>
          <p className="page-sub">
            This becomes the ownership and policy boundary for your missions
            and company agents.
          </p>
        </div>
      </div>
      <form className="card" onSubmit={submit}>
        <label className="label" htmlFor="workspace-name">
          Workspace name
        </label>
        <input
          id="workspace-name"
          className="input"
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="Acme Operations"
          required
          minLength={2}
          maxLength={100}
          autoFocus
        />
        {error && (
          <div className="notice notice-danger" style={{ marginTop: 16 }}>
            {error}
          </div>
        )}
        <button
          className="btn btn-primary"
          type="submit"
          disabled={busy || name.trim().length < 2}
          style={{ marginTop: 20 }}
        >
          {busy ? "Creating…" : "Create workspace"}
        </button>
      </form>
    </div>
  );
}
