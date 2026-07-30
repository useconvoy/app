# Vinny's Convoy runtime handoff

This file is the living handoff for the simulations and agent-runtime work. It
is deliberately separate from the application README so Aneesh and his agents
can change the product surface without having to infer infrastructure intent.

## Current status

**Phase:** local foundation and live recursive-agent cloud POC verified;
Temporal Cloud, ECS Fargate, S3, provisioned DynamoDB, ECR, and CloudWatch are
connected. The Convoy UI/API is live on a capped App Runner service and has
successfully launched and observed a full mission end to end.

**Application source:** Aneesh's current application was read from
`origin/agent/landing-start-here` at commit `0acaeda`. The AWS adapter changes
were merged through PR #4 into `agent/landing-start-here`.

The two workstreams are:

1. **Worlds / simulations** — resettable company environments with typed tools,
   policy gates, audit logs, and deterministic evaluators.
2. **Agent runtime infrastructure** — isolated Linux execution sessions that
   accept a versioned run contract, stream events, enforce resource limits, and
   can run against any World.

They meet only through contracts. The application should create a `RunSpec`,
observe `RunEvent` records, and display World state; it should not know whether
the executor is local Docker, Amazon Bedrock AgentCore Runtime, ECS/EC2, or a
future Firecracker fleet.

## Architecture decision

### Runtime progression

| Stage | Executor | Why |
| --- | --- | --- |
| Development | Local Docker | Fast feedback, portable, easy to inspect and reset |
| Managed production | AgentCore Runtime | Dedicated microVM per session, custom containers, session filesystem, shell execution, managed scaling |
| Owned production | ECS on EC2 capacity providers | Custom host dependencies, image caches, browsers, GPUs, warm pools, Spot/On-Demand control |
| High-isolation future | Firecracker on dedicated hosts | Strong tenant boundary and snapshot restore when Convoy has enough scale to justify owning a microVM control plane |

Do not start with Kubernetes. ECS gives Convoy task roles, capacity providers,
warm pools, and a smaller operational surface. Keep the executor interface
portable so EKS/Kubernetes can be added if customer deployment requirements
eventually demand it.

### Free-tier-conscious POC versus production runtime

The current AWS POC deliberately has a different operating shape from the
production data plane:

| Concern | POC executor | Production path |
| --- | --- | --- |
| Product UI/API | One App Runner instance, 0.25 vCPU/0.5 GB, max size 1 | Multi-instance service behind enterprise identity and tenant isolation |
| Orchestrator compute | One-shot Fargate coordinator for one mission | Long-lived, horizontally scaled Temporal Worker service |
| Agent compute | One Fargate task per bounded Agent Episode | Fargate initially; ECS/EC2 capacity providers and warm pools for specialized or high-volume workloads |
| Network | Public IP, no ingress, no NAT Gateway | Private subnets, VPC endpoints, egress proxy/firewall |
| Product state | Ephemeral file store in the App Runner container | Postgres for product/config/governance state |
| Live mission projection | DynamoDB provisioned at 5 RCU/5 WCU | DynamoDB capacity and indexes sized from measured traffic |
| Artifacts/checkpoints | S3 | S3 with tenant keys, lifecycle, retention, and customer-managed encryption |
| Identity | Shared HTTP Basic credential from SSM Parameter Store | OIDC/SAML, workspaces, RBAC, and auditable service identities |

There is no Lambda alternate executor. The brief Lambda bridge experiment was
removed so the demo and the production reference share the same physical
episode model. Fargate is not free, but the tasks are one-shot, the POC has no
NAT Gateway, ALB, EFS, or always-on ECS service, and the mission envelope caps
parallelism, total agents, depth, deadline, and estimated spend.

### Mission compilation and dynamic execution

Convoy must not compile a complete static DAG before work begins. Compilation
produces only a governed `MissionSpec` execution envelope:

- objective and deadline;
- `maxParallelAgents`, `maxTotalAgents`, `maxDepth`, and checkpoint interval;
- `computeMode` (`auto`, `fast`, `economy`, or `dedicated`);
- mission budget and estimated per-episode cost;
- deployment, environment, policy, tool, and input references.

Temporal hosts a small static runtime: generic `MissionWorkflow` and
`AgentWorkflow` definitions. The execution graph emerges at runtime because
each Agent Episode returns one recorded, typed next intent:
`use_tool`, `spawn_agents`, `wait_for_children`, `request_approval`,
`wait_for_event`, `replan`, or `complete`.

