# Decision 004: qualify a planner and an action policy as one application

Status: merged on `v1`, including local deployment-driven activation and restore.
Live Jetson model admission and hosted edge/cloud execution remain unqualified.

## The next behavior

A mission starts the task frozen in the selected application release. A slower
planner proposes a supported skill; the edge coordinator validates the proposal
against the deployed skill catalog and task boundary. A compatible local policy
executes that skill through the robot adapter. The local coordinator owns
execution and its durable history.

For the first manipulation reference the catalog contains one qualified skill:
pick and place the puck in the pinned MetaWorld scene. The planner may select
that skill or decline. It cannot turn an arbitrary instruction into a newly
supported task, choose unqualified objects, or invent a controller interface.
A one-skill demonstration tests composition and admission; it is not evidence of
general robotic planning. Multi-step and multi-skill tasks require additional
policies/adapters and independent task qualification.

Free-form operator task input is outside this first slice. It needs durable
mission parameters and suites with expected outcomes for both execution and
valid declines. For the fixed supported task, a decline is an unsuccessful task
outcome; refusal of unsupported/malformed proposals is tested separately as an
admission invariant. Do not silently count a refusal as physical task success.

The target placement is an onboard action policy and a separate remote planner.
Development placement is reported explicitly: a model on a local Mac, another
LAN machine or a Jetson is not described as a hosted cloud deployment.

## Immutable compatibility boundary

Keep the current single-policy profiles working. Introduce a distinct paired
profile once its coordinator and worker paths can execute end to end. Its release
must resolve all of the following, rather than accepting two model names:

- Exact planner runtime, weights/configuration and prompt/parser artifacts.
- Exact action-policy runtime, weights, observation transforms and action decoder.
- Fixed task instruction, skill catalog, planner proposal schema, embodiment and
  pinned environment.
- Intended component placement and the separately qualified timing limits.
- Behavior when the planner is unavailable, declines, returns invalid output or
  misses its original deadline.

Endpoint addresses and credentials are deployment bindings, not embedded secrets
in the immutable release. Readiness checks the bound workers' actual identities
against the release. A change in policy, planner, prompt, parser, catalog,
placement or timing creates a new candidate requiring applicable evaluation.

## Execution and activation

Start with a planner call at mission admission, before any action. The complete
proposal has one bounded skill identifier and only the parameters the catalog
allows. Free-form text, partial streaming output and unknown keys cannot actuate.
The returned proposal is bound to the original mission, release, observation,
request, coordinator incarnation and deadline. The coordinator validates it and
journals acceptance before starting the skill. Restart ambiguity is reconciled;
the planner is not silently called again to regenerate an unrecorded decision.

Planning and policy decisions have separate local monotonic deadlines. A slow
planner does not extend a policy decision's lifetime. Once a skill is admitted,
its qualified local behavior may continue through planner loss until that skill
finishes or its own authorization expires. No additional skill may begin without
a valid proposal. Cancellation and authority changes retain the existing local
submission fence and unknown-outcome behavior.

Preparation probes both component identities without moving the robot. Activation
is at an acknowledged idle boundary and records the complete bundle. A partially
prepared bundle is blocked. A lost acknowledgement causes reconciliation with
the recorded generation and loaded identities. This is per-robot convergence,
not an atomic transaction across the network.

After a transient probe failure, the same queued, never-claimed mission may
regain readiness without changing its generation or original expiry. Server
reconciliation expires only requests for which no execution authority was ever
issued. An admitted or uncertain execution cannot be cleared by a readiness probe.

The API is the sole mission-grant signer. In asymmetric mode, the action worker
and planner have distinct public verification keys and purposes. The evaluation service has database
access but no model signing keys; it reconciles authorized runs through normal
deployments and missions. The coordinator holds a device credential and receives
short-lived mission grants. Model calls bypass the API and database. Remote
session cleanup happens after the local terminal record is durable, so a stalled
service cannot delay cancellation reporting.

The development harness runs API, action worker, planner and simulator/coordinator
as separate local processes; `--serve` also starts the evaluation job process
unless `--no-evaluation-job` leaves it to a caller that owns the job.
The explicit controlled planner has its own runtime identifier and local placement.
It makes a deterministic skill selection and does not stand in as evidence of LLM
inference. The real gateway adapter requires independently captured loaded-model,
runtime, binary, configuration and implementation hashes. See the
[planner package](../../../integrations/planner/README.md) for both entrypoints.

## The existing Jetson model

The historical Jetson installation serves a text LLM through the Convoy gateway.
First verify its live hardware, loaded release, model/runtime digests and health;
historical benchmark numbers do not establish its current state. It can qualify
text-to-skill proposals. It does not thereby become a compatible visual arm
policy. Installing an action policy requires its own ARM/CUDA/runtime, memory,
observation/action and timing checks on that device.

The existing gateway's public metadata identifies a legacy release but does not
by itself attest to the currently admitted weights, configuration and runtime
generation. Add and qualify that binding, or label an endpoint-only experiment
as such; a model alias or successful chat response cannot satisfy exact paired
readiness. Its current text API does not support tool calls or schema-enforced
output. The first adapter therefore needs an exact pinned prompt and a strict,
bounded parser that rejects extra text and unsupported proposals.

## Evidence before merging the executable slice

Use a real-process supported proposal plus actual learned-policy physics rollout.
Focused contract, HTTP-service and coordinator integration checks cover declined,
malformed, late and wrong-release proposals; planner loss before admission and
after the local skill begins; cancellation during planning and stalled session
I/O; coordinator restart with an accepted proposal; and component mismatch at
readiness. Keep model-quality cases separate from deterministic fault inputs.
The report joins both component identities, the planner decision, local action
history and the final task outcome. Existing single-policy and evaluation
behavior must remain functional.

No paid capacity is required for initial development. A hosted planner needs the
separate provider/account/region/budget decision and deployment evidence. Fleet
provisioning, arbitrary model pairing, a multi-skill planner and physical-robot
qualification are later scopes, not implied by this first profile.
