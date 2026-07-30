import type { Metadata } from "next";
import Link from "next/link";
import { PolicyVerdict } from "@/components/PolicyVerdict";
import { SiteFooter, SiteHeader } from "@/components/SiteChrome";
import {
  IconAlert,
  IconArrowUpRight,
  IconBot,
  IconCheck,
  IconHand,
  IconLayers,
  IconPlay,
  IconShieldCheck,
} from "@/components/icons";
import { CONNECTORS } from "@/server/connectors";
import { SEED_POLICIES } from "@/server/policies";
import { CLOSED_WON_SCENARIOS } from "@/server/scenarios";
import { TEMPLATES } from "@/server/templates";
import "./marketing.css";

// Counts come from the same modules the running product uses, so this page
// cannot drift away from the workspace it describes.
const POLICY_RULES = SEED_POLICIES.length;
const GATED_RULES = SEED_POLICIES.filter((r) => r.effect === "require_approval").length;
const SCENARIOS = CLOSED_WON_SCENARIOS.length;
const ASSERTIONS = CLOSED_WON_SCENARIOS.reduce((total, s) => total + s.assertions.length, 0);
const AGENTS = TEMPLATES.length;
const CONNECTOR_COUNT = CONNECTORS.length;
const PAUSING_SCENARIOS = CLOSED_WON_SCENARIOS.filter((s) =>
  s.assertions.some((a) => a.type === "run_state" && a.expect === "paused_pending_approval"),
).length;
const TOOLS = CONNECTORS.reduce((total, c) => total + c.tools.length, 0);

const siteUrl = process.env.NEXT_PUBLIC_SITE_URL ?? "https://shark-app-dj8b4.ondigitalocean.app";

export const metadata: Metadata = {
  title: "Convoy Labs — The control plane for company agents",
  description:
    "Create a versioned agent, grant it only the tools its environment allows, test that exact version against real system state, and promote it behind a gate. Every action passes a policy gateway the agent cannot route around.",
  alternates: { canonical: "/" },
  openGraph: {
    type: "website",
    url: "/",
    title: "Convoy Labs — The control plane for company agents",
    description:
      "Agents that belong to the company, not to a login. The environment decides what an agent may touch, and consequential actions stop for a human.",
  },
};

const softwareSchema = {
  "@context": "https://schema.org",
  "@type": "SoftwareApplication",
  name: "Convoy Labs",
  applicationCategory: "BusinessApplication",
  operatingSystem: "Web",
  url: siteUrl,
  description:
    "A control plane for company-owned AI agents. Agents are versioned, permissioned per environment, tested against system state before promotion, and supervised through human approvals and an append-only audit trail.",
  featureList: [
    "Environment-scoped permission policies evaluated on every tool call",
    "Agents never hold credentials; the gateway injects them",
    "Scenario suite asserted against real system state",
    "Promotion blocked unless the suite is green on that exact version",
    "Policy-gated and agent-flagged approvals in one queue",
    "Ordered per-run trace and an append-only audit log",
    "Fleet-wide kill switch re-checked on every action",
  ],
};

const faqSchema = {
  "@context": "https://schema.org",
  "@type": "FAQPage",
  mainEntity: [
    {
      "@type": "Question",
      name: "Is there an actual model in here, or is the agent a script?",
      acceptedAnswer: {
        "@type": "Answer",
        text: "Both modes exist and share the same governance layer. By default the demo runs a deterministic planner, so a walkthrough never depends on a network call. Set ANTHROPIC_API_KEY and CONVOY_LIVE_MODEL=1 and the same agent runs as a real Claude tool-use loop that sees only gateway-governed tools plus the built-in escalation tool. The gateway does not trust the model in either case.",
      },
    },
    {
      "@type": "Question",
      name: "What exactly does the gateway check, and in what order?",
      acceptedAnswer: {
        "@type": "Answer",
        text: "Kill switch, escalation short-circuit, tool grant on the version, whether the name resolves to a connector at all, whether a healthy instance of that connector is attached to this environment, then the permission rule for that environment, connector and tool including any conditions on the arguments. Only a call that survives all six gets credentials and executes. A trace row is written on every branch, refusals included.",
      },
    },
    {
      "@type": "Question",
      name: "What stops the tests passing against something the agent wrote itself?",
      acceptedAnswer: {
        "@type": "Answer",
        text: "Assertions read system state rather than the agent's summary: the CRM field, the document and its contents, the outbox, the channel. Several assertions check absence — that no invoice went out and no order form exists — which a confident summary cannot fake.",
      },
    },
    {
      "@type": "Question",
      name: "Can a version be promoted without being tested?",
      acceptedAnswer: {
        "@type": "Answer",
        text: "No. Promotion computes a pre-flight diff keyed to the agent version rather than the agent, and refuses when no suite exists or the suite is not green on that exact version.",
      },
    },
    {
      "@type": "Question",
      name: "How is this different from an approval step in a workflow tool?",
      acceptedAnswer: {
        "@type": "Answer",
        text: "A workflow tool gates a branch someone drew in advance, so it can only stop what that person anticipated. Here the gate sits on the tool call, covering actions the author never thought about. In Production a tool with no matching rule requires approval; in Sandbox it is allowed. Unstated actions fail toward a human.",
      },
    },
  ],
};