Workflow code only interprets recorded results and remains deterministic.
Model, network, database, and tool work belongs in Activities or disposable
Agent Episodes. Every `spawn_agents` request passes through the Convoy
admission Activity before child Workflows start. The POC implements
deadline/depth/total-agent/budget admission and an idempotent DynamoDB-backed
parallel-slot semaphore. This permits open-ended logical recursion while
bounding physical compute and cost.

Planning is rolling-horizon: an episode chooses only its next intent, persists a
checkpoint/artifact, and yields control. For trees large enough to threaten a
single Workflow's history, route subtrees through supervisor Workflows and use
Temporal Continue-As-New. That sharding step is a production hardening item,
not something the 13-episode POC needs to fake.

### Image model

- **AMI = host acceleration and hardening.** ECS agent, SSM, telemetry,
  container runtime, kernel settings, and optionally pre-pulled common layers.
- **OCI image = agent capability.** Language runtimes, browser/toolchain,
  Convoy supervisor, and agent code.
- **World image = simulation capability.** Seed state, tool surface, policies,
  evaluator, and reset semantics.
- **RunSpec = immutable launch request.** Exact image digests, command,
  resources, World, network posture, timeout, secrets references, and artifact
  policy.

Dependencies belong in images, not in startup scripts. Startup should mostly
mount a workspace, receive short-lived credentials, and start the supervisor.

## What exists now

| Area | Concrete artifact | Status |
| --- | --- | --- |
| Contracts | `RunSpec`, `WorldSpec`, and `RunEvent` JSON schemas plus executable validators | Verified |
| Renewal simulation | Seeded enterprise accounts, chat, tasks, documents, approvals, policy gates, evaluator | 100/100 reference run |
| Vendor simulation | Seeded vendor/evidence state, remediation, memo, approvals, decision policy, evaluator | 100/100 reference run |
| World interface | Dependency-free HTTP API and MCP stdio server | Tested |
| Agent image | Non-root Node 22/Python 3/git/curl/jq/SSH base with Convoy supervisor | Built |
| Local executor | Per-run internal network, Docker volumes, read-only containers, capability/resource limits, exact cleanup | Two end-to-end runs passed |
| Tests | Contracts, MCP negotiation, deterministic outcomes, approval/egress/forecast denial policies | 10/10 passed |
| AWS owned runtime | Terraform for ECS/EC2 warm capacity, ECR, S3, SQS/DLQ, KMS, logs, IAM, task/host networking | `terraform validate` passed |
| AWS managed runtime | AgentCore container and RunSpec mapping | Design documented; provider adapter not yet implemented |
| Durable orchestration | Temporal Cloud namespace and restricted coordinator service account | Live connection verified with the TypeScript SDK |
| Recursive cloud POC | TypeScript CDK, governed MissionSpec, typed episode intents, policy-admitted Fargate Agent Episodes, S3 artifacts, DynamoDB projection | Live governed 13-agent mission completed; zero running tasks afterward |
| Hosted demo | App Runner UI/API adapter launches one-shot coordinator tasks and reads the DynamoDB projection | Live authenticated run completed from the application API |

The local executor uses Docker-managed volumes instead of macOS bind mounts.
This avoids Docker Desktop file-sharing stalls, makes the filesystem boundary
explicit, and mirrors ephemeral production task volumes more closely. Run
artifacts are copied back under `.convoy/runs/<run-id>-<timestamp>/`.

## Application integration boundary

Aneesh's application can stay independent from the runtime implementation:

1. Create and validate a `RunSpec`.
2. Submit it to a provider adapter.
3. Store/stream ordered `RunEvent` objects by `run_id` and `sequence`.
4. Treat `run.succeeded`, `run.failed`, `run.timed_out`, and `run.cancelled` as
   mutually exclusive terminal states.
5. Display the World evaluator result and checkpoint evidence from
   `result.json`; do not infer mission success from a zero process exit code.
6. Store only secret references in specs. A provider resolves values when the
   task starts.

The minimum UI-facing run lifecycle is:

```text
accepted -> preparing -> started -> agent/world events -> exactly one terminal event
```

Provider-specific task ARNs, container IDs, and AgentCore session IDs should be
metadata, not the application's primary run identity.

