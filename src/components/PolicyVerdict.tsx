import { SEED_POLICIES, evaluateRules, type PolicyDecision } from "@/server/policies";
import type { ConnectorId, PolicyEffect } from "@/server/types";
import { IconCheck, IconHand, IconX } from "./icons";

/**
 * The same tool call, evaluated against both environments at once.
 *
 * This is not a mock-up. Every verdict below comes from `evaluateRules` — the
 * exact function `src/server/gateway.ts` calls on each real tool call — run
 * against `SEED_POLICIES`, the rules Meridian Labs' two environments ship with.
 *
 * It is a Server Component. All eight verdicts are computed at build time and
 * rendered into the HTML; a radio group plus `:has()` decides which pair is
 * visible. No client JavaScript, no hydration, and the widget still works with
 * scripting disabled. Nothing here touches the demo store, so a public visitor
 * cannot change what another visitor sees.
 */

interface Call {
  id: string;
  intent: string;
  tool: string;
  connectorId: ConnectorId;
  args: Record<string, string>;
}

const CALLS: Call[] = [
  {
    id: "invoice",
    intent: "Invoice the customer",
    tool: "email.send",
    connectorId: "google_email",
    args: {
      to: "ap@northwindsystems.com",
      subject: "Invoice & Order Form — Northwind Systems",
    },
  },
  {
    id: "paperwork",
    intent: "Update the deal record",
    tool: "crm.update_deal",
    connectorId: "hubspot",
    args: {
      dealId: "deal_prod_northwind",
      fields: '{ "paperwork_status": "complete" }',
    },
  },
  {
    id: "publish",
    intent: "Publish a customer doc",
    tool: "docs.publish",
    connectorId: "google_docs",
    args: { title: "Rate limits (draft update)" },
  },
  {
    id: "merge",
    intent: "Merge duplicate records",
    tool: "crm.merge_records",
    connectorId: "hubspot",
    args: { primary: "record #4221", duplicate: "record #4221-b" },
  },
];

const SANDBOX_RULES = SEED_POLICIES.filter((r) => r.environmentId === "env_sandbox");
const PRODUCTION_RULES = SEED_POLICIES.filter((r) => r.environmentId === "env_production");

const VERDICT: Record<PolicyEffect, { label: string; className: string }> = {
  allow: { label: "Executed", className: "verdict-allow" },
  require_approval: { label: "Held for approval", className: "verdict-review" },
  deny: { label: "Refused", className: "verdict-deny" },
};

function VerdictIcon({ effect }: { effect: PolicyEffect }) {
  if (effect === "allow") return <IconCheck size={15} />;
  if (effect === "deny") return <IconX size={15} />;
  return <IconHand size={15} />;
}

function Outcome({
  environment,
  credential,
  decision,
}: {
  environment: string;
  credential: string;
  decision: PolicyDecision;
}) {
  const copy = VERDICT[decision.effect];
  return (
    <div className="verdict-col">
      <div className="verdict-env">
        <b>{environment}</b>
        <span className="code">{credential}</span>
      </div>
      <p className={`verdict-outcome ${copy.className}`}>
        <VerdictIcon effect={decision.effect} />
        {copy.label}
      </p>
      <p className="verdict-reason">{decision.note ?? "No rule matched."}</p>
      <span className="verdict-rule">
        <b>rule</b> {decision.rule?.id ?? "(environment default)"}
        {decision.rule?.approvers ? ` · approver ${decision.rule.approvers.join(", ")}` : ""}
      </span>
    </div>
  );
}

export function PolicyVerdict() {
  return (
    <section className="verdict" aria-labelledby="verdict-title">
      <div className="verdict-head">
        <div>
          <h2 id="verdict-title">One tool call, two environments</h2>
          <p className="micro">Evaluated by the gateway&rsquo;s own policy engine</p>
        </div>
        <span className="verdict-live">
          <i aria-hidden="true" />
          Live
        </span>
      </div>

      <fieldset className="verdict-picker">
        <legend className="verdict-picker-label">What the agent is trying to do</legend>
        <div className="verdict-options">
          {CALLS.map((call, index) => (
            <label className="verdict-option" key={call.id} htmlFor={`call-${call.id}`}>
              <input
                type="radio"
                name="verdict-call"
                id={`call-${call.id}`}
                value={call.id}
                defaultChecked={index === 0}
              />
              <span className="verdict-option-face">
                <code>{call.tool}</code>
                <span>{call.intent}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>

      <div className="verdict-panels">
        {CALLS.map((call) => (
          <div className="verdict-panel" data-call={call.id} key={call.id}>
            <dl className="verdict-args">
              {Object.entries(call.args).map(([key, value]) => (
                <div key={key}>
                  <dt>{key}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
            </dl>
            <div className="verdict-split">
              <Outcome
                environment="Sandbox"
                credential="vault://sandbox"
                decision={evaluateRules(SANDBOX_RULES, "sandbox", call.connectorId, call.tool, call.args)}
              />
              <Outcome
                environment="Production"
                credential="vault://production"
                decision={evaluateRules(
                  PRODUCTION_RULES,
                  "production",
                  call.connectorId,
                  call.tool,
                  call.args,
                )}
              />
            </div>
          </div>
        ))}
      </div>

      <p className="verdict-foot">
        The agent version is identical on both sides. Only the environment differs, so the answer
        does too. Change one argument and it changes again: the same <code>email.send</code>{" "}
        addressed to <code>billing@meridianlabs.dev</code> is allowed in both, because that domain
        is on the allowlist.
      </p>
    </section>
  );
}
