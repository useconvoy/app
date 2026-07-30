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

Docker container IDs, ECS task ARNs, and AgentCore session IDs are opaque
provider handles. They must never replace Convoy's `run_id`.

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

## Rollout gates

1. Deterministic local replays stay green.
2. Model-backed agent passes the same Worlds repeatedly.
3. ECS development account survives forced container/host/dispatcher failure.
4. Restricted egress is proven with positive and negative network tests.
5. IAM access analyzer and a human review the planned task roles.
6. One design partner runs shadow missions with no external side effects.
7. Human gates are exercised before any production write capability is added.
