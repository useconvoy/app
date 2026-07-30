"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

interface Mission {
  id: string;
  workspaceId: string;
  objective: string;
  state: string;
  provider: string;
  summary?: string;
  totalAgents?: number;
  createdAt: string;
  completedAt?: string;
  envelope: {
    deadline: string;
    maxParallelAgents: number;
    maxTotalAgents: number;
    maxDepth: number;
    computeMode: string;
    budgetCents: number;
  };
}

interface CloudAgent {
  agentId: string;
  depth: number;
  status: string;
  computeProvider?: string;
  artifactKey?: string;
  completedAt?: string;
}

export default function MissionPage() {
  const params = useParams<{ id: string }>();
  const [mission, setMission] = useState<Mission | null>(null);
  const [agents, setAgents] = useState<CloudAgent[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const response = await fetch(`/api/missions/${params.id}`, {
      cache: "no-store",
    });
    const result = await response.json();
    if (!response.ok) {
      setError(result.error ?? "Unable to load mission.");
      return;
    }
    setMission(result.mission);
    setAgents(result.cloudMission?.agents ?? []);
  }, [params.id]);

  useEffect(() => {
    void refresh();
    const interval = window.setInterval(() => {
      if (
        !mission ||
        !["succeeded", "failed", "cancelled"].includes(mission.state)
      ) {
        void refresh();
      }
    }, 2500);
    return () => window.clearInterval(interval);
  }, [mission, refresh]);

  if (error) return <div className="notice notice-danger">{error}</div>;
  if (!mission) return <div className="card">Loading mission…</div>;

  return (
    <div>
      <div className="page-head">
        <div>
          <div className="flex" style={{ marginBottom: 8 }}>
            <span className={`mission-state state-${mission.state}`}>
              {mission.state.replaceAll("_", " ")}
            </span>
            <span className="tag">{mission.provider}</span>
          </div>
          <h1 className="page-title">Mission control</h1>
          <p className="page-sub mission-objective">{mission.objective}</p>
        </div>
        <Link
          className="btn"
          href={`/missions?workspaceId=${mission.workspaceId}`}
        >
          All missions
        </Link>
      </div>

      <div className="grid grid-4">
        <Metric label="Logical agents" value={mission.totalAgents ?? agents.length} />
        <Metric label="Parallel limit" value={mission.envelope.maxParallelAgents} />
        <Metric label="Depth limit" value={mission.envelope.maxDepth} />
        <Metric
          label="Budget"
          value={`$${(mission.envelope.budgetCents / 100).toFixed(2)}`}
        />
      </div>

      {mission.summary && (
        <div className="notice notice-success" style={{ marginTop: 16 }}>
          {mission.summary}
        </div>
      )}

      <div className="section">
        <div className="section-head">
          <div>
            <div className="section-title">Dynamic agent tree</div>
            <div className="section-note">
              The graph is created at runtime under the mission envelope.
            </div>
          </div>
        </div>
        {agents.length === 0 ? (
          <div className="card">
            AWS is preparing the coordinator. Agent episodes will appear here
            as Temporal admits them.
          </div>
        ) : (
          <div className="card card-flush">
            <table className="table">
              <thead>
                <tr>
                  <th>Agent</th>
                  <th>Depth</th>
                  <th>Status</th>
                  <th>Compute</th>
                  <th>Artifact</th>
                </tr>
              </thead>
              <tbody>
                {agents.map((agent) => (
                  <tr key={agent.agentId}>
                    <td>
                      <code>{agent.agentId}</code>
                    </td>
                    <td>{agent.depth}</td>
                    <td>
                      <span
                        className={`mission-state state-${agent.status.toLowerCase()}`}
                      >
                        {agent.status.toLowerCase()}
                      </span>
                    </td>
                    <td className="muted">
                      {agent.computeProvider ?? "AWS Fargate"}
                    </td>
                    <td className="faint small">
                      {agent.artifactKey ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card">
        <div className="card-title">Execution envelope</div>
        <dl className="mission-envelope">
          <div>
            <dt>Deadline</dt>
            <dd>{new Date(mission.envelope.deadline).toLocaleString()}</dd>
          </div>
          <div>
            <dt>Maximum total agents</dt>
            <dd>{mission.envelope.maxTotalAgents}</dd>
          </div>
          <div>
            <dt>Compute mode</dt>
            <dd>{mission.envelope.computeMode}</dd>
          </div>
          <div>
            <dt>Created</dt>
            <dd>{new Date(mission.createdAt).toLocaleString()}</dd>
          </div>
        </dl>
      </div>
    </div>
  );
}

function Metric({
  label,
  value,
}: {
  label: string;
  value: string | number;
}) {
  return (
    <div className="card">
      <div className="kpi-value">{value}</div>
      <div className="kpi-label">{label}</div>
    </div>
  );
}
