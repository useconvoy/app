# Local paired application activation

The optional local owner makes a deployment request load an installed Qwen and
SmolVLA pair for one simulated robot. This is the first concrete placement adapter:
one trusted developer machine, fixed installed launchers, and offline MuJoCo
manipulation. Arbitrary model architectures, remote provisioning and physical
robot control require separately qualified adapters.

## Process layout

The existing management API owns deployment generations, mission authorization
and durable results. The console and evaluation service use its existing APIs.
The paired coordinator owns the simulator and polls management; in local mode it
also manages these components:

- An embedded HTTP gateway and one foreground native llama-server process for
  the text model.
- A separate planner adapter process that converts a bounded text proposal into
  the fixed skill contract.
- A separate action worker process that loads SmolVLA and returns bounded actions
  from camera observations.

Model traffic stays on loopback. The two model services receive separate execution
and probe credentials; they do not receive the management login or database
credentials. The model worker is the process that imports the heavy inference
libraries. The coordinator remains the only process allowed to apply simulator
actions. No deployment action starts a mission automatically.

The legacy mode still accepts explicit worker/planner endpoints. Local ownership
is selected with `convoy_agent.coordinator.paired --local-registry PATH` and is
mutually exclusive with those endpoint flags. It requires the optional LeRobot
`paired` environment and the fixed visual simulator adapter. No cloud account is
needed for this placement.

## Recipes and loading

An operator-installed private registry lists complete immutable release manifests,
qualified native identities, existing local asset receipts and the two supported
CPU context configurations. The registry is keyed by the manifest digest. API
requests cannot supply executable commands, Python imports or model downloads.

For a different requested bundle, the owner validates current source/package
identity and model/runtime assets before interrupting the old bundle. It records
preparation intent, stops only verified owned components, starts the new processes,
and waits for matching model identities. Native and action weights stay in the
existing pinned cache; the small native runtime archive is extracted into a fresh
attempt directory. The action runtime retains its artifact-bound startup warmup;
the owner issues no planner proposal, action request or simulator step during
deployment preparation.

The coordinator waits for prior inference and session cleanup before replacement.
After loading and probing both components, it fetches fresh desired state. A
superseded deployment, cancelled request or admitted mission prevents the old
candidate from gaining execution authority. Both clients bind together and the
observed component identity is committed locally before the ready acknowledgement.
A subsequent poll obtains fresh authority before any mission claim.

Healthy components can be reused for another deployment of identical content.
Changed process identity forces fresh probes and a new binding. A failed candidate
cannot run through the old bundle; requesting another deployment is the explicit
retry path. An interrupted mission stays unknown and is never resumed by replacing
models. Historical local observations are evidence, not proof that a process is
still ready after a restart.

Restoring a release uses the ordinary deployment API and current expected
generation. It passes the same mission, evaluation reservation and promotion gates.
There is no generation rollback or automatic fallback hidden behind a candidate's
ready state.

## Qualification boundary

The first A/B candidates use the same Qwen2.5-1.5B-Instruct Q4_K_M and SmolVLA
weights. A uses context 2048; B uses context 4096. Their native configuration,
planner artifact and outer release digests differ. These are two configurations,
not two different weight checkpoints.

The [completed acceptance](../../examples/manipulation/evidence/local-paired-activation.json)
used one API, coordinator, journal and SQLite database. The harness deployed A,
then B, then restored A, explicitly starting a successful seed-0 mission each time.
The browser deployed B and started a fourth mission, evaluated A and B against
one immutable seed-0 suite, compared the reports, explicitly promoted B, and
restored A at generation 7 without starting another mission. No evaluation gate
was enabled, so the explicit restore did not need a promotion of A.

All six missions accepted an actual Qwen proposal and applied 54 SmolVLA actions.
Every action, reward and success flag matched the direct seed-0 reference.
Independent final checks covered seven bindings, 21 process incarnations, 28
model listeners and native credential-file removal. This proves the release
workflow on the fixed local task; it does not establish general reliability.
Focused faults also cover unknown recipes, corrupted assets before replacement,
partial startup, stale desired state and unresolved execution.

This path does not configure wireless access points, offer real-time physical
control guarantees, activate the Jetson, or provision a hosted environment. The
current console shows the requested release and acknowledged deployment state;
detailed observed-bundle UI and an explicit restore shortcut remain follow-ups.
Attempt records and logs are retained locally; artifact retention and multi-tenant
installation management remain separate work.

## Repeatable local acceptance

From the repository root, use the existing optional LeRobot `paired` Python
environment and already downloaded, pinned assets:

```sh
python examples/manipulation/local_activation.py \
  --text-assets /absolute/path/to/asset-receipt.json \
  --action-assets /absolute/path/to/verified-smolvla-assets \
  --output /absolute/path/to/a-new-private-run
```

The checked-in `examples/manipulation/evidence/native-qwen-local-activation.json`
supplies the qualified context-2048 and context-4096 native identities. The optional
`--gateway-qualification` argument selects another explicit qualification receipt;
the harness never rewrites its native pins to match the current code. The owner
revalidates the existing assets and running identities. No assets are downloaded.

The harness starts one management API and one coordinator. The coordinator owns
all model processes. It registers A and B through the API, deploys A, B, and A
again using current generations, and separately starts a seed-0 mission after
each readiness acknowledgement. The private run directory retains immutable
release manifests, accepted planner proposals, compact action traces, process
incarnations, owner attempts and final cleanup evidence. Failed attempts stay in
place; use a fresh output directory for every run.

Add `--serve` to **complete all three missions first**, then keep the restored A
deployment ready and idle. Its mode-0600 `connection.json` contains local login
credentials and both release IDs for subsequent console checks. Stop with Ctrl-C
or SIGTERM. Shutdown verifies owned process identities, all recorded listeners
and removal of native API credential files. Forced recovery or incomplete
graceful cleanup makes the acceptance fail even when the orphan processes can
be stopped safely. Never publish the private run directory or connection file.
