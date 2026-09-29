# Linux ARM64 planner image

This package turns the existing owned llama.cpp gateway and fixed-task planner
adapter into one CPU container. It carries the real pinned Qwen weights, native
runtime, planner service and optional process-ownership library. It contains no
Torch, LeRobot, MuJoCo or management server. The action policy, simulator and
device coordinator remain separate services.

The initial scope is **local Linux ARM64 qualification**. This does not provision
an AWS task, qualify a Jetson or establish WAN timing. Paired releases may declare
`development-local` or `development-remote-cpu` for this real CPU planner; the
action policy remains `development-local-cpu`. The remote declaration changes the
release and evaluation identity, but it is not evidence of hosted execution.
Controlled planner fixtures cannot use this declaration.

## Reproduce the artifact

Use Docker on a native Linux ARM64 host (including the Linux VM on an ARM Mac),
an existing clean llama.cpp checkout at
`5266f24da75dc449bd56cbed7addb9c8e4a6a73e`, and the existing verified Qwen text
asset receipt. Paths below are private directories outside the repository.
The helpers never download model weights or modify existing model/runtime assets.

```sh
python3 infra/planner/build_linux_native.py \
  --source /private/llama.cpp --output /private/native-build
python3 infra/planner/prepare_assets.py \
  --native-receipt /private/native-build/native-receipt.json \
  --model-receipt /private/text-assets/asset-receipt.json \
  --output /private/planner-assets
docker build --platform linux/arm64 \
  --build-context planner_assets=/private/planner-assets \
  -f infra/planner/planner.Dockerfile -t convoy-v1-linux-planner:local .
```

The native builder uses a pinned Debian ARM64 base and dated Debian package
snapshot. Compilation has no network access and is bounded to two CPUs and
3 GiB. `GGML_NATIVE=OFF` and explicit `armv8-a` avoid inheriting the development
host's instruction set. CUDA, Metal, OpenMP, runtime backend loading and optional
downloads are disabled. The archive includes only the executable, its required
llama/ggml libraries and build receipt, with relative loader paths. External system
libraries and symbol requirements are recorded separately. A glibc 2.36 build
baseline does not qualify a particular JetPack release.

The asset helper verifies the original archive and each binary/library pin, then
copies and rehashes the model/archive into a curated three-file Docker context.
The image uses the pinned Python base and frozen planner lock, installs the
`owned` extra, and verifies assets again. Baked assets are root-owned and read-only
to the service user; runtime ownership records live in a separate private directory.

## Inspect, bind and serve

The image entrypoint supports the same modes as `python -m convoy_planner.owned`.
Inspection needs no execution credentials or external network:

```sh
docker run --name convoy-planner-inspect --network none \
  --init --cpus 2 --memory 4g --cap-drop ALL \
  --security-opt no-new-privileges convoy-v1-linux-planner:local \
  inspect --assets /opt/convoy/assets/assets.json --output /run/convoy/state
docker cp convoy-planner-inspect:/run/convoy/state/ready.json /private/planner-identity.json
docker cp convoy-planner-inspect:/run/convoy/state/result.json /private/planner-stop.json
docker rm convoy-planner-inspect
```

Use the inspected `planner` object in an immutable paired release containing the
unchanged, previously qualified action child manifest. Inspect the descriptor and
cleanup evidence before binding it. This inspection loads and checks the actual
native model; a separate signed planning request establishes proposal inference.

Serving requires the paired manifest at `/run/convoy/release.json`, plus
`CONVOY_PLANNER_VERIFICATION_JSON` and `CONVOY_PLANNER_PROBE_TOKEN` injected by the
deployment system. The first value is **only** the API signer's public planner
verification document. Private API keys and action/HMAC authority are rejected.
Use secret/file injection, not command-line credential literals. The wrapper
consumes JSON into a private file; the standalone Python launcher accepts only
`CONVOY_PLANNER_VERIFICATION_KEYS_FILE` and the probe credential.