## Compatibility with Aneesh's current application

I inspected commit `5d1f130` without merging or changing it. The application
already has a useful product-domain layer:

- agents, versions, deployments, sandbox/production environments;
- connectors, credential references, tool grants, and policy rules;
- runs, tool-call traces, approvals, promotions, and audit events;
- an in-process event bus, agent loop, simulated gateway, and state-based test
  harness;
- eight Closed-Won Paperwork scenarios with positive and negative assertions.

That work and this runtime are complementary. The clean integration is:

| Application today | Runtime foundation |
| --- | --- |
| `AgentVersion` + `Deployment` + trigger | compile into a resolved `RunSpec` |
| `Run.id` | preserve as the Convoy `run_id` |
| in-process `advanceRun` | replace behind a provider adapter |
| simulated connector gateway | expose as a World/tool endpoint |
| `ConvoyEvent` SSE updates | project ordered `RunEvent` records into UI events |
| `TestScenario.assertions` | compile into World evaluator checkpoints |
| approval rows and decisions | bridge to `approval.request/resolve` tool gates |
| fleet kill switch | provider cancellation plus one `run.cancelled` terminal event |

State names need one intentional translation layer. The application currently
uses `queued`, `running`, `paused_pending_approval`, `succeeded`, `failed`,
`rejected`, and `killed`; the provider contract uses lifecycle events such as
`accepted`, `preparing`, `started`, `cancelled`, and `timed_out`. Do not make
the UI depend on raw ECS/AgentCore states. Keep `paused_pending_approval` as a
business run state while the provider session is still alive.

The Closed-Won Paperwork suite should become the third World rather than being
rewritten. Its fixtures are the seed-state generator, gateway tools are the
typed World surface, and assertions are already the evaluator specification.

When write access and a stable target branch exist, integrate by starting from
Aneesh's branch and adding `contracts/`, `runtime/`, `worlds/`, `infra/`, and
the runtime architecture docs. Reconcile the root `package.json`, `README.md`,
and `.gitignore` manually; do not merge this standalone root wholesale.

## Repository boundary

The planned repository layout is:

```text
contracts/                 JSON schemas shared with the application
runtime/supervisor/        PID 1 process inside every agent container
runtime/local/             local Docker executor
runtime/providers/         future AgentCore and ECS provider adapters
worlds/runtime/            provider-neutral World API and MCP surface
worlds/catalog/            versioned simulation definitions and seed data
examples/runs/             reproducible RunSpecs
infra/terraform/           AWS runtime data plane
infra/packer/              optional golden host AMI
scripts/                   validation and developer operations
tests/                     contract, World, supervisor, and end-to-end tests
```

## Non-negotiable invariants

1. Every run has a stable ID, immutable resolved spec, event log, and result.
2. Every World supports `reset`, `observe`, `act`, `gate`, and `evaluate`.
3. Agent credentials are references in specs and short-lived values at runtime;
   secret values never enter Git, images, event logs, or World state.
4. The agent container is non-root, read-only except for `/workspace` and
   `/convoy/output`, and receives explicit CPU, memory, PID, timeout, and
   network limits.
5. A successful agent process is not a successful mission. The World evaluator
   determines mission success.
6. Simulation state and agent workspace are separate. Resetting one must not
   silently mutate the other.
7. Providers emit the same `RunEvent` vocabulary so the product is not coupled
   to AWS or Docker details.

## Initial use cases

### Enterprise renewal recovery

Agents identify high-risk renewals, coordinate internally, create accountable
work, write an executive recovery brief, and request human approval before
material forecast changes.

### Vendor security review

Agents collect security/privacy/architecture evidence, create remediation work,
write a risk memo, and route the final vendor decision to a human.

These use the same chat, work, document, evidence, approval, and audit
primitives. That is the product wedge: new missions compound on a shared World
and runtime substrate instead of becoming bespoke automations.

## Research conclusions

- Amazon AgentCore Runtime now provides dedicated microVMs per session,
  container-defined dependencies, persistent session filesystems, shell
  commands, MCP support, and sessions up to eight hours. It is the fastest
  credible managed production executor, but Convoy should keep its orchestration
  loop and contracts independent of AgentCore. Custom containers are Linux
  ARM64 and expose `/ping` and `/invocations`.
