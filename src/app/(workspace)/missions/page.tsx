"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

interface Workspace {
  id: string;
  name: string;
}

interface Mission {
  id: string;
  workspaceId: string;
  objective: string;
  state: string;
  provider: string;
  createdAt: string;
  totalAgents?: number;
}

export default function MissionsPage() {
  return (
    <Suspense>
      <MissionList />
    </Suspense>
  );
}

function MissionList() {
  const params = useSearchParams();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState(
    params.get("workspaceId") ?? "",
  );
  const [missions, setMissions] = useState<Mission[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/auth/me")
      .then(async (response) => {
        const result = await response.json();
        if (!response.ok) throw new Error(result.error);
        setWorkspaces(result.workspaces);
        setWorkspaceId((current) => current || result.workspaces[0]?.id || "");
      })
      .catch((cause) =>
        setError(
          cause instanceof Error ? cause.message : "Unable to load account.",
        ),
      );
  }, []);

  useEffect(() => {
    if (!workspaceId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    fetch(`/api/missions?workspaceId=${encodeURIComponent(workspaceId)}`)
      .then(async (response) => {
        const result = await response.json();
        if (!response.ok) throw new Error(result.error);
        setMissions(result.missions);
      })
      .catch((cause) =>
        setError(
          cause instanceof Error ? cause.message : "Unable to load missions.",
        ),
      )
      .finally(() => setLoading(false));
  }, [workspaceId]);

  const workspace = workspaces.find((item) => item.id === workspaceId);

  return (
    <div>
      <div className="page-head">
        <div>
          <h1 className="page-title">Missions</h1>
          <p className="page-sub">
            Durable objectives executed by policy-governed agent trees on AWS.
          </p>
        </div>
        {workspaceId && (
          <Link
            className="btn btn-primary"
            href={`/missions/new?workspaceId=${workspaceId}`}
          >
            New mission
          </Link>
        )}
      </div>
      {workspaces.length > 1 && (
        <div className="card" style={{ marginBottom: 16 }}>
          <label className="label" htmlFor="mission-workspace">
            Workspace
          </label>
          <select
            id="mission-workspace"
            className="select"
            value={workspaceId}
            onChange={(event) => setWorkspaceId(event.target.value)}
          >
            {workspaces.map((item) => (
              <option value={item.id} key={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </div>
      )}
      {error && <div className="notice notice-danger">{error}</div>}
      {!loading && workspaces.length === 0 ? (
        <div className="empty">
          <h2>Create a workspace first</h2>
          <p>Missions always belong to an organization workspace.</p>
          <Link className="btn btn-primary" href="/workspaces/new">
            Create workspace
          </Link>
        </div>
      ) : loading ? (
        <div className="card">Loading missions…</div>
      ) : missions.length === 0 ? (
        <div className="empty">
          <h2>No missions yet</h2>
          <p>
            Launch the first governed objective for {workspace?.name ?? "this workspace"}.
          </p>
          <Link
            className="btn btn-primary"
            href={`/missions/new?workspaceId=${workspaceId}`}
          >
            Create mission
          </Link>
        </div>
      ) : (
        <div className="card card-flush">
          <table className="table">
            <thead>
              <tr>
                <th>Objective</th>
                <th>Status</th>
                <th>Provider</th>
                <th>Agents</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {missions.map((mission) => (
                <tr key={mission.id}>
                  <td>
                    <Link
                      className="row-link"
                      href={`/missions/${mission.id}`}
                    >
                      {mission.objective}
                    </Link>
                  </td>
                  <td>
                    <span className={`mission-state state-${mission.state}`}>
                      {mission.state.replaceAll("_", " ")}
                    </span>
                  </td>
                  <td className="muted">{mission.provider}</td>
                  <td>{mission.totalAgents ?? "—"}</td>
                  <td className="faint">
                    {new Date(mission.createdAt).toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