Deployments without a mounted release file can instead inject
`CONVOY_PLANNER_RELEASE_JSON` and `CONVOY_PLANNER_RELEASE_SHA256`. The digest must
be the SHA-256 of the canonical validated release JSON (`canonical_digest` from
`convoy_contracts.execution`), not the bytes of a pretty-printed file. Override
the container command with `serve --assets /opt/convoy/assets/assets.json
--output /run/convoy/state --host 0.0.0.0 --port 8080`, omitting `--manifest`.
The unchanged image default command remains the mounted-file workflow.

The wrapper consumes and removes both release variables, rejects JSON over
16 KiB, duplicate keys, non-finite values, digest mismatches and unsupported
releases, then writes a mode-0600 `/run/convoy/release.json` in the private runtime
directory. It accepts only the real paired CPU planner profile. Any `--manifest`
argument conflicts with injection, even if it names the default path. Existing
files and symlinks are never replaced. Incomplete injection or invalid inputs
fail before native startup with a bounded error. Inspection rejects release
injection, a manifest argument or execution credentials. The injected release
contains model identities and configuration; it never grants execution authority.

Only the planner endpoint listens on port 8080. Native inference and the internal
gateway remain on loopback with a private, per-launch native credential. Exposing
the planner outside local development requires verified HTTPS ingress and
restricted task networking. The image provides private HTTP for that ingress;
it does not create a TLS endpoint or disable certificate verification in clients.

`/ready` checks the owner's lifetime and fresh, exact native/gateway identity.
It is the container health check. The general adapter's `/health` endpoint is not
used as proof of a loaded model. A runtime failure closes admission and ends the
container; there is no automatic native restart or mission replay. Replacing a
container loses its in-memory sessions and produces a new incarnation.

Startup, including asset hashing, is bounded to 120 seconds by default. SIGTERM
during startup unwinds into the existing supervisor's verified cleanup. Serving
shutdown closes admission and shares a bounded cleanup deadline across the
planner, gateway and native child. Cleanup uncertainty is an error and remains in
`result.json`. A fresh private output directory is required; reusing a stopped
container's old state is not an implicit recovery operation.

## Regular checks

```sh
uv run --project integrations/planner --extra owned --frozen \
  pytest -q integrations/planner/tests infra/planner/tests
uv run --project integrations/planner --extra owned --frozen \
  ruff check integrations/planner infra/planner
```

These tests exercise protocol, authority, readiness, startup interruption and
cleanup boundaries without downloading models. Full image qualification is
separate and should run when changing the native runtime, model, image or owner:

```sh
uv run --project integrations/planner --extra owned --frozen \
  python infra/planner/verify_image.py --image convoy-v1-linux-planner:local \
  --action-manifest /private/qualified-action-manifest.json \
  --output /private/new-planner-proof
```

The [recorded qualification](../../docs/v1/linux-planner-image.md) passed on
clean `afe128b`: a real Qwen proposal, signed admission and replay rejection,
native loss, startup interruption and complete cleanup. It includes an ownership
fix proved with direct and recovered Linux thread-group regressions. The final
request took 12.58 seconds within its 30-second budget; this is not a low-latency
benchmark. Failed attempts remain preserved. No cloud, Jetson or physical
action-policy claim follows from this local planner response.

The [separate HTTPS planner qualification](../../docs/v1/network-planner.md)
now connects this image to the actual local SmolVLA/MuJoCo pipeline. Clean
`59f2754` passed one accepted Qwen plan and 54 successful learned actions, each
matching the direct seed-0 reference. The harness checks certificate trust,
private planner networking, exact live identity and complete cleanup. This is
local Docker networking, with TLS terminating at the proxy; cloud/WAN and Jetson
qualification remain separate.

The [release-injection qualification](../../docs/v1/remote-planner-packaging.md)
rebuilt this image after adding bounded release delivery and the remote CPU
declaration. Clean `a7e8bd2` passed another real network mission with
`--inject-release`: one accepted plan and 54 successful learned actions matching
the direct reference. Its new image/artifact identity must be used for a cloud
trial; the measured placement was still local Docker, not AWS.
