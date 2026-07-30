# Convoy runtime architecture

## Scope

This design owns execution after the application has accepted a mission. It
does not own the product UI, tenant configuration screens, billing, or the
business database.

The durable boundary is:

```text
application -> resolved RunSpec -> provider -> ordered RunEvents + artifacts
                                      |
                                      +-> isolated agent <-> resettable World
```

## Executor progression

| Stage | Executor | Why |
| --- | --- | --- |
| Development | Local Docker | Fast feedback, portable, easy to inspect and reset |
| Managed production | AgentCore Runtime | Dedicated microVM per session, custom containers, session filesystem, shell execution, managed scaling |
| Owned production | ECS on EC2 capacity providers | Custom host dependencies, image caches, browsers, GPUs, warm pools, Spot/On-Demand control |
| High-isolation future | Firecracker on dedicated hosts | Strong tenant boundary and snapshot restore, once scale justifies owning a microVM control plane |

Do not start with Kubernetes. ECS gives task roles, capacity providers, warm
pools, and a smaller operational surface. Keep the executor interface portable
so EKS can be added if customer deployment requirements demand it.

## Control plane versus data plane

The **control plane** validates and resolves requests, enforces tenant quotas,
chooses a runtime profile, dispatches work, records events, handles
cancellation, and reconciles orphaned runs.

The **data plane** is the disposable Linux session containing an agent
supervisor, workspace, output volume, short-lived credentials, and a reachable
World/tool surface. It has no authority to mutate control-plane run state
directly; it emits events through a scoped channel.

The first application integration can be four operations:

```text
submit(resolvedRunSpec, idempotencyKey) -> runId
inspect(runId)                          -> status + result
events(runId, afterSequence)            -> ordered RunEvent stream
cancel(runId, reason)                   -> accepted | alreadyTerminal
```

Provider adapters implement the internal equivalent:

```text
prepare(run)
launch(run) -> providerHandle
inspect(providerHandle)
terminate(providerHandle)
collect(providerHandle) -> artifacts
```

Rules the application side must hold to stay independent of the executor:

1. Store/stream ordered `RunEvent` objects by `run_id` and `sequence`.
2. Treat `run.succeeded`, `run.failed`, `run.timed_out`, and `run.cancelled` as
   mutually exclusive terminal states.
3. Display the World evaluator result and checkpoint evidence from
   `result.json`. Do not infer mission success from a zero process exit code.
4. Store only secret references in specs; a provider resolves values at launch.

The minimum UI-facing lifecycle is:

```text
accepted -> preparing -> started -> agent/world events -> exactly one terminal event
```

Docker container IDs, ECS task ARNs, and AgentCore session IDs are opaque
provider handles. They must never replace Convoy's `run_id`.

## Mission compilation and dynamic execution

Compilation does not produce a complete static DAG before work begins. It
produces a governed `MissionSpec` execution envelope: objective and deadline,
`maxParallelAgents`, `maxTotalAgents`, `maxDepth`, checkpoint interval,
`computeMode`, mission budget and estimated per-episode cost, and the
deployment/environment/policy/tool/input references.

Temporal hosts a small static runtime — generic `MissionWorkflow` and
`AgentWorkflow` definitions. The execution graph emerges at runtime because
each Agent Episode returns one recorded, typed next intent: `use_tool`,
`spawn_agents`, `wait_for_children`, `request_approval`, `wait_for_event`,
`replan`, or `complete`.

Workflow code only interprets recorded results and stays deterministic. Model,
network, database, and tool work belongs in Activities or disposable Agent
Episodes. Every `spawn_agents` request passes an admission Activity before
child Workflows start; admission enforces deadline, depth, total-agent, and
budget limits against an idempotent DynamoDB-backed parallel-slot semaphore.
This permits open-ended logical recursion while bounding physical compute.

Planning is rolling-horizon: an episode chooses only its next intent, persists
a checkpoint/artifact, and yields. For trees large enough to threaten a single
Workflow's history, route subtrees through supervisor Workflows and use
Continue-As-New. That sharding is a production hardening item.

