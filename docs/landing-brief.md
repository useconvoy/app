# Landing and Start Here brief

The working brief behind `src/app/page.tsx` and `src/app/start/page.tsx`. It records
what the public surface claims, why, and — as importantly — what it is not allowed to claim.
Every number on the landing page is either derived at build time from a product module or
listed in §7 below with the file that proves it.

---

## 1. Product definition

Convoy Labs is the control plane for company agents: an operations team creates a versioned
agent, grants it only the tools a given environment allows, tests that exact version against
real system state, promotes it behind a gate, and supervises it through human approvals, an
ordered per-run trace, and a fleet kill switch.

## 2. Audiences

| Audience | What they are trying to establish | Where the page sends them |
|---|---|---|
| **RevOps / business-systems operators** (primary) | Can this actually do the paperwork, and can I tell when it went wrong? | `/agents/agent_closed_won_paperwork` |
| **IT, security and platform admins** (primary) | What can it reach, who granted that, and how fast can I stop it? | `/environments` |
| **Technical evaluators and investors** (secondary) | Is the governance real or a screenshot? | `/runs/run_hist_1` |

The three lanes appear as one row in the "Start here" band, at roughly 60% scroll depth. There
is no persona switcher: the three audiences want the *same* evidence from different angles, and
hiding two-thirds of it behind a tab would weaken all three — as well as splitting one crawlable
page into three.

## 3. Core problem

The company's routine work is already being handed to agents that belong to individuals. They
inherit one employee's access, keep their history in that employee's account, and leave when
that person does. The alternative — deterministic automation — breaks the moment the input
varies. Neither is a shape you can put in front of a system of record at volume.

## 4. Thesis

Bounded LLM reasoning becomes deployable company infrastructure when authority lives in the
environment rather than in the agent: every action passes an environment-aware policy gateway,
the exact version is tested against system state before promotion, and consequential decisions
stop for a human.

## 5. Operating principles

- Hand over routine work; do not hand over judgment.
- Authority is environment-defined. Agents never hold credentials.
- Test the exact version, against system state, never against the agent's own summary.
- Escalate instead of guessing.
- Every action attributable; every agent interruptible mid-run.
- Stay scoped to work that is routine, taste-free, verifiable, systems-of-record and
  volume-bearing.
- Describe the proof of concept honestly, before anyone asks.

## 6. Narrative sequence

Concrete before abstract. The run comes before the lifecycle, because a reader who has watched
one deal get stopped twice already understands what "supervise" means.

| # | Section | Job | Evidence |
|---|---|---|---|
| 1 | Hero + verdict panel | Define the category and prove the mechanism in one viewport | `SEED_POLICIES` evaluated by `evaluateRules` |
| 2 | The problem | Personal vs company agents, with the failure modes named | `docs/product-spec.md` §1 |
| 3 | One run | Northwind Systems, two independent stops | `seed.ts`, `templates.ts`, `planner.ts` |
| 4 | The gateway | Six ordered checks, three outcomes, the credential boundary | `gateway.ts:28-83` |
| 5 | Lifecycle | Create, test, promote, supervise | `harness.ts`, `promotion.ts` |
| 6 | Where this belongs | The band between scripts and taste, plus explicit exclusions | `docs/product-spec.md` §1, §3 |
| 7 | What's real | Implemented / simulated / not built, in three columns | this document, §8 |
| 8 | Start here | Three role-labelled lanes into the workspace | `/environments`, `/agents/…`, `/runs/…` |
| 9 | FAQ | Eight objections a technical reviewer actually raises | code, cited inline |
| 10 | Closer | One CTA, restated with the time cost attached | — |

**Comprehension targets.** At 5 seconds: the headline plus a verdict panel showing the same call
refused in Sandbox and held in Production. At 30 seconds: the Northwind run and its two
unrelated stops. At 120 seconds: the check order, the promotion gate, and the honest inventory.

## 7. Calls to action

- **Primary — "Start the guided walkthrough" → `/start`.** Seven steps, about eight minutes.
- **Secondary — "Open the sample workspace" → `/dashboard`.**
- The header carries the same primary CTA and one text link to the workspace.

`/register` is deliberately **not** a call to action. Registration exists, but it only activates
under `CONVOY_ACCOUNTS_ENABLED=1`, and creating an account does not produce a governed workspace
of your own — the Meridian demo data is a separate, shared store. Promoting it would be the
largest overclaim available on this page. It stays as one footer link, labelled plainly.

