"use client";

import { Suspense, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { TEMPLATES, templateById } from "@/server/templates";
import { CONNECTORS } from "@/server/connectors";

export default function NewAgentPage() {
  return (
    <Suspense>
      <Builder />
    </Suspense>
  );
}

function Builder() {
  const router = useRouter();
  const params = useSearchParams();
  const template = templateById(params.get("template") ?? "");

  const [name, setName] = useState(template?.name ?? "");
  const [emoji] = useState(template?.emoji ?? "🤖");
  const [description, setDescription] = useState(template?.description ?? "");
  const [instructions, setInstructions] = useState(template?.instructions ?? "");
  const [grants, setGrants] = useState<string[]>(template?.toolGrants ?? []);
  const [paramsJson, setParamsJson] = useState(JSON.stringify(template?.params ?? {}, null, 2));
  const [triggerType, setTriggerType] = useState(template?.trigger.type ?? "manual");
  const [error, setError] = useState<string | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  const allTools = useMemo(
    () => CONNECTORS.flatMap((c) => c.tools.map((t) => ({ ...t, connector: c.name }))),
    []
  );

  const toggle = (tool: string) =>
    setGrants((g) => (g.includes(tool) ? g.filter((x) => x !== tool) : [...g, tool]));

  const save = async () => {
    setBusy(true);
    setError(null);
    setProblems([]);
    let parsedParams: Record<string, string> = {};
    try {
      parsedParams = JSON.parse(paramsJson || "{}");
    } catch {
      setError("Parameters must be valid JSON");
      setBusy(false);
      return;
    }
    const res = await fetch("/api/agents", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name,
        emoji,
        description,
        templateId: template?.id,
        instructions,
        toolGrants: grants,
        params: parsedParams,
        trigger: template && triggerType === template.trigger.type ? template.trigger : { type: triggerType, config: {} },
      }),
    });
    const data = await res.json();
    setBusy(false);
    if (!res.ok) {
      setError(data.error ?? "Failed to create agent");
      setProblems(data.problems ?? []);
      return;
    }
    router.push(`/agents/${data.agentId}`);
  };

  return (
    <div style={{ maxWidth: 760 }}>
      <h1 className="page-title">Hire a company agent</h1>
      <p className="page-sub">
        {template ? (
          <>Starting from the <b>{template.name}</b> template. Saved as <b>v1</b>, owned by the workspace, and bound to <b>Sandbox</b> — binding validates every granted tool is available and permitted there.</>
        ) : (
          <>From scratch: instructions, tool grants, parameters, trigger. Saved as v1 and bound to Sandbox.</>
        )}
      </p>

      <div className="card">
        <label className="label">Name</label>
        <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Closed-Won Paperwork Agent" />

        <label className="label">Description</label>
        <input className="input" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What does this agent do?" />

        <label className="label">Instructions — the agent&apos;s operating prompt</label>
        <textarea className="textarea" value={instructions} onChange={(e) => setInstructions(e.target.value)} />

        <label className="label">Tool grants — picked from what the environment offers</label>
        <div className="grid grid-2" style={{ gap: 6 }}>
          {allTools.map((t) => (
            <label key={t.name} className="flex" style={{ fontSize: 13, cursor: "pointer" }}>
              <input type="checkbox" checked={grants.includes(t.name)} onChange={() => toggle(t.name)} />
              <span className="mono">{t.name}</span>
              <span className="muted small">{t.connector}{t.kind === "write" ? " · write" : ""}</span>
            </label>
          ))}
        </div>

        <label className="label">Parameters (JSON)</label>
        <textarea className="textarea" style={{ minHeight: 110 }} value={paramsJson} onChange={(e) => setParamsJson(e.target.value)} />

        <label className="label">Trigger</label>
        <select className="select" value={triggerType} onChange={(e) => setTriggerType(e.target.value as typeof triggerType)}>
          <option value="crm_webhook">CRM webhook — deal stage change</option>
          <option value="manual">Manual</option>
          <option value="schedule">Schedule (nightly)</option>
        </select>

        {error && (
          <div className="card" style={{ marginTop: 16, borderColor: "rgba(240,106,106,0.5)" }}>
            <b style={{ color: "var(--red)" }}>{error}</b>
            {problems.map((p, i) => (
              <div key={i} className="small muted">• {p}</div>
            ))}
          </div>
        )}

        <div className="flex" style={{ marginTop: 20 }}>
          <button className="btn btn-primary" onClick={save} disabled={busy || !name || !instructions || grants.length === 0}>
            {busy ? "Validating binding…" : "Save v1 → bind to Sandbox"}
          </button>
          {!template && (
            <span className="muted small">or pick a template: {TEMPLATES.map((t) => t.emoji).join(" ")}</span>
          )}
        </div>
      </div>
    </div>
  );
}
