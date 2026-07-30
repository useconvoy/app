import type { Metadata } from "next";
import Link from "next/link";
import { MarketingFooter, MarketingHeader } from "@/components/MarketingChrome";
import { PolicyPreview } from "@/components/PolicyPreview";
import {
  IconAlert,
  IconArrowUpRight,
  IconBot,
  IconCheck,
  IconFlag,
  IconLayers,
  IconList,
  IconPause,
  IconPlay,
  IconShieldCheck,
} from "@/components/icons";

export const metadata: Metadata = {
  title: "Convoy Labs — The control plane for company agents",
  description:
    "Create, permission, test, promote, and supervise company-owned agents for routine work—without handing away human judgment.",
  alternates: { canonical: "/" },
};

const lifecycle = [
  {
    number: "01",
    title: "Create",
    text: "Define a versioned company agent with instructions, a trigger, and only the tools it needs.",
    icon: <IconBot size={18} />,
  },
  {
    number: "02",
    title: "Test",
    text: "Run it in Sandbox. Check outputs against system state across repeatable scenarios.",
    icon: <IconPlay size={18} />,
  },
  {
    number: "03",
    title: "Promote",
    text: "Review the credential, policy, and exact-version test diff before Production.",
    icon: <IconLayers size={18} />,
  },
  {
    number: "04",
    title: "Supervise",
    text: "Approve consequential actions, inspect causal traces, and stop the fleet at any time.",
    icon: <IconShieldCheck size={18} />,
  },
];

const fit = [
  ["Routine", "The task repeats with the same general shape."],
  ["Taste-free", "Correctness matters more than voice, creativity, or relationships."],
  ["Verifiable", "Success can be checked against system state."],
  ["Systems-of-record", "The work reads or writes CRM, docs, email, billing, or similar systems."],
  ["Volume-bearing", "Enough instances recur that a fleet beats a one-off workflow."],
];

const schema = {
  "@context": "https://schema.org",
  "@type": "SoftwareApplication",
  name: "Convoy Labs",
  applicationCategory: "BusinessApplication",
  operatingSystem: "Web",
  description:
    "A control plane for creating, permissioning, testing, promoting, and supervising company-owned agents.",
  url: "https://shark-app-dj8b4.ondigitalocean.app/",
};

