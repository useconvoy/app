# Linux ARM64 planner qualification

Clean source `afe128b55e18ee063a580dc24da6de0020a1674d` passed local qualification
of the real Qwen planner image on September 28, 2026. This moves the text planner
from a macOS-specific native build into an independently deployable Linux CPU
container. The action policy, simulator, coordinator and API remain separate.

The final acceptance made **one actual signed Qwen proposal**, selecting the
existing `pick_place_puck` skill in 12.58 seconds wall time. This is a deliberately
bounded one-skill catalog. It is not open-ended robot reasoning, action-policy
execution, a cloud trial, or evidence for the design's illustrative two-second
response target. This request met its original 30-second mission-start budget.

See the [sanitized receipt](../../examples/manipulation/evidence/linux-planner-image.json)
and [build/run instructions](../../infra/planner/README.md).

## Artifact and hosting boundary

The image uses pinned Python and dependency inputs, the existing verified
Qwen2.5-1.5B-Instruct Q4_K_M weights, and llama.cpp source
`5266f24da75dc449bd56cbed7addb9c8e4a6a73e`. Native compilation ran without network
access using a pinned Debian builder, a dated package snapshot and an explicit
`armv8-a` CPU baseline. Its executable, required libraries, loader paths and
relocated extraction were checked. Deterministic archive packing was checked;
this is not a claim of an independently reproduced binary build. The executed
packaging source is preserved by hash; the final helper has a later lint-only
change.

The final local Docker image is approximately 1.15 GB. It contains no Torch,
LeRobot, MuJoCo or management server packages. The non-root service receives only
planner public verification authority and its probe credential. Model assets
are read-only. Native inference and the internal gateway remain on loopback.
The public-facing planner port requires verified HTTPS ingress before remote use.
No infrastructure was provisioned by this slice.

## What passed

- 85 affected planner, asset and ownership/runtime tests on macOS; the two
  Linux-specific cases were skipped there. Ruff and diff checks passed.
- All 15 ownership tests in a lightweight derivative of the actual Linux image,
  including the two direct/recovered thread-group regression cases. Production
  code in the derivative matched the built image.
- Actual image inspection established the loaded model, binary, archive,
  configuration and planner descriptor before release binding.
- Signed session admission and a real model response preserved the original
  identity, observation, deadline, artifact and serving incarnation. Action-only
  credentials and repeat planning were rejected.
- Normal shutdown, unexpected native-process loss, and interruption before
  readiness all produced their expected outcomes. Each of the four cases
  verified native exit, gateway closure/drain, planner closure, marker removal
  and closed listeners, then removed its exact container.
- Temporary credentials were removed. The identities, PIDs, start times and
  restart counts of all eight pre-existing containers remained unchanged.

The model case had limits of two CPUs and 4 GiB on Docker Desktop's Linux ARM64
VM. Its raw cgroup memory charge is retained, but shared/warmed file cache makes
that unsuitable as a total model-footprint or AWS-sizing estimate. One response
does not establish a latency distribution or multi-robot capacity.

Hosted GitHub checks could not start because the account reported a payments or
spending-limit problem; the planner job had no executed steps. Local results
above are not hosted CI success.

## Startup failure and correction

An early image acceptance passed inference but reported unresolved cleanup when
interrupted during loading. Diagnostic-only repetitions passed, so those runs
were not treated as a fix. A separate deterministic Linux process then proved
a real ownership bug: a zombie main thread could coexist with a live thread and
listener, while the ownership record already said `stopped`.

The ownership helper now proves whole-process exit before publishing that state.
A direct child's retained process handle must complete within the existing
deadline. Recovered Linux processes must have no remaining nonleader tasks,
with identity rechecked. Unresolved exit preserves the running record and blocks
replacement; unverifiable identities receive no signal. The deterministic
after-test waited 1.52 seconds for complete exit and verified the listener closed.
The full rebuilt image then passed all acceptance cases. The original startup
failure is consistent with this bug, but its limited diagnostics do not establish
that attribution conclusively.

The earlier CPU-statistic harness error, startup failure, diagnostic runs and
test-image packaging error remain preserved separately. They are not erased or
counted as successful final qualification.

## Next integration

Connect this image through verified HTTPS to the existing local API,
SmolVLA worker and MuJoCo coordinator, using the descriptor inspected inside Linux
and a fresh live planner probe. That local network test precedes a separately
costed cloud trial. AWS access currently needs renewed authentication; the Jetson
still needs accepted SSH authentication and its own target-compute qualification.
