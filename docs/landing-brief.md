# Landing and Start Here brief

## Product definition

Convoy Labs is the control plane for company-owned agents: operations teams create, permission, test, promote, and supervise LLM workers that handle routine, verifiable work against systems of record.

## Audiences

- **Primary:** RevOps and business-systems operators who choose, test, promote, and supervise agents.
- **Primary:** IT, security, and platform admins who own environments, connectors, credentials, and policies.
- **Secondary:** Technical evaluators and investors assessing the governance model and working proof of concept.

## Problem

Personal agents inherit an employee's identity, access, and history, while deterministic automation cannot handle routine work whose inputs vary. Companies need agents that belong to the organization, operate inside explicit boundaries, and pause when human judgment matters.

## Thesis

Bounded LLM reasoning becomes deployable company infrastructure when every action passes through an environment-aware policy gateway, outputs are tested before promotion, and risky or uncertain decisions pause for a human.

## Mission and values

- Hand routine work to agents without handing away human judgment.
- Make authority environment-defined; agents never own credentials.
- Test the exact version before Production.
- Escalate instead of guessing.
- Make every action attributable and interruptible.
- Stay deliberately scoped to routine, taste-free, verifiable, systems-of-record, volume-bearing work.
- Describe the proof of concept honestly: the governance lifecycle is implemented; external systems and credentials are simulated.

## Main proof points

- A policy gateway checks the kill switch, declared tool grants, connector availability, and environment rules on every tool call.
- Sandbox and Production bind the same version to different credentials and permissions.
- Policy-gated and agent-flagged reviews enter one approval queue and pause the run.
- An eight-scenario suite checks the Closed-Won agent against sandbox state; promotion is blocked without a green suite on that version.
- Runs expose inputs, outputs, policy verdicts, approval identity, and a causal audit trail.
- A fleet-wide kill switch is checked again at every action.

The Meridian Labs workspace, its activity counts, people, companies, credentials, and connected systems are fictional sample data. HubSpot, Gmail, Google Docs, and Slack are database-backed simulations in this proof of concept.

## Narrative sequence

1. Define company agents and the control problem in one viewport.
2. Contrast personal copilots with durable, organization-owned agents.
3. Place Convoy in the routine-but-fuzzy middle between scripts and human taste.
4. Demonstrate one action moving through the gateway in Sandbox and Production.
5. Show the lifecycle: create, test, promote, supervise.
6. Show the Closed-Won workflow and its two independent pauses.
7. Explain the gateway architecture and credential boundary.
8. Show the use-case rubric and explicit exclusions.
9. Route new visitors into a guided sample journey and returning visitors into the fleet dashboard.
10. Answer proof-of-concept, framework, workflow-tool, and human-control objections.

## Calls to action

- **Primary:** Explore the guided demo → `/start`
- **Secondary:** Open the sample workspace → `/dashboard`
- Contextual deep links lead to real environment, agent, trace, approval, system, and audit routes.

There is no signup, pricing, sales, install, or public documentation flow in the repository, so the landing page must not imply one.

## Visitor journeys

### New visitor

Landing thesis → non-mutating policy-gateway demonstration → guided `/start` sequence → sample workspace deep links → return to the guide with local progress preserved.

### Returning visitor

Landing or `/start` → sample fleet dashboard → operate, configure, govern, and inspect connected simulated systems through the existing application navigation.

## Start Here decision

Use a combination:

- an interactive, non-mutating mechanism demonstration on the landing page for immediate comprehension;
- a dedicated `/start` route for the cross-route eight-minute product walkthrough;
- direct access to the returning-user workspace at `/dashboard`.

A modal overlay tour would be fragile because the walkthrough spans several routes and mutates shared JSON demo state. The dedicated guide can explain each step, label sample data, preserve local progress, and let browser history work normally.

## Content and evidence gaps

- No customers, testimonials, production usage, independent benchmarks, or verified commercial metrics.
- No real OAuth integrations, credential vault, Postgres, MCP transport, Slack notification, or production security evidence.
- No signup, demo booking, install, pricing, API reference, legal, security, privacy, team, or contact destinations.
- Naming clearance and the final company/product name remain open; the current repository UI uses “Convoy Labs.”
- Only the Closed-Won Paperwork agent has a complete scenario suite.
- RBAC, SSO, multi-tenant hardening, connector marketplace, rollback, rate limits, PII redaction, and multi-model routing are roadmap items.