export default function LandingPage() {
  return (
    <div className="marketing-site">
      <a className="skip-link" href="#marketing-main">Skip to content</a>
      <MarketingHeader />
      <main id="marketing-main">
        <section className="marketing-hero">
          <div className="marketing-container hero-grid">
            <div className="hero-copy">
              <div className="hero-kicker">
                <span className="hero-kicker-line" aria-hidden="true" />
                The control plane for company agents
              </div>
              <h1>Company agents, governed from Sandbox to Production.</h1>
              <p className="hero-lede">
                Create organization-owned agents for routine work. Give them only the tools their environment allows,
                test the exact version, and keep consequential decisions on a human desk.
              </p>
              <div className="hero-actions">
                <Link className="btn btn-primary marketing-hero-button" href="/register">
                  Create your workspace <IconArrowUpRight size={16} />
                </Link>
                <Link className="btn marketing-hero-button" href="/start">
                  Explore the guided demo
                </Link>
              </div>
              <div className="hero-proof">
                <span><IconCheck size={15} /> Working governance POC</span>
                <span><IconCheck size={15} /> No credentials required</span>
                <span><IconCheck size={15} /> Fictional sample workspace</span>
              </div>
            </div>
            <PolicyPreview />
          </div>
        </section>

        <section className="thesis-strip" aria-labelledby="thesis-title">
          <div className="marketing-container">
            <div className="thesis-lead">
              <span className="marketing-section-label">A different primitive</span>
              <h2 id="thesis-title">Employees spawn personal agents. Companies need company agents.</h2>
            </div>
            <div className="thesis-grid">
              <article>
                <span className="thesis-index">Personal agent</span>
                <h3>Bound to a person</h3>
                <p>Tied to one employee&apos;s identity, credentials, history, and continuing access.</p>
              </article>
              <div className="thesis-arrow" aria-hidden="true">→</div>
              <article className="thesis-company">
                <span className="thesis-index">Company agent</span>
                <h3>Owned by the workspace</h3>
                <p>Versioned, permissioned by environment, tested before Production, and supervised as a durable asset.</p>
              </article>
            </div>
          </div>
        </section>

        <section className="marketing-section" id="how-it-works" aria-labelledby="lifecycle-title">
          <div className="marketing-container">
            <div className="marketing-section-head">
              <div>
                <span className="marketing-section-label">Agents get the SDLC</span>
                <h2 id="lifecycle-title">Move from useful prompt to accountable worker.</h2>
              </div>
              <p>
                The agent reasons over fuzzy inputs. Convoy makes the surrounding lifecycle explicit, testable, and
                interruptible.
              </p>
            </div>
            <div className="lifecycle-grid">
              {lifecycle.map((step) => (
                <article key={step.number}>
                  <div className="lifecycle-top">
                    <span>{step.number}</span>
                    <i>{step.icon}</i>
                  </div>
                  <h3>{step.title}</h3>
                  <p>{step.text}</p>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section className="marketing-section run-story-section" aria-labelledby="run-story-title">
          <div className="marketing-container run-story-grid">
            <div className="run-story-copy">
              <span className="marketing-section-label">One run, two kinds of judgment</span>
              <h2 id="run-story-title">The agent knows when to ask. The environment does not care whether it knows.</h2>
              <p>
                In the Meridian Labs sample, a Closed-Won deal triggers paperwork across CRM, Docs, Email, and Chat.
                A 22% discount causes the agent to flag itself. Later, Production policy independently stops the
                external invoice email.
              </p>
              <Link href="/runs/run_hist_1" className="marketing-text-link">
                Inspect a separate completed trace <IconArrowUpRight size={15} />
              </Link>
            </div>
            <div className="run-story-panel" aria-label="Closed-Won run sequence">
              <div className="run-story-head">
                <div>
                  <span className="preview-eyebrow">Illustrative run path · seeded policy</span>
                  <strong>Northwind Systems — Closed-Won paperwork</strong>
                </div>
                <span className="badge badge-warning"><span className="dot" />Awaiting approval</span>
              </div>
              <ol>
                <li className="done">
                  <span><IconCheck size={14} /></span>
                  <div><code>crm.read_deal</code><small>Northwind Systems · USD 87,700</small></div>
                  <em>Allowed</em>
                </li>
                <li className="gate">
                  <span><IconFlag size={14} /></span>
                  <div><code>flag_for_review</code><small>22% discount exceeds 15% standard</small></div>
                  <em>Agent-flagged</em>
                </li>
                <li className="done">
                  <span><IconCheck size={14} /></span>
                  <div><code>docs.create + crm.update</code><small>Order form and paperwork state</small></div>
                  <em>Allowed</em>
                </li>
                <li className="gate policy">
                  <span><IconAlert size={14} /></span>
                  <div><code>email.send</code><small>External recipient · exact email shown</small></div>
                  <em>Policy gate</em>
                </li>
                <li className="pending">
                  <span><IconPause size={14} /></span>
                  <div><strong>Human decides</strong><small>Approval identity and outcome join the trace</small></div>
                  <em>Paused</em>
                </li>
              </ol>
            </div>
          </div>
        </section>

        <section className="marketing-section architecture-section" id="architecture" aria-labelledby="architecture-title">
          <div className="marketing-container">
            <div className="marketing-section-head">
              <div>
                <span className="marketing-section-label">The invariant</span>
                <h2 id="architecture-title">Agents never hold credentials.</h2>
              </div>
              <p>
                The runtime emits abstract tool calls. The gateway resolves the run, deployment, environment, grant,
                connector, credential handle, and policy before any simulated system action executes.
              </p>
            </div>
            <div className="architecture-map" role="img" aria-label="Agent runtime sends a tool call through the policy gateway, which applies environment policy and routes the action to a system of record or human approval.">
              <div className="architecture-node runtime-node">
                <span>Reason</span>
                <strong>Agent runtime</strong>
                <code>tool + arguments</code>
              </div>
              <div className="architecture-connector">
                <span>no credentials</span>
                <i aria-hidden="true">→</i>
              </div>
              <div className="architecture-node gateway-node">
                <span>Enforce</span>
                <strong><IconShieldCheck size={18} /> Policy gateway</strong>
                <div className="gateway-checks">
                  <b>Kill switch</b><b>Tool grant</b><b>Connector</b><b>Environment policy</b>
                </div>
              </div>
              <div className="architecture-connector">
                <span>credential injected</span>
                <i aria-hidden="true">→</i>
              </div>
              <div className="architecture-destinations">
                <div><span className="architecture-status allow" />Systems of record</div>
                <div><span className="architecture-status review" />Human approval</div>
                <div><span className="architecture-status deny" />Structured refusal</div>
              </div>
            </div>
            <div className="architecture-proof">
              <div><strong>Every</strong><span>live demo-triggered tool call crosses the gateway</span></div>
              <div><strong>2</strong><span>isolated sample environments</span></div>
              <div><strong>8</strong><span>Closed-Won scenarios checking state</span></div>
              <div><strong>1</strong><span>causal trace per run</span></div>
            </div>
          </div>
        </section>

        <section className="marketing-section fit-section" id="fit" aria-labelledby="fit-title">
          <div className="marketing-container fit-grid">
            <div className="fit-copy">
              <span className="marketing-section-label">Good agent work</span>
              <h2 id="fit-title">Automate the routine layer. Keep taste and relationships human.</h2>
              <p>
                Convoy is designed for work that is too variable for an if-statement and too repeatable to deserve
                constant human attention.
              </p>
              <div className="fit-exclusion">
                <strong>Deliberately out of scope</strong>
                <span>Brand-sensitive creative, outbound sales voice, empathy-heavy support, and novel judgment.</span>
              </div>
            </div>
            <ol className="fit-list">
              {fit.map(([title, text], index) => (
                <li key={title}>
                  <span>0{index + 1}</span>
                  <div><h3>{title}</h3><p>{text}</p></div>
                  <IconCheck size={17} />
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section className="marketing-section evidence-section" aria-labelledby="evidence-title">
          <div className="marketing-container">
            <div className="marketing-section-head">
              <div>
                <span className="marketing-section-label">Evidence, not theater</span>
                <h2 id="evidence-title">A transparent working proof of concept.</h2>
              </div>
              <p>
                The governance lifecycle is implemented. The external systems are simulated. The distinction matters,
                so the product makes it visible.
              </p>
            </div>
            <div className="evidence-grid">
              <article>
                <span className="evidence-icon"><IconShieldCheck size={19} /></span>
                <h3>Working in this build</h3>
                <ul>
                  <li>Policy gateway and tool-grant enforcement</li>
                  <li>Sandbox tests and version-specific promotion gate</li>
                  <li>Human approval pause, resume, and reject</li>
                  <li>Live trace, audit chain, and fleet kill switch</li>
                </ul>
              </article>
              <article>
                <span className="evidence-icon subtle"><IconList size={19} /></span>
                <h3>Simulated in this build</h3>
                <ul>
                  <li>HubSpot, Gmail, Google Docs, and Slack data</li>
                  <li>Vault credential handles and connector health</li>
                  <li>Meridian Labs, its people, activity, and outcomes</li>
                  <li>External notification and durable production storage</li>
                </ul>
              </article>
            </div>
          </div>
        </section>

        <section className="marketing-section faq-section" id="faq" aria-labelledby="faq-title">
          <div className="marketing-container faq-grid">
            <div>
              <span className="marketing-section-label">FAQ</span>
              <h2 id="faq-title">Where this fits—and where it does not.</h2>
            </div>
            <div className="faq-list">
              <details>
                <summary>Is Convoy another agent framework?</summary>
                <p>
                  No. Frameworks help build one agent. Convoy is the layer where agents become company assets:
                  permissioned, tested, promoted, supervised, and audited.
                </p>
              </details>
              <details>
                <summary>How is this different from workflow automation?</summary>
                <p>
                  Workflow tools excel when every branch can be designed in advance. Convoy targets routine work whose
                  content varies enough to require bounded model reasoning—but whose results can still be verified.
                </p>
              </details>
              <details>
                <summary>Who decides when a human reviews an action?</summary>
                <p>
                  Both sides can. The agent can self-escalate ambiguity through <code>flag_for_review</code>, while the
                  environment can require approval for a tool or condition regardless of model confidence.
                </p>
              </details>
              <details>
                <summary>Are the connected systems live?</summary>
                <p>
                  Not in this proof of concept. HubSpot, Gmail, Google Docs, and Slack are database-backed simulations
                  with real tool-shaped manifests. The policy, testing, promotion, approval, trace, and audit lifecycle
                  is the working proof.
                </p>
              </details>
              <details>
                <summary>Is this production-ready?</summary>
                <p>
                  This repository is a single-workspace POC. Real OAuth, a credential vault, durable database,
                  RBAC/SSO, tenant isolation, and production connector hardening remain product work—not hidden claims.
                </p>
              </details>
            </div>
          </div>
        </section>

        <section className="marketing-cta-section">
          <div className="marketing-container marketing-cta-card">
            <div>
              <span className="marketing-section-label">Start with the proof</span>
              <h2>Follow one company agent from trigger to audit.</h2>
              <p>Seven guided steps. Six minutes. Every route points to the working Meridian Labs sample workspace.</p>
            </div>
            <div className="marketing-cta-actions">
              <Link className="btn btn-primary marketing-hero-button" href="/start">
                Start the walkthrough <IconArrowUpRight size={16} />
              </Link>
              <Link className="btn marketing-hero-button" href="/dashboard">
                Return to the workspace
              </Link>
            </div>
          </div>
        </section>
      </main>
      <MarketingFooter />
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(schema) }} />
    </div>
  );
}