`robots.ts` indexes `/` and `/start` only. The workspace routes sit on shared, mutating sample
state, and `/login`, `/register`, `/missions` and `/workspaces` are disallowed as well.

## 8. Verifiable numbers used on the page

Counts marked *derived* are computed at build time in `page.tsx` from the product's own modules,
so the page cannot drift from the workspace it describes.

| Claim | Value | Source |
|---|---|---|
| Permission rules in the sample workspace | 18 (*derived*) | `SEED_POLICIES.length` |
| …that hold an action for a human | 4 (*derived*) | `effect === "require_approval"` |
| Scenarios in the Closed-Won suite | 8 (*derived*) | `CLOSED_WON_SCENARIOS.length` |
| Assertions across that suite | 33 (*derived*) | sum of `assertions.length` |
| Agents in the sample workspace | 4 (*derived*) | `TEMPLATES.length` |
| Tools across four connectors | 13 (*derived*) | sum over `CONNECTORS[].tools` |
| Ordered gateway checks | 6 | `gateway.ts:28-83` |
| Walkthrough steps / duration | 7 / ~8 min | `StartGuide.tsx`; step times sum to ~7 min 20 s |

The Northwind figures — USD 87,700, a 22% discount against a 15% standard, an external billing
contact — are all in `seed.ts`, and the two stops they produce are what `planner.ts` and
`pol_prod_email` actually do.

## 9. What the page must never claim

Grouped by the gap that forbids it.

**No customers.** No logos, testimonials, case studies, "trusted by", or user counts.

**No production usage.** No uptime, SLO, latency, throughput, time-saved or ROI figures. The
overnight dashboard counts (47 leads, 6 deals, 12 drafts, 31 records) are seeded fixture data
and must never be presented as product metrics.

**External systems are simulated.** No vendor logos, no integrations grid, no "connects to
HubSpot", no OAuth, no MCP transport claim. `connectors.ts` is an MCP-*shaped* seam, which is a
design statement, not a capability.

**No security infrastructure.** No SOC 2, ISO, GDPR or pen-test language. No "vault" —
`credentialRef` is a display string. No "role-based approvals" or "verified approver identity":
`api/approvals/[id]` accepts the approver name from the client without checking it. And the
audit log is **append-only**, never "immutable" or "tamper-proof" — it is `auditEvents.push`
with no hash chain.

**No commercial motion.** No pricing, trial, "book a demo", "contact sales" or "request access".
No "read the docs" — there is no docs site, so every "learn more" must resolve to a real
in-app route. The Start here band exists to absorb that pressure: it converts "learn more" into
"go and verify", which is a stronger offer and the only one this repo can keep.

**Not the second execution plane.** No Fargate, Temporal, missions or durable execution on the
marketing surface. `api/missions` saves locally unless `CONVOY_CLOUD_RUNTIME=1`, and the cloud
path does not produce trace rows or approvals in this app.

## 10. Start Here: one canonical route

`/start` is the single guided path; the landing page keeps exactly one interactive widget and
otherwise routes. No modal tour, no second in-page walkthrough.

The walkthrough spans seven distinct destinations with their own query params and a hash, three
of which mutate shared server state, and three of which mount an `EventSource` on arrival. An
overlay tour would have to survive seven full navigations and could not degrade when a deal is
already Closed-Won. A dedicated route can: it labels each step's blast radius before you click,
keeps progress in `localStorage`, leaves browser Back working, and offers a completed-run
fallback when the sample state has already moved.

## 11. Remaining content and evidence gaps

- No customers, testimonials, production usage, independent benchmark, or verified commercial
  metric exists, so the page's credibility rests entirely on being checkable and being candid.
- Only the Closed-Won Paperwork agent has a scenario suite; the other three cannot be promoted.
- Approval decisions are not bound to a verified identity. This is stated on the page rather
  than omitted, and it is the top M1 item in `docs/architecture-v2.md`.
- Naming clearance is still open; the repository UI uses "Convoy Labs".
- `NEXT_PUBLIC_SITE_URL` is build-time only. If it is omitted, canonical URLs, Open Graph URLs
  and the sitemap fall back to the DigitalOcean preview host.
