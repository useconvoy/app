"use client";

import { Suspense, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { templateById } from "@/server/templates";
import { CONNECTORS } from "@/server/connectors";
import type { TriggerSpec } from "@/server/types";

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
  const [description, setDescription] = useState(template?.description ?? "");
  const [instructions, setInstructions] = useState(template?.instructions ?? "");
  const [grants, setGrants] = useState<string[]>(template?.toolGrants ?? []);
  const [paramsJson, setParamsJson] = useState(JSON.stringify(template?.params ?? {}, null, 2));
  const [triggerType, setTriggerType] = useState<TriggerSpec["type"]>(template?.trigger.type ?? "manual");
  const [error, setError] = useState<string | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  const allTools = useMemo(
    () => CONNECTORS.flatMap((c) => c.tools.map((t) => ({ ...t, connector: c.name }))),
    []
  );

  const toggle = (tool: string) =>
    setGrants((g) => (g.includes(tool) ? g.filter((x) => x !== tool) : [...g, tool]));

  const buildTrigger = (): TriggerSpec => {
    if (template && triggerType === template.trigger.type) return template.trigger;
    if (triggerType === "crm_webhook") return { type: "crm_webhook", config: { event: "deal.stage_changed", to_stage: "closedwon" } };
    if (triggerType === "schedule") return { type: "schedule", config: { cron: "0 2 * * *" } };
    return { type: "manual", config: {} };
  };

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
        description,
        templateId: template?.id,
        instructions,
        toolGrants: grants,
        params: parsedParams,
        trigger: buildTrigger(),
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
    <div style={{ maxWidth: 780 }}>
      <div className="page-head">
        <div>
          <h1 className="page-title">New agent</h1>
          <p className="page-sub">
            {template ? (
              <>Starting from the <b>{template.name}</b> template. The agent is saved as <b>v1</b>, owned by the workspace, and bound to <b>Sandbox</b> — binding validates that every granted tool is available and permitted there.</>
            ) : (
              <>Define the agent&apos;s instructions, tool grants, parameters, and trigger. It is saved as v1 and bound to Sandbox for validation.</>
            )}
          </p>
        </div>
      </div>

      <div className="card">
        <label className="label" htmlFor="agent-name">Name</label>
        <input id="agent-name" className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Closed-Won Paperwork Agent" />

        <label className="label" htmlFor="agent-desc">Description</label>
        <input id="agent-desc" className="input" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What does this agent do?" />

        <label className="label" htmlFor="agent-instructions">Instructions <span className="hint">— the agent&apos;s operating prompt</span></label>
        <textarea id="agent-instructions" className="textarea" value={instructions} onChange={(e) => setInstructions(e.target.value)} />

        <label className="label">Tool grants <span className="hint">— selected from what the target environment offers</span></label>
        <div className="grid grid-2" style={{ gap: 2 }}>
          {allTools.map((t) => (
            <label key={t.name} className="checkbox-row">
              <input type="checkbox" checked={grants.includes(t.name)} onChange={() => toggle(t.name)} />
              <span className="mono">{t.name}</span>
              <span className="faint small">{t.connector}{t.kind === "write" ? " · write" : ""}</span>
            </label>
          ))}
        </div>

        <label className="label" htmlFor="agent-params">Parameters <span className="hint">(JSON)</span></label>
        <textarea id="agent-params" className="textarea" style={{ minHeight: 110 }} value={paramsJson} onChange={(e) => setParamsJson(e.target.value)} />

        <label className="label" htmlFor="agent-trigger">Trigger</label>
        <select id="agent-trigger" className="select" value={triggerType} onChange={(e) => setTriggerType(e.target.value as TriggerSpec["type"])}>
          <option value="crm_webhook">CRM webhook — deal moves to Closed-Won</option>
          <option value="manual">Manual</option>
          <option value="schedule">Schedule — nightly</option>
        </select>

        {error && (
          <div className="notice notice-danger" style={{ marginTop: 16 }}>
            <div>
              <b>{error}</b>
              {problems.map((p, i) => (
                <div key={i} className="small">{p}</div>
              ))}
            </div>
          </div>
        )}

        <div className="flex" style={{ marginTop: 20 }}>
          <button className="btn btn-primary" onClick={save} disabled={busy || !name || !instructions || grants.length === 0}>
            {busy ? "Validating binding…" : "Save v1 and bind to Sandbox"}
          </button>
          <span className="faint small">Requires a name, instructions, and at least one tool grant.</span>
        </div>
      </div>
    </div>
  );
}
