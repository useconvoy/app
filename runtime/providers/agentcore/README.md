# AgentCore provider mapping

AgentCore is Convoy's first managed-runtime candidate. The adapter should keep
the application contract identical to `local-docker` and `ecs-ec2`.

## Container contract

AgentCore custom containers must be Linux ARM64, listen on port 8080, and
implement:

- `GET /ping` for health and busy/idle status;
- `POST /invocations` for a session invocation.

Build Convoy runtime images as `linux/arm64` even on x86 developer machines.
Native Node/Python dependencies must also be compiled for Linux ARM64.

## RunSpec mapping

| Convoy field | AgentCore concept |
| --- | --- |
| `metadata.id` | invocation/run ID in payload and event records |
| `metadata.mission_id` | mission label and observability attribute |
| `runtime.image` | immutable ECR image digest on a runtime version |
| `runtime.timeout_seconds` | supervisor deadline, capped by platform limits |
| `runtime.environment` | non-secret runtime environment |
| `runtime.network` | public or VPC network configuration plus Convoy policy |
| `secrets[].reference` | AgentCore Identity or launch-time secret resolution |
| `world.id` | session payload and World endpoint selection |
| `artifacts` | session filesystem plus final upload to the artifact store |

Use one stable AgentCore `runtimeSessionId` for all invocations belonging to a
single Convoy run. A session must not be reused across customers or unrelated
runs.

## Adapter lifecycle

1. Validate and resolve the RunSpec; replace image tags with a digest.
2. Select or create an immutable AgentCore runtime version for that image.
3. Invoke with a unique session ID and the resolved non-secret payload.
4. Translate invocation/session logs into ordered `RunEvent` records.
5. Preserve supervisor output and World evaluation as run artifacts.
6. Stop the session on cancellation or deadline and emit exactly one terminal
   event.

## Deliberately deferred

This repository does not declare an unverified Terraform AgentCore resource.
Provisioning should be added after the AWS provider resource/API shape is
selected and tested against the target AWS account. The portable work to do
first is an ARM64 HTTP wrapper around the current supervisor with `/ping` and
`/invocations`; the `RunSpec` and event contracts remain unchanged.
