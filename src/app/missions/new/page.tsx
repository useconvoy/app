"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

interface Workspace {
  id: string;
  name: string;
}

export default function NewMissionPage() {
  return (
    <Suspense>
      <MissionForm />
    </Suspense>
  );
}

function MissionForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState(
    params.get("workspaceId") ?? "",
  );
  const [objective, setObjective] = useState("");
  const [deadlineMinutes, setDeadlineMinutes] = useState(15);
  const [maxParallelAgents, setMaxParallelAgents] = useState(6);
  const [maxTotalAgents, setMaxTotalAgents] = useState(13);
  const [maxDepth, setMaxDepth] = useState(2);
  const [budgetCents, setBudgetCents] = useState(100);
  const [computeMode, setComputeMode] = useState("auto");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetch("/api/auth/me")
      .then((response) => response.json())
      .then((result) => {
        setWorkspaces(result.workspaces ?? []);
        setWorkspaceId((current) => current || result.workspaces?.[0]?.id || "");
      })
      .catch(() => setError("Unable to load your workspaces."));
  }, []);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const response = await fetch("/api/missions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        workspaceId,
        objective,
        deadlineMinutes,
        maxParallelAgents,
        maxTotalAgents,
        maxDepth,
        budgetCents,
        computeMode,
      }),
    });
    const result = await response.json();
    setBusy(false);
    if (!response.ok) {
      setError(result.error ?? "Unable to create mission.");
      return;
    }
    router.push(`/missions/${result.mission.id}`);
    router.refresh();
  };

  if (workspaces.length === 0 && !error) {
    return <div className="card">Loading workspaces…</div>;
  }

  return (
    <div style={{ maxWidth: 820 }}>
      <div className="page-head">
        <div>
          <h1 className="page-title">New mission</h1>
          <p className="page-sub">
            Describe the outcome. Convoy supplies a governed execution envelope;
            the agent tree and working plan emerge during execution.
          </p>
        </div>
      </div>
      <form className="card" onSubmit={submit}>
        <label className="label" htmlFor="new-mission-workspace">
          Workspace
        </label>
        <select
          id="new-mission-workspace"
          className="select"
          value={workspaceId}
          onChange={(event) => setWorkspaceId(event.target.value)}
          required
        >
          {workspaces.map((workspace) => (
            <option value={workspace.id} key={workspace.id}>
              {workspace.name}
            </option>
          ))}
        </select>

        <label className="label" htmlFor="mission-objective">
          Objective
        </label>
        <textarea
          id="mission-objective"
          className="textarea"
          value={objective}
          onChange={(event) => setObjective(event.target.value)}
          placeholder="Research our highest-risk enterprise renewals, identify the strongest intervention for each account, and produce an evidence-backed recovery plan."
          required
          minLength={12}
          maxLength={8000}
          style={{ minHeight: 150 }}
        />

        <div className="grid grid-2 mission-envelope-grid">
          <NumberField
            id="mission-deadline"
            label="Deadline (minutes)"
            value={deadlineMinutes}
            min={5}
            max={1440}
            onChange={setDeadlineMinutes}
          />
          <div>
            <label className="label" htmlFor="mission-compute">
              Compute mode
            </label>
            <select
              id="mission-compute"
              className="select"
              value={computeMode}
              onChange={(event) => setComputeMode(event.target.value)}
            >
              <option value="auto">Auto</option>
              <option value="fast">Fast</option>
              <option value="economy">Economy</option>
              <option value="dedicated">Dedicated</option>
            </select>
          </div>
          <NumberField
            id="mission-parallel"
            label="Maximum parallel agents"
            value={maxParallelAgents}
            min={1}
            max={24}
            onChange={setMaxParallelAgents}
          />
          <NumberField
            id="mission-total"
            label="Maximum total agents"
            value={maxTotalAgents}
            min={1}
            max={500}
            onChange={setMaxTotalAgents}
          />
          <NumberField
            id="mission-depth"
            label="Maximum delegation depth"
            value={maxDepth}
            min={0}
            max={8}
            onChange={setMaxDepth}
          />
          <NumberField
            id="mission-budget"
            label="Mission budget (cents)"
            value={budgetCents}
            min={1}
            max={100000}
            onChange={setBudgetCents}
          />
        </div>

        {error && (
          <div className="notice notice-danger" style={{ marginTop: 16 }}>
            {error}
          </div>
        )}
        <button
          className="btn btn-primary"
          type="submit"
          disabled={busy || !workspaceId || objective.trim().length < 12}
          style={{ marginTop: 20 }}
        >
          {busy ? "Launching on AWS…" : "Create and launch mission"}
        </button>
      </form>
    </div>
  );
}

function NumberField({
  id,
  label,
  value,
  min,
  max,
  onChange,
}: {
  id: string;
  label: string;
  value: number;
  min: number;
  max: number;
  onChange: (value: number) => void;
}) {
  return (
    <div>
      <label className="label" htmlFor={id}>
        {label}
      </label>
      <input
        id={id}
        className="input"
        type="number"
        min={min}
        max={max}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </div>
  );
}
