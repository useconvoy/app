"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

interface Workspace {
  id: string;
  name: string;
  slug: string;
  role: string;
  createdAt: string;
}

export default function WorkspacesPage() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/workspaces")
      .then(async (response) => {
        const result = await response.json();
        if (!response.ok) throw new Error(result.error);
        setWorkspaces(result.workspaces);
      })
      .catch((cause) =>
        setError(
          cause instanceof Error ? cause.message : "Unable to load workspaces.",
        ),
      )
      .finally(() => setLoading(false));
  }, []);

  return (
    <div>
      <div className="page-head">
        <div>
          <h1 className="page-title">Workspaces</h1>
          <p className="page-sub">
            Workspaces own missions, agents, environments, policies, and audit
            history.
          </p>
        </div>
        <Link className="btn btn-primary" href="/workspaces/new">
          New workspace
        </Link>
      </div>
      {error && <div className="notice notice-danger">{error}</div>}
      {loading ? (
        <div className="card">Loading workspaces…</div>
      ) : workspaces.length === 0 ? (
        <div className="empty">
          <h2>Create your first workspace</h2>
          <p>A workspace is the governance boundary for company agents.</p>
          <Link className="btn btn-primary" href="/workspaces/new">
            Create workspace
          </Link>
        </div>
      ) : (
        <div className="grid grid-2">
          {workspaces.map((workspace) => (
            <Link
              className="card"
              href={`/missions?workspaceId=${workspace.id}`}
              key={workspace.id}
            >
              <div className="flex-between">
                <div className="card-title">{workspace.name}</div>
                <span className="badge badge-neutral">{workspace.role}</span>
              </div>
              <div className="card-sub">{workspace.slug}</div>
              <div className="faint small" style={{ marginTop: 12 }}>
                Created {new Date(workspace.createdAt).toLocaleDateString()}
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