Temporal is the scheduler for recursive mission execution; it supersedes the
Postgres `SKIP LOCKED` run queue proposed in `architecture-v2.md` §1.1. Do not
add SQS as a competing core scheduler.

## Run state machine

```text
accepted -> preparing -> started -> succeeded
                                -> failed
                                -> timed_out
                                -> cancelled
```

Rules:

- `submit` is idempotent by tenant plus idempotency key.
- Each run receives monotonically increasing event sequence numbers.
- A run has exactly one terminal event.
- Duplicate provider observations are normal and must be de-duplicated.
- A zero agent exit code is only process success. `run.succeeded` additionally
  requires a passing World evaluation.
- Cancellation is cooperative first, forceful after a bounded grace period.
- The reconciler marks a non-terminal run failed only after proving that its
  provider resource is gone or its lease has expired.

## Dispatch and reconciliation

The run queue contains identifiers, not secret values or the full mutable
business payload. A dispatcher:

1. leases a queued run;
2. transactionally changes `accepted` to `preparing`;
3. resolves image tags to digests and secret names to provider references;
4. launches through the selected provider;
5. stores the provider handle;
6. changes the run to `started` when the provider proves it is running.

A separate reconciler periodically compares non-terminal database rows with
provider state. This prevents a dispatcher crash from creating permanently
stuck work. SQS delivery is at least once, so correctness comes from
idempotency and reconciliation rather than assuming one delivery.

## Runtime profiles

Profiles should be a small platform-owned catalog, not arbitrary user-supplied
host configuration.

| Profile | Typical use | Suggested executor |
| --- | --- | --- |
| `standard-arm64` | API/MCP tools, documents, analysis | AgentCore or Graviton ECS |
| `browser-arm64` | Browser automation and screenshots | ECS with browser image |
| `high-memory-arm64` | Large repositories or document sets | Larger ECS task |
| `gpu` | Local model/media workloads | ECS GPU capacity provider |
| `regulated-isolated` | Mutually untrusted or stricter tenant boundary | AgentCore first, Firecracker later |

A profile selects resource bounds, compatible image architecture, network
policy, artifact cap, executor, and mission IAM role. The RunSpec selects a
profile indirectly through its validated resource and policy request.

## Image model

- **AMI = host acceleration and hardening.** ECS agent, SSM, telemetry,
  container runtime, kernel settings, optionally pre-pulled common layers.
- **OCI image = agent capability.** Language runtimes, browser/toolchain,
  supervisor, and agent code.
- **World image = simulation capability.** Seed state, tool surface, policies,
  evaluator, and reset semantics.
- **RunSpec = immutable launch request.** Exact image digests, command,
  resources, World, network posture, timeout, secret references, artifact
  policy.

Dependencies belong in images, not startup scripts. Startup should mostly mount
a workspace, receive short-lived credentials, and start the supervisor.

## Security boundary

- Agent and World containers run non-root with dropped Linux capabilities.
- Root filesystems are read-only; only explicit workspace/state/output volumes
  are writable.
- Secrets are references in persisted specs and values only in process
  environment or provider-native credential channels.
- Each mission receives a dedicated task role; the base task role can only
  write its artifact prefix.
- No inbound network path is required.
- `world-only` is a real network isolation mode locally. In AWS, hostname-level
  restricted egress requires a proxy or network firewall; a security group
  alone is not sufficient.
- AgentCore offers the preferred managed isolation boundary. ECS on EC2 is for
  trusted Convoy workloads needing host control; containers on one EC2 host
  should not be treated as mutually untrusted microVMs.
- Every consequential action is policy-checked in the tool/World layer, not
  merely suggested in a prompt.

## Artifacts and observability

The minimum immutable run bundle is:

```text
run.json
events.jsonl
result.json
mission-result.json
logs/
workspace-manifest.json
```

Artifacts use `runs/<tenant-id>/<run-id>/...` in production. Apply size limits
before upload, encrypt with the customer/environment key, and attach content
type, digest, image digest, World version, and retention metadata.

