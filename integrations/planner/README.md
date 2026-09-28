# Fixed-task planner adapter

This optional process composes a text model already owned by the legacy Convoy
agent with the qualified visual pick-and-place policy. It selects the one fixed
catalog skill or declines it. It does not load a model, control a robot, perceive
the camera image, invent a task, or establish general planning performance.

The initial placement is the planner adapter beside the existing Jetson gateway
and the action policy/simulator on the development Mac. These are development
placements, not cloud or physical-robot qualification. Actual model quality and
device timing must be measured after this adapter is installed on the device.

## Install and bind

The package supports Python 3.10 and later and contains no Torch or simulator
dependencies. The checked-in lock resolves its optional HTTP service dependencies.
The existing agent must include the additive runtime-provenance gateway routes.
Keep it as the sole owner of the native model process and release lifecycle.

```sh
cd integrations/planner
uv sync --frozen --no-dev
uv run --frozen convoy-planner inspect --gateway-url http://127.0.0.1:8080
```

Use the returned `planner` object to create the paired application manifest.
`action_manifest` embeds the existing qualified visual release unchanged. The
paired profile also fixes the task, skill catalog, planner protocol, placement,
and original planning deadline; see `convoy_contracts.pairing`.

Provision `CONVOY_PLANNER_EXECUTION_SECRET` and `CONVOY_PLANNER_PROBE_TOKEN` through
the deployment secret mechanism. The planner execution key must differ from the
action worker key. The control plane signs a purpose-scoped `planner_grant` with
the planner key; the ordinary action grant does not authorize this service.

```sh
uv run --frozen convoy-planner serve \
  --gateway-url http://127.0.0.1:8080 --manifest /path/to/paired-manifest.json
```

The reference service binds loopback. A remote coordinator can use an explicitly
configured SSH tunnel or an independently configured HTTPS proxy. Endpoint URLs
and credentials are deployment bindings; they are not model artifacts. No tunnel
or external listener is created automatically.

## Protocol and ownership

`POST /v1/probe` authenticates with the probe token and accepts exactly
`{release_digest, profile}`. It verifies actual loaded model/runtime/template,
configuration and adapter source identity against the immutable bundle, and
returns the planner artifact, process incarnation, and runtime generation.

`POST /v1/sessions/start` with a planner grant and `{identity}` binds one mission
to that admitted runtime. Exact duplicate starts preserve the sequence and never
regenerate a plan. The start response includes `identity`, `next_sequence`,
`planner_artifact_sha256`, `planner_incarnation`, and `runtime_generation`.

`POST /v1/plans` accepts exactly `identity`, `request_id`, `observation_id`,
`observation_digest`, `deadline_monotonic_ns`, and `budget_ms`. The original
coordinator monotonic deadline is opaque on this host and echoed unchanged. The
local budget and signed mission expiry independently bound this service. The
observation digest binds the coordinator's admission snapshot; the text model
does not receive or interpret its image. Task text and prompts come only from the
pinned release protocol. There are no HTTP retries, redirects, streaming results,
JSON repair, extra catalog parameters, or queued inference.

The response echoes the request identity/context/deadline, adds the three planner
identity fields, and returns `decision` plus `planner_duration_ms`. The only
decisions are `{"kind":"skill","skill_id":"pick_place_puck","parameters":{}}`
and `{"kind":"decline","reason":"unsupported_task"}`. A decline is unsuccessful
for the required fixed task. The coordinator must validate identity and its own
original deadline, then journal admission before any action-policy session or
simulator step. A model failure or ambiguous result cannot be regenerated in the
same mission.

`POST /v1/sessions/end` accepts `{identity}` with its original planner grant. It
fences an in-flight result and tombstones the mission until grant expiry, even if
end arrives before start. It never waits behind model inference. The bounded
retention table reserves capacity for closing its active owner.

The gateway's `/v1/bound-completions` checks the expected captured launch identity
inside the existing inference slot and rejects mode/epoch, generation or identity
changes before returning. This adapter neither stops nor restarts the runtime;
the existing agent handles uncertain native completion and controlled recovery.

## Verification

```sh
uv run --frozen pytest -q
uv run --frozen ruff check src tests
```

The focused tests use deterministic test doubles for planner outcomes and explicit
faults. They cover strict parsing, source/model identity, one plan per mission,
generation changes, readiness mismatch, cancellation, expiry and transport loss.
They do not establish a real text model's success rate. Separate managed evidence
must report the actual device/runtime identity, admitted plan and physics rollout.

## Controlled composition harness

The separate `convoy-controlled-text-skill-v1` runtime always selects the single
qualified skill. Its artifact records the executing fixture source and explicitly
contains no model weights or native binary identity. The real gateway path still
requires complete verified model provenance. Controlled releases declare planner
placement `development-local-controlled` and evidence kind
`controlled-text-planner`; they cannot share a planner artifact with the real LLM.

With the existing SmolVLA assets already present, run from the repository root:

```sh
uv run --project integrations/lerobot --extra paired --frozen \
  python examples/manipulation/paired.py --output /path/to/fresh-private-evidence
```

This starts the API, controlled planner, pretrained SmolVLA action worker and
MuJoCo coordinator as separate processes. `--serve` instead leaves a ready, idle
stack with the evaluation worker for console use. `--postgres` uses the existing
disposable-database mechanism; it requires its explicit configured test endpoint.
The wrapper never downloads model weights. `paired-result.json` joins accepted
durable plans with actual action/physics outcomes and keeps the controlled planner
label. An optional direct report/trace comparison checks that policy actions still
match the prior qualified seed0 rollout.

The [recorded clean-source run](../../examples/manipulation/evidence/paired-controlled-seed0.json)
accepted one controlled plan and reached benchmark success in 54 actual SmolVLA
actions, all equal to the original direct trace. It took 44.36 wall seconds for
0.675 simulated seconds. This qualifies the fixed-task integration, not a learned
planner, Jetson deployment, real-time control or sustained physical placement.