- ECS EC2 capacity providers support Auto Scaling warm pools. This is the owned
  runtime path for specialized images and host-level control.
- ECS task roles are useful but EC2-hosted containers are not a security
  boundary. Sensitive or mutually untrusted runs should use AgentCore/Fargate
  initially or a later Firecracker tier.
- Firecracker snapshots restore memory copy-on-write and can be fast, but
  snapshot compatibility is coupled to CPU model, kernel, Firecracker version,
  and external block-device handling. It should be a deliberate platform
  investment, not an MVP shortcut.
- AppWorld, WebArena, and TheAgentCompany all reinforce the same World shape:
  seeded state, reset, a typed action surface, isolated execution, trajectory
  logs, and state-based evaluation.

Primary references:

- [AgentCore custom runtime contract](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/getting-started-custom.html)
- [AgentCore runtime sessions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-sessions.html)
- [ECS EC2 capacity providers and warm pools](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/asg-capacity-providers.html)
- [ECS-optimized AL2023 AMI parameters](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/retrieve-ecs-optimized_AMI.html)
- [ECS task IAM roles](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-iam-roles.html)
- [Firecracker snapshot support](https://github.com/firecracker-microvm/firecracker/blob/main/docs/snapshotting/snapshot-support.md)
- [TheAgentCompany](https://github.com/TheAgentCompany/TheAgentCompany)
- [AppWorld](https://github.com/StonyBrookNLP/appworld)
- [WebArena](https://github.com/web-arena-x/webarena)

## Developer workflow

```bash
npm run validate
npm test
npm run build:images
npm run test:e2e
```

No model or cloud credentials are needed for these commands. The deterministic
agents exercise the same isolation, event, World, policy, and evaluation path
that a model-backed image will use.

To inspect the two scenarios without Docker:

```bash
node worlds/runtime/cli.mjs catalog
node worlds/runtime/cli.mjs replay aurelia-renewal-ops \
  worlds/catalog/aurelia-renewal-ops/successful-run.json
node worlds/runtime/cli.mjs replay aurelia-vendor-review \
  worlds/catalog/aurelia-vendor-review/successful-run.json
```

The AWS module is under `infra/terraform/aws-runtime`. It is validated but has
not been planned against or applied to an AWS account.

Deeper implementation notes:

- [`docs/runtime-architecture.md`](./docs/runtime-architecture.md) defines the
  application/provider boundary, state machine, reconciliation, profiles,
  security posture, and rollout gates.
- [`worlds/README.md`](./worlds/README.md) defines how to extend the platform
  with additional use cases without creating bespoke infrastructure.
- [`runtime/providers/agentcore/README.md`](./runtime/providers/agentcore/README.md)
  maps the portable contracts to AgentCore.
- [`infra/terraform/aws-runtime/README.md`](./infra/terraform/aws-runtime/README.md)
  explains what the owned AWS runtime creates and deliberately leaves out.

## Work log

### 2026-07-29

- Confirmed `useconvoy/app` is private and read-only for the current GitHub
  identity; it was initially empty.
- Chose provider-neutral contracts with local Docker as the first executor.
- Chose AgentCore as the first managed production candidate and ECS/EC2 warm
  capacity as the owned-runtime candidate.
- Implemented versioned RunSpec, WorldSpec, and RunEvent contracts.
- Implemented two resettable Worlds, HTTP and MCP interfaces, typed actions,
  human gates, audit trails, and state-based evaluators.
- Built non-root agent and World OCI images.
- Built and repaired the local isolated executor after an end-to-end Docker
  Desktop portability test.
- Verified 10 unit/integration tests and two full Docker runs at 100/100.
- Added and validated the ECS/EC2 Terraform data plane with AWS provider
  `6.56.0`.
- Detected and reviewed Aneesh's first application commit (`5d1f130`) and
  documented a concrete, low-conflict integration boundary.

### 2026-07-29 — consolidated end-to-end target

- Reviewed Aneesh's `docs/architecture-v2.md` at commit `5a56a5c` and
  reconciled it with the recursive-agent runtime direction.
- Wrote the consolidated native Google Doc:
  [Convoy — End-to-End Platform Architecture](https://docs.google.com/document/d/1-bkcIxTraFQ0N2K-T_KkM9bJUG-jfE0pX18arnPEkj8/edit).
- Embedded ten architecture figures in that document: system-plane and AWS
  topology maps, mission and recovery sequences, recursive admission,
  scale-to-zero waiting, state ownership, gateway authorization, and hermetic
  evaluation/promotion, plus the static-runtime/dynamic-execution-graph model.
  Editable SVG sources, document-ready PNGs, and the generator live in
  `assets/architecture-diagrams/`.
- Clarified that mission compilation produces a governed `MissionSpec` and
  execution envelope, not a complete DAG. Generic, versioned
  `MissionWorkflow` and `AgentWorkflow` definitions interpret typed episode
  intents while the working plan and Agent graph emerge through rolling-horizon
  execution.
- The v2 environment, gateway, credential, policy, approval, hermetic-eval,
  immutable-deployment, and governance model remains the product foundation.
- The target execution path uses Temporal for durable Mission/Agent workflows,
  ECS Fargate for bounded Agent Episodes, S3 for shared mission artifacts and
  checkpoints, DynamoDB for the rebuildable live projection, and Postgres for
  product configuration and governance records.
- The logical work unit exposed by the product is an Agent. Physical compute is
  an Agent Episode: a disposable task that loads a checkpoint, works, persists
  results, and exits. Recursive logical spawn is allowed, while admission is
  bounded by deadline, parallelism, depth, and cost.
- Use AWS CDK v2 with TypeScript for the new AWS implementation. The existing
  Terraform module remains a validated reference, but should not become a
  second source of infrastructure truth.
- Temporal supersedes the proposed Postgres `SKIP LOCKED` run queue for new
  recursive mission execution. Avoid adding SQS as a competing core scheduler.

### 2026-07-29 — cloud bootstrap

- Configured the local AWS CLI profile `convoy-dev` for `us-west-2` using
  temporary browser-login credentials. The current identity is the AWS account
  root, so it must not be used for application deployment; create a non-root
  CDK deployment identity before applying infrastructure.
- Created and activated the Temporal Cloud namespace `convoy-dev.caqjl` in AWS
  `us-west-2` with API-key authentication and 30-day Workflow history
  retention.
- Created the Temporal service account `convoy-dev-coordinator` with Developer
  account scope and Write access only to `convoy-dev.caqjl`.
- Created the API key `convoy-dev-coordinator-2026-07`, expiring October 28,
  2026. The key value is not present in this repository or local configuration.
- Stored the connection bundle in AWS Secrets Manager at
  `convoy/dev/temporal-cloud`. Its JSON fields are `namespace`, `endpoint`, and
  `apiKey`; the endpoint is `convoy-dev.caqjl.tmprl.cloud:7233`.
- Verified a real TLS and authenticated namespace connection using
  `@temporalio/client` `1.21.1` and a Workflow-list request. The test retrieved
  the secret from Secrets Manager and did not print the credential.

### 2026-07-29 — live recursive-agent POC

- Bootstrapped CDK and deployed `ConvoyPocStack` in AWS account
  `327998106824`, region `us-west-2`.
- Added the minimal TypeScript CDK implementation under `infra/cdk`:
  a two-AZ public-only VPC with no NAT Gateway, no-ingress runner security
  group, ECS Fargate cluster, ECR repository, encrypted/versioned S3 artifact
  bucket, on-demand DynamoDB mission table with point-in-time recovery,
  CloudWatch logs, and separate least-purpose task roles.
- Added the shared ARM64 runtime image under `poc/recursive-agents`. It runs
  non-root with a read-only root filesystem and writable `/tmp`, and has two
  modes: a one-shot Temporal coordinator or one bounded Agent Episode.
- Implemented `MissionWorkflow` and recursive `AgentWorkflow`. Each Workflow
  launches one Fargate episode, waits for its durable Temporal signal, and may
  start children. Logical agents can therefore recursively create more logical
  agents while physical compute remains bounded and disposable.
- The first smoke run failed safely before agent launch because the slim image
  did not include an OS certificate bundle. Added `ca-certificates`; the failed
  coordinator exited and left zero running tasks.
- Live mission `convoy-poc-20260730T054646Z` then completed a depth-2,
  fanout-3 tree: 13 Fargate Agent Episodes (`1 + 3 + 9`), 14 DynamoDB records
  all marked `COMPLETED`, and 14 S3 artifacts including the mission summary.
  Temporal completed in about 92 seconds, the coordinator exited with code 0,
  and ECS returned to zero running tasks.
- The deployed POC deliberately uses public IPs and outbound-only security to
  avoid NAT Gateway fixed cost. Production should move agents into private
  subnets with VPC endpoints and a controlled egress path.
- `poc/recursive-agents/README.md` contains the exact image publication and
  mission launch commands. Generated CDK output files are ignored.

### 2026-07-29 — governed runtime and hosted end-to-end POC

- Replaced static-DAG-shaped inputs with a governed `MissionSpec`: objective,
  deadline, maximum parallel and total agents, maximum depth, checkpoint
  interval, compute mode, budget, and deployment/environment/policy/tool/input
  references.
- Added typed episode intents. The deterministic demo emits `spawn_agents` and
  `complete`; the contract also reserves `use_tool`, `wait_for_children`,
  `request_approval`, `wait_for_event`, and `replan`.
- Added idempotent DynamoDB-backed admission decisions and agent-slot leases.
  Spawn admission enforces deadline, depth, total-agent, and estimated-cost
  limits. Slot leases cap physically active episodes at
  `maxParallelAgents`.
- Kept Temporal Workflow code deterministic: it interprets recorded episode
  results and admission decisions, while ECS, DynamoDB, S3, and Temporal client
  calls remain in Activities or Agent Episodes.
- Removed the temporary Lambda episode bridge. Both the POC and production
  reference now use disposable Fargate Agent Episodes; the production runtime
  adds a durable Worker pool, private networking, and supervisor/Continue-As-New
  sharding.
- Added the application cloud adapter in the isolated `convoy-app-aws`
  worktree. App Runner starts one coordinator task per run and reads the
  DynamoDB mission projection for the live run page.
- Built the amd64 application image with CodeBuild
  `BUILD_GENERAL1_SMALL`, published it as `app-poc-latest`, and deployed one
  App Runner instance at 0.25 vCPU/0.5 GB with min/max size 1.
- Added HTTP Basic authentication backed by the Standard SecureString
  `/convoy/poc/app-basic-auth`, plus a public health endpoint. The first
  App Runner attempt exposed that its injected `HOSTNAME` caused Next.js to
  bind to one interface; the final image forces `0.0.0.0` and passed both a
  local App Runner-like hostname test and the managed health check.
- Added a `$10` monthly AWS budget and disabled ECS Container Insights. The
  stack has no NAT Gateway, ALB, EFS, always-on ECS service, or Lambda bridge.
- Verified hosted mission `run_ms75cbt80cf4jtw` through the authenticated
  application API:
  - the saved envelope capped total agents at 13, active agents at 6, depth at
    2, deadline at 15 minutes, and estimated spend at 100 cents;
  - live projection grew dynamically from 1 root to 4 agents and then 13;
  - all 13 Agent Episodes completed, the coordinator exited `0`, and the app
    reported `succeeded`;
  - DynamoDB contains the mission, 13 agents, and 4 admission decisions;
  - S3 contains 13 episode artifacts plus the mission summary; and
  - ECS returned to zero running and zero pending tasks.

## Next actions

- Replace the temporary root-backed AWS CLI login with an IAM Identity Center
  administrator/deployer and runtime break-glass roles. The POC is live, but
  root-backed CLI access must not become the operating model.
- Replace the mutable `poc-latest` image reference with immutable image digests
  in a versioned deployment record.
- Add reconciler behavior for Fargate task launch failure/interruption so an
  Agent Workflow receives an explicit failure signal instead of waiting for its
  Workflow timeout.
- Add explicit mission cancellation and deadline propagation to live Fargate
  tasks; the current deadline blocks further admission but does not yet stop an
  already-running episode.
- Add periodic episode heartbeats/checkpoints. The POC checkpoints at episode
  boundaries and passes the configured interval into the episode.
- Implement `spawn_agent`, `cancel_agent`, `signal_agent`, `join_agents`, and
  `publish_artifact` platform tools behind the policy gateway.
- Replace the POC's file-backed application state and shared Basic credential
  with Postgres plus workspace identity/RBAC.
- Shard large execution trees across supervisor Workflows and
  Continue-As-New before permitting production-scale recursion.
- Add a restricted-egress proxy profile and prove its hostname policy with a
  negative network test.
- When write access is granted and a default/target branch is designated,
  reconcile this foundation into a narrow runtime/infrastructure PR rather than
  pushing directly.