Metrics worth defining before production:

- queue-to-start latency by executor/profile;
- warm versus cold start latency;
- active/queued/terminal runs by tenant;
- agent exit and evaluator outcome separately;
- timeout, cancellation, denied-action, and orphan-recovery counts;
- CPU/memory saturation and artifact bytes;
- cost per successful mission and per evaluator point.

## Invariants

1. Every run has a stable ID, immutable resolved spec, event log, and result.
2. Every World supports `reset`, `observe`, `act`, `gate`, and `evaluate`.
3. A successful agent process is not a successful mission. The World evaluator
   determines mission success.
4. Simulation state and agent workspace are separate. Resetting one must not
   silently mutate the other.
5. Providers emit the same `RunEvent` vocabulary, so the product is not coupled
   to AWS or Docker details.

## POC shape versus production

The deployed POC (`poc/recursive-agents`, `infra/cdk`) is deliberately shaped
for low fixed cost, not for production. The differences are intentional:

| Concern | POC | Production path |
| --- | --- | --- |
| Orchestrator compute | One-shot Fargate coordinator per mission | Long-lived, horizontally scaled Temporal Worker pool |
| Agent compute | One Fargate task per bounded Agent Episode | Fargate, plus ECS/EC2 capacity providers and warm pools for specialized workloads |
| Network | Public IP, no ingress, no NAT Gateway | Private subnets, VPC endpoints, egress proxy/firewall |
| Product state | File store, or Postgres when `DATABASE_URL` is set | Postgres for product, config, and governance state |
| Live projection | DynamoDB at fixed low capacity | Capacity and indexes sized from measured traffic |
| Artifacts | S3 | S3 with tenant keys, lifecycle, retention, customer-managed keys |
| Identity | Shared HTTP Basic credential | OIDC/SAML, workspaces, RBAC, auditable service identities |

Both the POC and the production reference use the same disposable Fargate
episode model, so the physical execution unit does not change on the way up.

## Rollout gates

1. Deterministic local replays stay green.
2. Model-backed agent passes the same Worlds repeatedly.
3. ECS development account survives forced container/host/dispatcher failure.
4. Restricted egress is proven with positive and negative network tests.
5. IAM access analyzer and a human review the planned task roles.
6. One design partner runs shadow missions with no external side effects.
7. Human gates are exercised before any production write capability is added.

## References

Conclusions behind the executor progression above:

- AgentCore Runtime provides dedicated microVMs per session, container-defined
  dependencies, persistent session filesystems, shell commands, MCP support,
  and long-running sessions. It is the fastest credible managed production
  executor, but the orchestration loop and contracts stay independent of it.
  Custom containers are Linux ARM64 and expose `/ping` and `/invocations`.
- ECS EC2 capacity providers support Auto Scaling warm pools — the owned path
  for specialized images and host-level control. ECS task roles are useful, but
  EC2-hosted containers are not a security boundary; mutually untrusted runs
  belong on AgentCore/Fargate or a later Firecracker tier.
- Firecracker snapshot compatibility is coupled to CPU model, kernel, and
  Firecracker version. It is a deliberate platform investment, not an MVP
  shortcut.
- AppWorld, WebArena, and TheAgentCompany reinforce the same World shape:
  seeded state, reset, a typed action surface, isolated execution, trajectory
  logs, and state-based evaluation.

- [AgentCore custom runtime contract](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/getting-started-custom.html)
- [AgentCore runtime sessions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-sessions.html)
- [ECS EC2 capacity providers and warm pools](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/asg-capacity-providers.html)
- [ECS task IAM roles](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-iam-roles.html)
- [Firecracker snapshot support](https://github.com/firecracker-microvm/firecracker/blob/main/docs/snapshotting/snapshot-support.md)
- [TheAgentCompany](https://github.com/TheAgentCompany/TheAgentCompany)
- [AppWorld](https://github.com/StonyBrookNLP/appworld)
- [WebArena](https://github.com/web-arena-x/webarena)
