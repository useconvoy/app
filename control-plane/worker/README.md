# Action-policy inference worker

The worker is an independent FastAPI process; it does not open the management database. A trusted, installed runtime factory loads one policy and reports its runtime name and verified artifact SHA-256. The supplied release manifest must match both before startup succeeds.

```sh
uv sync --frozen --all-packages
# Configure separate, randomly generated secrets through the local environment:
# CONVOY_EXECUTION_SECRET and CONVOY_WORKER_PROBE_TOKEN (at least 32 characters).
uv run --frozen convoy-worker --release release.json --factory installed_module:factory
```

The factory returns an object with `runtime`, `artifact_sha256`, and `get_action(observation)`. It must hash the checkpoint actually loaded, not echo the release's expected hash. The initial supported contract is the pinned MetaWorld Sawyer profile: 39 finite state values in, four normalized displacement/gripper values out. Heavy simulator/model libraries are optional integrations; they are not worker dependencies.

`POST /v1/probe` requires the separate probe token and verifies release/profile readiness. It grants no execution authority. `POST /v1/decisions` requires a short-lived mission grant signed by the management service; every device/robot/mission/boot/incarnation/epoch/release field must match. An occupied worker rejects additional calls with 429 rather than queueing them. Invalid policy outputs fail closed. The robot still checks its own deadline and cancellation before applying any returned action.

The listener defaults to loopback. Remote deployment requires authenticated TLS ingress; do not expose its plain HTTP listener directly. The process does not forcibly interrupt a blocking model implementation, and its health route is not a latency qualification. Run `uv run --frozen pytest -q contracts/tests worker/tests` for the focused contract/admission checks.
