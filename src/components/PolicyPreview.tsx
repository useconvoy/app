"use client";

import { useState } from "react";
import { IconAlert, IconCheck, IconFlag, IconShieldCheck, IconX } from "./icons";

type Environment = "sandbox" | "production";
type Scenario = "crm" | "email" | "flag";

const OUTCOMES: Record<
  Environment,
  Record<
    Scenario,
    {
      tool: string;
      input: string;
      credential: string;
      verdict: "allow" | "deny" | "review";
      label: string;
      note: string;
    }
  >
> = {
  sandbox: {
    crm: {
      tool: "crm.update_deal",
      input: "paperwork_status: complete",
      credential: "vault://sandbox/hubspot",
      verdict: "allow",
      label: "Executed",
      note: "Sandbox policy auto-allows CRM writes. The action is still traced.",
    },
    email: {
      tool: "email.send",
      input: "to: ap@northwindsystems.com",
      credential: "vault://sandbox/google",
      verdict: "deny",
      label: "Refused",
      note: "Sandbox email is restricted to Meridian test domains. External delivery is denied.",
    },
    flag: {
      tool: "flag_for_review",
      input: "22% discount exceeds 15% standard",
      credential: "Built-in escalation",
      verdict: "review",
      label: "Paused for review",
      note: "The agent can stop itself when a decision looks consequential or ambiguous.",
    },
  },
  production: {
    crm: {
      tool: "crm.update_deal",
      input: "paperwork_status: complete",
      credential: "vault://production/hubspot",
      verdict: "allow",
      label: "Executed + logged",
      note: "Production policy permits this CRM write and records the full call.",
    },
    email: {
      tool: "email.send",
      input: "to: ap@northwindsystems.com",
      credential: "vault://production/google",
      verdict: "review",
      label: "Paused for approval",
      note: "External email requires RevOps approval—regardless of model confidence.",
    },
    flag: {
      tool: "flag_for_review",
      input: "22% discount exceeds 15% standard",
      credential: "Built-in escalation",
      verdict: "review",
      label: "Paused for review",
      note: "Agent judgment and deterministic policy feed the same human approval queue.",
    },
  },
};

const SCENARIOS: { id: Scenario; label: string }[] = [
  { id: "crm", label: "CRM update" },
  { id: "email", label: "External email" },
  { id: "flag", label: "Uncertain decision" },
];

export function PolicyPreview() {
  const [environment, setEnvironment] = useState<Environment>("production");
  const [scenario, setScenario] = useState<Scenario>("email");
  const outcome = OUTCOMES[environment][scenario];

  return (
    <div className="policy-preview" aria-label="Interactive policy gateway preview">
      <div className="policy-preview-top">
        <div>
          <span className="preview-eyebrow">Mechanism preview</span>
          <strong>One call. An environment decides.</strong>
        </div>
        <span className="preview-live"><span /> Sample call</span>
      </div>

      <div className="preview-controls">
        <div className="preview-segment" role="group" aria-label="Choose environment">
          <button
            type="button"
            aria-pressed={environment === "sandbox"}
            className={environment === "sandbox" ? "active" : ""}
            onClick={() => setEnvironment("sandbox")}
          >
            Sandbox
          </button>
          <button
            type="button"
            aria-pressed={environment === "production"}
            className={environment === "production" ? "active" : ""}
            onClick={() => setEnvironment("production")}
          >
            Production
          </button>
        </div>
        <div className="preview-scenarios" role="group" aria-label="Choose a tool call">
          {SCENARIOS.map((item) => (
            <button
              type="button"
              key={item.id}
              aria-pressed={scenario === item.id}
              className={scenario === item.id ? "active" : ""}
              onClick={() => setScenario(item.id)}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      <div className="preview-call" aria-live="polite">
        <div className="preview-call-row">
          <span className="preview-step">01</span>
          <div>
            <span className="preview-label">Agent requests</span>
            <code>{outcome.tool}</code>
            <small>{outcome.input}</small>
          </div>
        </div>
        <div className="preview-rail" aria-hidden="true"><span /></div>
        <div className="preview-call-row preview-gateway">
          <span className="preview-step"><IconShieldCheck size={17} /></span>
          <div>
            <span className="preview-label">Policy gateway</span>
            <strong>Grant → connector → {environment} policy</strong>
            <small>{outcome.credential}</small>
          </div>
        </div>
        <div className="preview-rail" aria-hidden="true"><span /></div>
        <div className={`preview-call-row preview-outcome ${outcome.verdict}`}>
          <span className="preview-step">
            {outcome.verdict === "allow" ? <IconCheck size={17} /> : outcome.verdict === "deny" ? <IconX size={17} /> : scenario === "flag" ? <IconFlag size={17} /> : <IconAlert size={17} />}
          </span>
          <div>
            <span className="preview-label">Verdict</span>
            <strong>{outcome.label}</strong>
            <small>{outcome.note}</small>
          </div>
        </div>
      </div>

      <p className="preview-disclosure">
        This preview mirrors the repository&apos;s seeded policies. It does not change the shared sample workspace.
      </p>
    </div>
  );
}