export default function LandingPage() {
  return (
    <div className="site">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <SiteHeader withSectionNav />

      {/* tabIndex -1 so the skip link actually moves focus, not just the scroll
          position (WCAG 2.4.1). */}
      <main id="main" tabIndex={-1}>
        {/* ---------------- hero ---------------- */}
        <section className="hero" aria-labelledby="hero-title">
          <div className="wrap hero-grid">
            <div className="hero-copy">
              <p className="eyebrow">
                <span className="eyebrow-rule" aria-hidden="true" />
                Control plane for company agents
              </p>
              <h1 className="display" id="hero-title">
                An agent can reason about the work. It shouldn&rsquo;t decide what it&rsquo;s
                allowed to touch.
              </h1>
              <p className="lede">
                Convoy Labs is where a company creates a versioned agent, grants it only the tools
                a given environment allows, tests that exact version against real system state, and
                promotes it behind a gate. Every action it takes goes through a policy gateway the
                agent has no way around.
              </p>

              <div className="hero-actions">
                <Link className="btn btn-primary" href="/start">
                  Start the guided walkthrough <IconArrowUpRight size={16} />
                </Link>
                <Link className="btn" href="/dashboard">
                  Open the sample workspace
                </Link>
              </div>

              <dl className="hero-facts">
                <div>
                  <dt>Status</dt>
                  <dd>
                    Working proof of concept. The governance lifecycle runs; the systems it acts on
                    are simulations you can open and read.
                  </dd>
                </div>
                <div>
                  <dt>Access</dt>
                  <dd>
                    No signup, no API key, no install. The walkthrough takes about eight minutes.
                  </dd>
                </div>
                <div>
                  <dt>Workspace</dt>
                  <dd>
                    Meridian Labs — a fictional revenue-operations team with two environments,{" "}
                    {AGENTS} agents, and one night of history.
                  </dd>
                </div>
              </dl>
            </div>

            <PolicyVerdict />
          </div>
        </section>

        {/* ---------------- the problem ---------------- */}
        <section className="band band-ink" id="problem" aria-labelledby="problem-title">
          <div className="wrap">
            <div className="band-head">
              <p className="eyebrow">
                <span className="eyebrow-rule" aria-hidden="true" />
                The problem
              </p>
              <h2 className="h2" id="problem-title">
                Every company already has agents. None of them belong to the company.
              </h2>
              <p className="lede">
                People have been running assistants on their own logins since the first useful ones
                shipped. They inherit one person&rsquo;s access, keep their history in one
                person&rsquo;s account, and leave when that person does. For helping someone think,
                that is fine. For doing the company&rsquo;s work — writing to the CRM, sending the
                invoice, publishing the page — it is the wrong shape.
              </p>
            </div>

            <div className="band-body">
              <div className="contrast-grid">
                <div className="contrast-col">
                  <header>
                    <span className="contrast-tag">Personal agent</span>
                  </header>
                  <h3>Borrowed authority, no record</h3>
                  <ul className="contrast-list">
                    <li>
                      <dfn>Identity</dfn>
                      <span>Runs as an employee, on that employee&rsquo;s login.</span>
                    </li>
                    <li>
                      <dfn>Access</dfn>
                      <span>Whatever that person can already reach, which nobody enumerated.</span>
                    </li>
                    <li>
                      <dfn>Change</dfn>
                      <span>A prompt someone edited this morning. No version, no diff.</span>
                    </li>
                    <li>
                      <dfn>Oversight</dfn>
                      <span>A chat log in a personal account.</span>
                    </li>
                    <li>
                      <dfn>Stopping it</dfn>
                      <span>Ask them to close the tab.</span>
                    </li>
                  </ul>
                </div>

                <div className="contrast-col is-primary">
                  <header>
                    <span className="contrast-tag">Company agent</span>
                  </header>
                  <h3>Granted authority, on the record</h3>
                  <ul className="contrast-list">
                    <li>
                      <dfn>Identity</dfn>
                      <span>Belongs to the workspace and outlives whoever created it.</span>
                    </li>
                    <li>
                      <dfn>Access</dfn>
                      <span>
                        Only the tools its environment grants, written out rule by rule and checked
                        on every call.
                      </span>
                    </li>
                    <li>
                      <dfn>Change</dfn>
                      <span>A numbered version that passed its suite before anything promoted it.</span>
                    </li>
                    <li>
                      <dfn>Oversight</dfn>
                      <span>
                        An ordered trace of every call and its verdict, with an append-only audit
                        log behind it.
                      </span>
                    </li>
                    <li>
                      <dfn>Stopping it</dfn>
                      <span>
                        A fleet switch re-checked on every action, so a paused agent halts mid-run.
                      </span>
                    </li>
                  </ul>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* ---------------- one run ---------------- */}
        <section className="band band-paper" id="run" aria-labelledby="run-title">
          <div className="wrap run-grid">
            <div className="run-copy">
              <p className="eyebrow">
                <span className="eyebrow-rule" aria-hidden="true" />
                One run
              </p>
              <h2 className="h2" id="run-title">
                Two stops in one run, and neither knew about the other.
              </h2>
              <p className="prose">
                A deal moves to Closed-Won in Production: Northwind Systems, USD 87,700, a 22%
                discount, and a billing contact at an outside domain. The Closed-Won Paperwork agent
                reads the deal and the pricing policy, checks for duplicates, and starts assembling
                the paperwork. It gets stopped twice, for unrelated reasons.
              </p>

              <ul className="run-stops">
                <li className="stop-agent">
                  <i aria-hidden="true">04</i>
                  <div>
                    <strong>The agent stops itself</strong>
                    <p>
                      Meridian&rsquo;s pricing policy leaves 15% to an account executive&rsquo;s
                      discretion. This deal is at 22% with no exception on file, so the agent calls{" "}
                      <span className="code">flag_for_review</span> with the number, the standard it
                      exceeded, and what it proposes to do next.
                    </p>
                  </div>
                </li>
                <li className="stop-policy">
                  <i aria-hidden="true">09</i>
                  <div>
                    <strong>The environment stops the agent</strong>
                    <p>
                      The invoice is addressed outside{" "}
                      <span className="code">meridianlabs.dev</span>, so the Production rule holds
                      it for a named approver, whatever the agent itself concluded.
                    </p>
                  </div>
                </li>
              </ul>

              <p className="prose" style={{ marginTop: 22 }}>
                The two mechanisms do not depend on each other. Take away the agent&rsquo;s
                judgment and the policy gate still fires. Loosen the policy and the agent still
                escalates. That redundancy is the design. It is not a property of this one deal.
              </p>
            </div>

            <figure className="trace-panel">
              <figcaption className="trace-panel-head">
                <div>
                  <strong>run · agent_closed_won_paperwork</strong>
                  <p className="micro">Production · triggered by deal.stage_changed → closedwon</p>
                </div>
                <span className="badge badge-warning">
                  <span className="dot" />
                  Awaiting approval
                </span>
              </figcaption>

              <ol className="trace-list">
                <TraceRow seq="01" tool="crm.read_deal" note="deal_prod_northwind" />
                <TraceRow seq="02" tool="docs.get" note="Pricing &amp; Discount Policy" />
                <TraceRow seq="03" tool="crm.search_deals" note="company: Northwind Systems" />
                <TraceRow
                  seq="04"
                  tool="flag_for_review"
                  note="Discount is 22% against a 15% standard and no exception doc found."
                  verdict="Agent-flagged"
                  gate
                />
                <TraceRow seq="05" tool="docs.create" note="Order Form — Northwind Systems — Platform (85 seats)" />
                <TraceRow seq="06" tool="crm.update_deal" note="paperwork_status: complete" />
                <TraceRow seq="07" tool="docs.create" note="Kickoff Checklist — Northwind Systems — Platform (85 seats)" />
                <TraceRow seq="08" tool="chat.post" note="#revops" />
                <TraceRow
                  seq="09"
                  tool="email.send"
                  note="to: ap@northwindsystems.com — outside the allowlist, held for revops_lead"
                  verdict="Policy gate"
                  gate
                  policy
                />
              </ol>

              <p className="trace-panel-foot">
                The seeded Production path, drawn from the run this agent actually produces. Trigger
                it yourself from{" "}
                <Link className="code code-link" href="/systems?env=env_production&amp;tab=crm">
                  /systems
                </Link>{" "}
                and the trace streams live, or read a completed one at{" "}
                <Link className="code code-link" href="/runs/run_hist_1">
                  /runs/run_hist_1
                </Link>
                .
              </p>
            </figure>
          </div>
        </section>

        {/* ---------------- the gateway ---------------- */}
        <section className="band" id="gateway" aria-labelledby="gateway-title">
          <div className="wrap">
            <div className="band-head">
              <p className="eyebrow">
                <span className="eyebrow-rule" aria-hidden="true" />
                The mechanism
              </p>
              <h2 className="h2" id="gateway-title">
                One choke point, six checks, a trace row on every branch.
              </h2>
              <p className="lede">
                The runtime never touches a credential or an external system. It emits an abstract
                tool call — a name and some arguments — and hands it over. The gateway resolves the
                run to its deployment and environment, runs the checks below in order, injects the
                environment&rsquo;s credentials only if the call survives all of them, executes,
                and writes the trace before it returns. Refusals and kill-switch stops are traced
                too.
              </p>
            </div>

            <div className="band-body">
              <div className="gateway">
                <div className="gw-node">
                  <span>Agent runtime</span>
                  <h3>Reasons, then asks</h3>
                  <p>
                    A deterministic planner by default, or a live model tool loop. Either way it
                    holds no credentials and reaches no system directly.
                  </p>
                  <p className="gw-note">emits: {"{ tool, args }"}</p>
                </div>

                <Arrow />

                <div className="gw-node gw-core">
                  <span>Policy gateway</span>
                  <h3>Decides, credentials, records</h3>
                  <ol className="gw-checks">
                    <li>
                      <i>01</i>
                      <span>
                        <b>Kill switch.</b> Is this agent paused fleet-wide? A pause takes effect at
                        its next action, mid-run.
                      </span>
                    </li>
                    <li>
                      <i>02</i>
                      <span>
                        <b>Escalation.</b> Is this the built-in review request? Every agent has it,
                        in every environment, without a grant.
                      </span>
                    </li>
                    <li>
                      <i>03</i>
                      <span>
                        <b>Tool grant.</b> Did this agent <em>version</em> declare this tool? The
                        version, not the agent.
                      </span>
                    </li>
                    <li>
                      <i>04</i>
                      <span>
                        <b>Known tool.</b> Does the name resolve to a connector at all? An
                        unrecognised one is refused, not ignored.
                      </span>
                    </li>
                    <li>
                      <i>05</i>
                      <span>
                        <b>Connector.</b> Is a healthy instance of that connector attached to this
                        environment?
                      </span>
                    </li>
                    <li>
                      <i>06</i>
                      <span>
                        <b>Policy.</b> What does the rule for this environment, connector and tool
                        say — including its conditions on the arguments?
                      </span>
                    </li>
                  </ol>
                  <p className="gw-after">
                    Only then: inject the environment&rsquo;s credential handle, execute, write the
                    trace row.
                  </p>
                </div>

                <Arrow />

                <div className="gw-node">
                  <span>Three outcomes</span>
                  <h3>Every call ends in one</h3>
                  <ul className="gw-outputs">
                    <li>
                      <i className="dot-allow" aria-hidden="true" />
                      Executed against the system
                    </li>
                    <li>
                      <i className="dot-review" aria-hidden="true" />
                      Held for a named approver
                    </li>
                    <li>
                      <i className="dot-deny" aria-hidden="true" />
                      Refused, with the reason
                    </li>
                  </ul>
                  <p className="gw-note">
                    {POLICY_RULES} rules ship with the sample workspace. {GATED_RULES} of them hold
                    an action for a human.
                  </p>
                </div>
              </div>

              <div className="gw-invariant">
                <span>The invariant</span>
                <p>
                  <b>Agents never hold credentials.</b>{" "}
                  A tool grant is a request, not an authority
                  — the environment decides. That is why one version behaves differently in Sandbox
                  and Production, and why editing an agent&rsquo;s instructions cannot widen what it
                  can reach. In Production, a tool with <b>no matching rule requires approval</b>;
                  in Sandbox it is allowed. Unstated actions fail toward review.
                </p>
              </div>
            </div>
          </div>
        </section>

        {/* ---------------- lifecycle ---------------- */}
        <section className="band band-sunken" id="lifecycle" aria-labelledby="lifecycle-title">
          <div className="wrap">
            <div className="band-head">
              <p className="eyebrow">
                <span className="eyebrow-rule" aria-hidden="true" />
                The lifecycle
              </p>
              <h2 className="h2" id="lifecycle-title">
                Company agents get the same treatment as company software.
              </h2>
              <p className="lede">
                Create, test, promote, supervise. The steps most agent tooling leaves to convention
                are the ones enforced here.
              </p>
            </div>

            <div className="band-body">
              <div className="lifecycle">
                <article>
                  <div className="lifecycle-top">
                    <span>01</span>
                    <i aria-hidden="true">
                      <IconBot size={17} />
                    </i>
                  </div>
                  <h3>Create</h3>
                  <p>
                    An agent is a versioned asset: instructions, parameters, a trigger, and the list
                    of tools it needs from its environment. From a template or from scratch.
                  </p>
                  <p className="lifecycle-proof">
                    <b>In the sample</b>
                    {AGENTS} agents · {TOOLS} tools across {CONNECTOR_COUNT} connectors
                  </p>
                </article>

                <article>
                  <div className="lifecycle-top">
                    <span>02</span>
                    <i aria-hidden="true">
                      <IconPlay size={17} />
                    </i>
                  </div>
                  <h3>Test</h3>
                  <p>
                    A scenario suite drives the real agent through the real gateway in Sandbox.
                    Assertions read the CRM record, the document, the outbox — never the
                    agent&rsquo;s own summary.
                  </p>
                  <p className="lifecycle-proof">
                    <b>In the sample</b>
                    {SCENARIOS} scenarios · {ASSERTIONS} assertions, including absence checks
                  </p>
                </article>

                <article>
                  <div className="lifecycle-top">
                    <span>03</span>
                    <i aria-hidden="true">
                      <IconLayers size={17} />
                    </i>
                  </div>
                  <h3>Promote</h3>
                  <p>
                    A pre-flight diff shows which credentials swap, which rules tighten, and whether
                    the suite is green on this exact version. It refuses if it is not.
                  </p>
                  <p className="lifecycle-proof">
                    <b>Try to break it</b>
                    An agent with no suite cannot reach Production
                  </p>
                </article>

                <article>
                  <div className="lifecycle-top">
                    <span>04</span>
                    <i aria-hidden="true">
                      <IconShieldCheck size={17} />
                    </i>
                  </div>
                  <h3>Supervise</h3>
                  <p>
                    Both kinds of pause land in one queue. Approve and the run resumes where it
                    stopped; reject and it ends, with the reason recorded against the run.
                  </p>
                  <p className="lifecycle-proof">
                    <b>Where to look</b>
                    /approvals · /audit · the fleet switch on /dashboard
                  </p>
                </article>
              </div>
            </div>
          </div>
        </section>

        {/* ---------------- fit ---------------- */}
        <section className="band" aria-labelledby="fit-title">
          <div className="wrap">
            <div className="band-head">
              <p className="eyebrow">
                <span className="eyebrow-rule" aria-hidden="true" />
                Where this belongs
              </p>
              <h2 className="h2" id="fit-title">
                Routine in shape, fuzzy in content.
              </h2>
              <p className="lede">
                One end of company work is fully scriptable, and code already owns it. The other end
                needs voice, relationships and taste, and should stay with people. Convoy Labs is built
                for the band between them — work too variable for an if-statement and too routine to
                deserve someone&rsquo;s afternoon.
              </p>
            </div>

            <div className="band-body">
              <div className="spectrum">
                <div className="spectrum-cell">
                  <span>Already solved</span>
                  <h3>Deterministic automation</h3>
                  <p>
                    Scripts, RPA and workflow builders own the fully specifiable end, and break the
                    moment the input varies.
                  </p>
                </div>
                <div className="spectrum-cell is-ours">
                  <span>Convoy Labs</span>
                  <h3>Routine work a model can read</h3>
                  <p>
                    Reading a closed deal and producing its paperwork. Researching a lead.
                    Reconciling records between systems. Keeping documentation current. The model
                    makes the small decisions; the environment decides what it may execute.
                  </p>
                </div>
                <div className="spectrum-cell">
                  <span>Stays human</span>
                  <h3>Judgment and taste</h3>
                  <p>
                    Anything where the voice, the relationship or the creative call is the product.
                    A boundary, not a roadmap item.
                  </p>
                </div>
              </div>

              <ul className="rubric">
                <li>
                  <span>01</span>
                  <h3>Routine</h3>
                  <p>The task recurs with the same shape. No novel judgment per instance.</p>
                </li>
                <li>
                  <span>02</span>
                  <h3>Taste-free</h3>
                  <p>Quality means correctness, not voice or relationship.</p>
                </li>
                <li>
                  <span>03</span>
                  <h3>Verifiable</h3>
                  <p>Success is checkable against system state, which is what makes tests possible.</p>
                </li>
                <li>
                  <span>04</span>
                  <h3>Systems of record</h3>
                  <p>The work is reads and writes against CRM, billing, docs, email, ticketing.</p>
                </li>
                <li>
                  <span>05</span>
                  <h3>Volume-bearing</h3>
                  <p>Enough instances per month that a fleet beats a hire.</p>
                </li>
              </ul>

              <div className="exclusion">
                <IconAlert size={17} />
                <p>
                  <b>Deliberately out of scope.</b> Marketing content, outbound sales messaging,
                  support conversations that need empathy, and anything brand-voice sensitive. One
                  bad write multiplied across a fleet is the failure mode this product exists to
                  prevent, so the scope stays narrow on purpose.
                </p>
              </div>
            </div>
          </div>
        </section>

        {/* ---------------- what's real ---------------- */}
        <section className="band band-sunken" id="reality" aria-labelledby="reality-title">
          <div className="wrap">
            <div className="band-head">
              <p className="eyebrow">
                <span className="eyebrow-rule" aria-hidden="true" />
                What&rsquo;s real
              </p>
              <h2 className="h2" id="reality-title">
                No customers, no logos, no benchmark to quote. Here is the inventory instead.
              </h2>
              <p className="lede">
                This is a proof of concept, and it is more useful to you if we are exact about which
                parts are load-bearing. Everything in the first column runs when you click through
                it.
              </p>
            </div>

            <div className="band-body">
              <div className="reality">
                <article>
                  <span className="reality-badge reality-shipped">
                    <IconCheck size={12} /> Implemented
                  </span>
                  <h3>The governance lifecycle</h3>
                  <p>Runs end to end in the workspace linked from this page.</p>
                  <ul>
                    <li>Policy gateway with per-call evaluation and tool-grant enforcement</li>
                    <li>Two environments with their own connector instances and rule sets</li>
                    <li>Scenario suite asserted against system state</li>
                    <li>Promotion gate keyed to the agent version</li>
                    <li>Approval pause, resume and reject — both gate kinds in one queue</li>
                    <li>Ordered per-run trace streamed over server-sent events</li>
                    <li>Append-only audit log with causal references</li>
                    <li>Fleet kill switch, re-checked on every call</li>
                  </ul>
                </article>

                <article>
                  <span className="reality-badge reality-sim">
                    <IconHand size={12} /> Simulated
                  </span>
                  <h3>The systems on the other side</h3>
                  <p>
                    Database-backed stand-ins carrying the real tool manifests, browsable at{" "}
                    <Link className="code code-link" href="/systems">
                      /systems
                    </Link>
                    .
                  </p>
                  <ul>
                    <li>HubSpot, Gmail, Google Docs and Slack are simulations, not integrations</li>
                    <li>
                      Credential handles are opaque strings such as{" "}
                      <code>vault://production/hubspot</code> — there is no vault behind them
                    </li>
                    <li>Connector health is a fixed value; nothing probes it</li>
                    <li>Meridian Labs and every person, company and deal in it are invented</li>
                    <li>
                      The default engine is a deterministic planner; the live model loop is real but
                      off unless configured
                    </li>
                  </ul>
                </article>

                <article>
                  <span className="reality-badge reality-planned">Not built yet</span>
                  <h3>What we are not claiming</h3>
                  <p>Named here so nothing above has to be read charitably.</p>
                  <ul>
                    <li>No SSO or role-based access control; approver identity is not verified</li>
                    <li>No secret vault, OAuth flow or real MCP transport</li>
                    <li>No approval timeouts — the rules carry the field, nothing enforces it</li>
                    <li>No rate limits, per-run budgets or rollback of a promoted version</li>
                    <li>No multi-tenant isolation: one workspace, one shared store, one replica</li>
                    <li>No compliance attestation, uptime history or independent benchmark</li>
                  </ul>
                </article>
              </div>
            </div>
          </div>
        </section>

        {/* ---------------- start here ---------------- */}
        <section className="band band-paper" id="verify" aria-labelledby="verify-title">
          <div className="wrap">
            <div className="band-head">
              <p className="eyebrow">
                <span className="eyebrow-rule" aria-hidden="true" />
                Start here
              </p>
              <h2 className="h2" id="verify-title">
                Go and check. Every claim above has a page behind it.
              </h2>
              <p className="lede">
                The{" "}
                <Link className="text-link" href="/start">
                  guided walkthrough
                </Link>{" "}
                takes about eight minutes and covers all three of the routes below, in order. If you
                would rather go straight to the part you care about, start where you already have
                questions.
              </p>
            </div>

            <div className="band-body">
              <div className="lanes">
                <Link className="lane" href="/environments">
                  <span className="lane-role">IT &amp; security</span>
                  <h3>Read the rules of the road</h3>
                  <p>
                    Two environments side by side: connector instances, credential handles, and the
                    full rule list with its approvers, conditions and timeouts.
                  </p>
                  <ul className="lane-steps">
                    <li>
                      <b>Look for</b>
                      <span>External email: refused in Sandbox, held in Production</span>
                    </li>
                  </ul>
                  <span className="lane-go">
                    Open environments <IconArrowUpRight size={15} />
                  </span>
                </Link>

                <Link className="lane" href="/agents/agent_closed_won_paperwork">
                  <span className="lane-role">Operations</span>
                  <h3>Inspect one agent completely</h3>
                  <p>
                    Its trigger, declared tool grants, parameters, both deployments, and the{" "}
                    {SCENARIOS} scenarios it has to survive before it can be promoted.
                  </p>
                  <ul className="lane-steps">
                    <li>
                      <b>Look for</b>
                      <span>What it does when the PO number is missing</span>
                    </li>
                  </ul>
                  <span className="lane-go">
                    Open the agent <IconArrowUpRight size={15} />
                  </span>
                </Link>

                <Link className="lane" href="/runs/run_hist_1">
                  <span className="lane-role">Technical evaluation</span>
                  <h3>Read a trace end to end</h3>
                  <p>
                    Every call in sequence with its arguments, its result, the policy verdict that
                    produced it, and how long it took.
                  </p>
                  <ul className="lane-steps">
                    <li>
                      <b>Look for</b>
                      <span>The approval row, and who the decision is attributed to</span>
                    </li>
                  </ul>
                  <span className="lane-go">
                    Open a run trace <IconArrowUpRight size={15} />
                  </span>
                </Link>
              </div>
            </div>
          </div>
        </section>

        {/* ---------------- FAQ ---------------- */}
        <section className="band" id="faq" aria-labelledby="faq-title">
          <div className="wrap faq-grid">
            <div>
              <p className="eyebrow">
                <span className="eyebrow-rule" aria-hidden="true" />
                Objections
              </p>
              <h2 className="h2" id="faq-title" style={{ marginTop: 14 }}>
                The questions that come after &ldquo;what is it.&rdquo;
              </h2>
              <p className="prose" style={{ marginTop: 18 }}>
                Answered against what the code does, including where the answer is unflattering.
              </p>
            </div>

            <div className="faq-list">
              <details>
                <summary>Is there an actual model in here, or is the agent a script?</summary>
                <div>
                  <p>
                    Both modes exist and they share the same governance layer. By default the demo
                    runs a deterministic planner, so a live walkthrough never depends on a network
                    call or a model&rsquo;s mood. Set <code>ANTHROPIC_API_KEY</code> and{" "}
                    <code>CONVOY_LIVE_MODEL=1</code> and the same agent runs as a real Claude
                    tool-use loop that sees only gateway-governed tools plus the escalation tool,
                    with its message history persisted per run so a paused run survives a restart.
                  </p>
                  <p>
                    That the two are interchangeable is the argument rather than a shortcut: the
                    gateway does not trust the model in either case. This deployment runs the
                    deterministic planner.
                  </p>
                </div>
              </details>

              <details>
                <summary>What exactly does the gateway check, and in what order?</summary>
                <div>
                  <p>
                    Kill switch, escalation short-circuit, tool grant on the version, whether the
                    name resolves to a connector at all, whether a healthy instance of that
                    connector is attached to this environment, then the permission rule for that
                    environment, connector and tool including its conditions. Only a call that
                    survives all six gets credentials and executes.
                  </p>
                  <p>
                    A trace row is written on every branch — refusals, kill-switch stops and
                    approval holds included. <code>callTool</code> in <code>src/server/gateway.ts</code> is about fifty
                    lines, which is deliberate: the whole trust
                    boundary should fit on one screen.
                  </p>
                </div>
              </details>

              <details>
                <summary>What stops the tests passing against something the agent wrote itself?</summary>
                <div>
                  <p>
                    Assertions read system state rather than the agent&rsquo;s summary: the CRM
                    field, the document and its contents, the outbox, the channel. Of the{" "}
                    {ASSERTIONS} assertions across the {SCENARIOS}-scenario suite, several check
                    absence — that no invoice went out and no order form exists — which a confident
                    summary cannot fake.
                  </p>
                  <p>
                    {PAUSING_SCENARIOS} of them assert that the run <em>paused</em>, with a
                    specific escalation reason. Stopping correctly is graded the same way finishing
                    is.
                  </p>
                </div>
              </details>

              <details>
                <summary>Can a version be promoted without being tested?</summary>
                <div>
                  <p>
                    No, and it is worth trying. Promotion computes a pre-flight diff keyed to the
                    agent version — not the agent — and refuses when no suite exists or the suite is
                    not green on that exact version. Three of the four sample agents have no suite,
                    so their promotion is blocked with the reason stated.
                  </p>
                </div>
              </details>

              <details>
                <summary>How is this different from an approval step in a workflow tool?</summary>
                <div>
                  <p>
                    A workflow tool gates a branch that someone drew in advance, which means it can
                    only stop what that person anticipated. Here the gate sits on the tool call, so
                    it covers actions the agent&rsquo;s author never thought about.
                  </p>
                  <p>
                    The defaults carry the difference: in Production a tool with no matching rule
                    requires approval, while in Sandbox it is allowed. An action nobody wrote a rule
                    for fails toward a human rather than toward execution.
                  </p>
                </div>
              </details>

              <details>
                <summary>What happens to a paused run if the process dies?</summary>
                <div>
                  <p>
                    Run state, the trace and pending approvals are persisted, and the live-model
                    engine also persists per-run message history, so an approval decided after a
                    restart resumes the run correctly.
                  </p>
                  <p>
                    The honest half: the demo store is a JSON file, there is no work queue, no retry
                    policy and no leader election, and the process runs single-replica because the
                    event bus is in-process. Postgres is the documented swap point and the table
                    shapes already match.
                  </p>
                </div>
              </details>

              <details>
                <summary>What is the blast radius, and how fast can I stop it?</summary>
                <div>
                  <p>
                    In Sandbox it is bounded by construction — different credentials, different
                    rules, and external email refused outright. In Production the consequential
                    actions hold for a human and everything else is recorded. The kill switch is per
                    agent and is re-checked on every call, so a pause halts a run already in flight
                    rather than only preventing the next one.
                  </p>
                  <p>
                    What is missing: rate limits, per-run budgets, and rollback of a promoted
                    version. And the interesting failure mode was never the rogue action — it is the
                    permitted-but-wrong one, which is why the test harness matters at least as much
                    as the gate.
                  </p>
                </div>
              </details>

              <details>
                <summary>Who can approve, and can someone approve their own action?</summary>
                <div>
                  <p>
                    A rule names its approvers, and the decision is written to the trace and the
                    audit log. But the approver identity is supplied by the client and not verified,
                    and there is no separation-of-duties check. Binding an approval to a verified
                    enterprise identity is the top item on the roadmap. Until it lands, treat this
                    as a demonstration of the mechanism rather than a control you could attest to.
                  </p>
                </div>
              </details>
            </div>
          </div>
        </section>

        {/* ---------------- closer ---------------- */}
        <section className="band" style={{ paddingTop: 0 }} aria-labelledby="closer-title">
          <div className="wrap">
            <div className="closer">
              <div>
                <p className="eyebrow">
                  <span className="eyebrow-rule" aria-hidden="true" />
                  Eight minutes
                </p>
                <h2 id="closer-title">
                  Follow one agent from Sandbox to Production and watch it get stopped twice.
                </h2>
                <p>
                  Seven steps through the sample workspace: compare the two environments, inspect
                  the agent, run it where mistakes are cheap, earn the promotion, then watch
                  Production hold two decisions for a human. No signup, no API key, no install.
                </p>
              </div>
              <div className="closer-actions">
                <Link className="btn btn-primary" href="/start">
                  Start the walkthrough <IconArrowUpRight size={15} />
                </Link>
                <Link className="btn" href="/runs/run_hist_1">
                  Read a run trace first
                </Link>
              </div>
            </div>
          </div>
        </section>
      </main>

      <SiteFooter />

      {/* Structured data describes only what the product does. No rating,
          price or review is asserted, because none exists. */}
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(softwareSchema) }}
      />
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(faqSchema) }}
      />
    </div>
  );
}

function Arrow() {
  return (
    <div className="gw-arrow" aria-hidden="true">
      <svg width="26" height="14" viewBox="0 0 26 14" fill="none" stroke="currentColor" strokeWidth="1.6">
        <path d="M0 7h23" />
        <path d="m18 2 5 5-5 5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </div>
  );
}

function TraceRow({
  seq,
  tool,
  note,
  verdict = "Executed",
  gate = false,
  policy = false,
}: {
  seq: string;
  tool: string;
  note: string;
  verdict?: string;
  gate?: boolean;
  policy?: boolean;
}) {
  return (
    <li className={`${gate ? "is-gate" : ""}${policy ? " is-policy" : ""}`}>
      <i aria-hidden="true">{seq}</i>
      <div className="trace-main">
        <code>{tool}</code>
        <em>{verdict}</em>
        <small>{note}</small>
      </div>
    </li>
  );
}
