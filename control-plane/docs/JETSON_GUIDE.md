# Jetson Orin Nano guide

> **Status (2026-09-13).** This guide has now been run once, end to end, on the project's own Jetson Orin
> Nano Developer Kit Super (section 9 records exactly what was measured and by whom). The measured board
> tuple is **L4T 36.4.7 / CUDA 12.6.11 / Orin SM 8.7** (track `l4t3647`); the other supported recipe
> track (`jp623`, L4T 36.5.2) is source-verified but has not been run on hardware. Every number in this
> guide is either a measurement from that board with its operation or eval id, or a labelled hypothesis
> (memory reserves, build time). Sections 9.8–9.11 record the reconciled evidence recovered from the
> board after its network path was lost: the fifteen-minute soak on the retained Qwen2.5 completed
> locally (910.012 s, 49 batches, 3,332 HTTP 200, 1,617/1,666 timed quality) and its records were
> reconciled afterwards; a 2.0 s managed-deadline trial was run as a separate lab configuration; one
> agent stop was **forced** (SIGKILL at the 45 s limit) so clean shutdown is not claimed; automatic
> backup acceptance on the 34c control plane **did not pass** (two bounded deferrals) and was later
> **accepted on the 5b7af1c WAL control plane with the ten simulators** while this board was stationary
> and unreachable (9.10), so all-eleven continuity is not claimed. Section 9.11 lists
> what remains pending, and the board is unreachable from the operator's Mac at the time of writing
> (last report 17:12:33.277626 UTC, seq 2538; a lost path, not a board failure).
> Simulated devices never qualify a physical release, and the simulated fleet on the same control plane
> is evidence for nothing in this guide.

This guide is served in the app under **Settings -> Jetson guide** (`GET /api/v1/docs/jetson-guide`).

## 1. Prerequisites

Two supported CUDA 12.6 / SM 8.7 target tuples exist (section 4 explains the tracks). Only one has
been measured:

| | measured board (track `l4t3647`) | other supported track (`jp623`) |
|---|---|---|
| hardware | Jetson Orin Nano Developer Kit **Super**, 8 GB unified memory (7619 MiB reported), `jetson-orin-nano-8gb` profile | Jetson Orin Nano Developer Kit, 8 GB, same profile |
| L4T | **36.4.7** (`/etc/nv_tegra_release`; no `nvidia-jetpack` metapackage, so no JetPack version is asserted) | 36.5.2 (JetPack 6.2.3) |
| CUDA | toolkit **12.6.11**, `nvcc` 12.6.68 | 12.6 |
| GPU | `nvidia-smi --query-gpu=name,compute_cap` → **Orin (nvgpu), 8.7**, measured by the agent's service user | SM 8.7 (source-verified, not measured) |
| OS / Python | Ubuntu 22.04, Python 3.10.12 | Ubuntu 22.04, Python 3.10 |
| power mode | `MAXN_SUPER`, read only: the scripts never change it | not measured |
| status | agent installed, enrolled, native runtime built, registered and deployed with CUDA offload (section 9) | source-verified pin, physically untested |

- JetPack 7.x (L4T 39.x, Ubuntu 24.04) is a **separate, unsupported track**: the server's compatibility
  matrix does not list it, so devices reporting L4T 39 get no physical release until that track exists.
- NVMe or a fast SD card with at least 6 GB free (model 1.1 GB + runtime archive + build tree ~3 GB).
- Outbound HTTPS to the Convoy server and to `huggingface.co` (model download). **No inbound ports.**
- Build tools for the runtime: `git cmake build-essential binutils python3-venv`, plus the JetPack CUDA
  toolkit (`/usr/local/cuda/bin/nvcc`). Not needed on devices that only *receive* a fleet artifact.

How the agent detects the platform (`agent/convoy_agent/hardware.py`):

- `/etc/nv_tegra_release` -> `l4t_release` (full string, e.g. `36.5.2`) and `l4t_major`; matching uses
  the **full** release string.
- `/usr/local/cuda/version.json` -> `cuda_version` (fallback `/usr/local/cuda/version.txt`), and whether
  `nvcc` is on `PATH`.
- `/proc/device-tree/model` -> `jetson_model`; `tegrastats` presence -> power/thermal telemetry;
  `nvidia-smi` presence is recorded but is **never** treated as inference evidence (Jetson may lack it).
- Memory total/available and disk free are read live; unknown values stay `null` and the budget verdict
  becomes `unknown`, never `fit`.

## 2. Install the agent

The agent is stdlib-only Python (3.10+), runs as an unprivileged `systemd` service and keeps its state
in `/var/lib/convoy-agent` (mode 0700). From a checkout of this repository on the Jetson:

```bash
sudo scripts/jetson/install-agent.sh
```

What it does (idempotent; re-run to upgrade):

- creates the system user `convoy-agent` (no login shell) and adds it to `video` (and `render` when
  present) for GPU device access;
- creates `/var/lib/convoy-agent` (0700, owned by `convoy-agent`);
- installs the agent into `/opt/convoy-agent/venv` with `pip install --no-deps` and proves no
  third-party packages were pulled in;
- installs `deploy/systemd/convoy-agent.service` (Restart=always, `NoNewPrivileges`,
  `ProtectSystem=strict` + `ReadWritePaths=/var/lib/convoy-agent`, `PrivateTmp`, `ProtectHome`,
  `RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX`, `MemoryMax=6G`) and enables it. The unit has
  `ConditionPathExists=/var/lib/convoy-agent/credential`, so it does nothing until you enroll.

Status: installed on the project's board from checkpoint 5fa9422 and running as the hardened service
since; the unit's `ProtectClock` correction (ce0cd2e, section 9.2) was applied by re-running the
installer, which is the supported upgrade path. **An already-working board needs neither a
re-install nor a re-enrollment for a documentation update**: re-run `install-agent.sh` only to move
to a newer agent checkpoint (it keeps `/var/lib/convoy-agent`, the credential and the journal).
`MemoryMax` is a starting point: cgroup accounting does not necessarily cover GPU-mapped allocations on
Tegra; the first deployment ran under it without hitting the limit, which is not a sizing measurement.

**Stopping the service.** `systemctl stop` sends SIGTERM to the agent only (`KillMode=mixed`,
`TimeoutStopSec=45s`). The agent stops under one 35 s budget (10 s margin before the unit's SIGKILL):
gateway admission closes at once, a control-plane request blocked in a TLS handshake or a stalled
response is woken and no new attempt, retry or dispatch starts, the cancelled operation thread is
joined within its share (an operation still before its grant is deferred and resumed at the next
start), the owned `llama-server` child is stopped and its exit verified, gateway requests still
running finish so their accounting lands, the final usage record is written, and only then the journal
is closed and the data-directory lock released. Nothing is uploaded during a stop: queued evidence and
the terminal outcome of a cancelled operation stay in the journal and are replayed by the next start.
The idle case with a dead control-plane route measured well under a second in the repository tests;
the shutdown summary in the log states each part's duration and names any bound that was not met.
**Pending: the physical offline-stop retest.** The two ~45 s stops recorded in section 9 (one ended
in SIGKILL) happened on agent 5fa9422; the bounded shutdown above has been exercised by repository
tests with a real agent process only. It is not accepted until the operator repeats the stop on the
board under a dead control-plane route and records the measured duration and exit code.

## 3. Enroll the device

Enrollment is a one-time act per device: the project's board was enrolled once and its device id,
journal, generations and history have been preserved through every later agent upgrade, the unit
correction and an unplanned reboot. Do not re-enroll a working device; the only re-enrollment path is
the **rebind** token described at the end of this section.

1. In the UI: **Fleet -> Enroll device**. Give it a label and group; leave *simulated* unticked. The
   dialog shows a **one-use enrollment token** (default TTL 1 h) and the exact command.
2. On the Jetson, as the service user:

```bash
sudo -u convoy-agent /opt/convoy-agent/venv/bin/convoy-agent enroll \
  --server https://<convoy-host> --token <token> --name <device-name>
sudo systemctl start convoy-agent
journalctl -u convoy-agent -f
```

What enrollment does (`docs/PROTOCOL.md`): the agent generates a 32-byte secret locally, writes it to
`/var/lib/convoy-agent/credential` with mode **0600** *before* the request, and sends
`request_id` + `sha256(secret)` with the token. Token consumption and device creation are one server
transaction; retrying after a lost response returns the same device id (`replay: true`). The secret is
the device's bearer credential from then on (`cvd_<device_id>_<secret>`); the server stores only its
hash. `/var/lib/convoy-agent/agent.json` holds the non-secret settings (server URL, device id, name,
optional `--ca-file`).

TLS trust: a server behind Caddy's internal CA (LAN deployments, `docs/HOSTING.md`) needs the CA
root on the device: copy the exported `convoy-root.crt` to `/etc/convoy/convoy-root.crt` (0644) and
pass `--ca-file /etc/convoy/convoy-root.crt`. The server's `CONVOY_DOMAIN` must be the exact name/IP
in `--server`, otherwise hostname verification fails. `--insecure` skips verification and is for a
first bring-up on an isolated bench only, never a default.

A successful `enroll` is followed by `systemctl start convoy-agent` (section 2); the service does
nothing until the credential exists. An enrollment token is consumed by the enrollment that succeeded:
do not reuse it for another device or a second enrollment. The only legitimate re-runs are the
unresolved same-state retry (the first attempt got no response; the agent replays the identical
request with its stored secret, section 3 of `docs/PROTOCOL.md`) and a fresh, bound **rebind** token.
Re-enrollment after a credential revocation or a server restore uses a **rebind** token (created from
the device page); it is bound to the existing device id, so history and the local journal survive.
`enroll` rewrites the stored settings, so the rebind command must repeat `--ca-file` (an omitted
`--ca-file` resets the trust to the system store and the agent then fails to connect).

## 4. Build and register the runtime (native CUDA)

Convoy never ships a prebuilt CUDA binary. A **build recipe** (llama.cpp commit + CMake flags + target
tuple) is turned into a **runtime artifact** by building on a Jetson and registering the build
receipt. Pinned recipe:

- llama.cpp `v0.4.0` = `5266f24da75dc449bd56cbed7addb9c8e4a6a73e`
- CMake flags (exactly, `DEFAULT_CMAKE` in `server/convoy_server/services/catalog.py`):
  `-DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=87 -DCMAKE_BUILD_TYPE=Release -DLLAMA_BUILD_TESTS=OFF
  -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_SERVER=ON -DLLAMA_CURL=OFF -DGGML_NATIVE=OFF
  -DLLAMA_USE_PREBUILT_UI=OFF -DLLAMA_BUILD_UI=OFF` (the last two keep the mutable prebuilt web UI
  out of the build; the agent also starts the server with `--no-webui`).

Two allowed CUDA 12.6 / SM 8.7 **tracks** share this recipe body and differ only in the L4T release
the build receipt must prove (`CUDA_TRACKS` in `catalog.py`, `support_matrix.json`):

| track | JetPack | L4T | CUDA | status |
|---|---|---|---|---|
| `jp623` (default) | 6.2.3 | 36.5.2 | 12.6 | original source-verified pin; not run on hardware |
| `l4t3647` | not established | 36.4.7 | 12.6 | the project's Orin Nano Developer Kit Super (2026-09-13); no `nvidia-jetpack` metapackage and NVIDIA lists JetPack 6.2.2 with Jetson Linux 36.5, so the track is named by the observed L4T release and asserts no JetPack version. Built natively, registered and deployed with CUDA offload on that board (section 9.3, 9.4) |

A recipe pins one track (`POST /api/v1/recipes {"name": ..., "commit": ..., "tag": "v0.4.0",
"backend": "cuda", "track": "l4t3647"}`); the build script is told the same track; an artifact built on
one track is refused under a recipe of the other. The scripts never change the power mode, never
flash or update the OS, and only read the host tuple.

Both tracks share the `jetson-orin-nano-8gb` hardware profile, so the profile alone cannot tell them
apart. The **platform tuple gate** does: a physical device must have *reported* the architecture, the
full L4T release string, the CUDA major.minor and the measured compute capability
(`nvidia-smi --query-gpu=name,compute_cap`, read by the agent into its inventory) equal to the
release's `platform.target`. The server refuses the operation at creation and again at grant issue
(409 `platform tuple: …`), and the agent refuses at preflight (before any bytes move) and once more
at cutover with a fresh inventory read, taken *before* the final grant-TTL check so a slow read can
never hide an expired grant. An unknown tuple, an unmeasured SM against a pinned SM, and a release of
the other track are all refused; simulated devices carry no CUDA tuple and are unaffected.

```bash
# 0. what the selected track requires (no host checks, no side effects)
scripts/jetson/build-llama-cpp.sh --track l4t3647 --print-track

# 0b. validated incremental build in an existing tree (for example to repackage after a track rename): no
#     configure, no clean; the tree must be the pinned clean source with exactly the recipe's cache entries
#     and the toolchain CMake recorded (full nvcc version, the recorded C++ compiler's own version); then
#     `cmake --build` runs (no-op when complete) and is recorded in the receipt, the identity is revalidated,
#     and the same packaging/startup checks/receipt follow. Run it only after the original build has exited.
scripts/jetson/build-llama-cpp.sh --track l4t3647 --reuse-build

# 1. build + package (clean build directory; ~1-2 h on an Orin Nano is a guess, not a measurement)
CONVOY_BUILD_JOBS=4 scripts/jetson/build-llama-cpp.sh --track l4t3647   # the board's L4T 36.4.7 track
#    -> ~/convoy-build/out/runtime-5266f24d...-aarch64.tar.gz and receipt.json (receipt records the track)

# 2. upload + register for the fleet (operator API token from Settings -> API tokens; recipe id from
#    Releases -> Recipes). --ca-file as in section 3 when the server uses the internal CA.
CONVOY_API_TOKEN=cva_... scripts/jetson/register-runtime.sh \
  --server https://<convoy-host> --recipe rcp_... --ca-file /etc/convoy/convoy-root.crt \
  --archive ~/convoy-build/out/runtime-*.tar.gz --receipt ~/convoy-build/out/receipt.json
#    -> artifact_id=art_...   (scope fleet, storage server, archive_verified)

# 2b. alternative, this device only: no upload; the archive is installed into the agent cache
#     (/var/lib/convoy-agent/cache/runtime/<sha256>.tar.gz, owned by convoy-agent) where the executor
#     looks before downloading, and registered with scope device:<this id>, storage device (receipt_only).
sudo CONVOY_API_TOKEN=cva_... scripts/jetson/register-runtime.sh --storage device \
  --server https://<convoy-host> --recipe rcp_... --ca-file /etc/convoy/convoy-root.crt \
  --archive ~/convoy-build/out/runtime-*.tar.gz --receipt ~/convoy-build/out/receipt.json

# 3. in the UI, create a NEW release binding the recipe + artifact (a build_required release is never mutated)
```

Fixed-recipe provenance: the build script derives the host tuple the way the agent does
(`/etc/nv_tegra_release` -> `36.4.7` or `36.5.2`, `/usr/local/cuda/version.json` -> `12.6`) and **fails** when it
differs from the selected track's tuple (select the matching track instead of overriding). `--allow-tuple-mismatch` lets a different track build anyway, but the
receipt then records `tuple_mismatch_allowed: true` and `target_observed`, `register-runtime.sh`
refuses it unless given the same flag, and the server checks the receipt tuple against the recipe at
registration. `register-runtime.sh` also refuses a receipt whose commit differs from the recipe.
The CMake invocation passes `-DCMAKE_CUDA_ARCHITECTURES:STRING=87` (typed, so the cache entry is a
STRING rather than UNINITIALIZED); the receipt keeps the recipe's flag strings as identity
(`cmake_flags`) and the actual invocation separately (`cmake_invocation`).

What `build-llama-cpp.sh` verifies and records:

- `HEAD == 5266f24d…`, clean source tree, CMake cache shows `GGML_CUDA=ON` and architectures `87`;
- the complete shared-library closure from `ldd bin/llama-server` (`libllama-server-impl`,
  `libllama-common`, `libmtmd`, `libllama`, `libggml`, `libggml-cpu`, `libggml-base`, `libggml-cuda`,
  and anything else the build produced) is copied into `lib/` as **regular files** under their real
  names, SONAMEs and NEEDED names (the agent's extractor rejects symlinks); CUDA runtime/driver and
  glibc libraries found under `/usr/lib`, `/lib` or `/usr/local/cuda*` are **not bundled** and are
  listed in the receipt's `system_dependencies`;
- an out-of-tree smoke test with the build directory **hidden** so `RPATH/RUNPATH` cannot mask a
  missing library: `LD_LIBRARY_PATH=<pkg>/lib <pkg>/bin/llama-server --version` must mention the
  commit and `--help` must run; `ldd` must resolve every library to the package or a system prefix;
- sha256 and size of every file, the archive sha256/size, canonical member names (`bin/llama-server`,
  `lib/<name>`, no `./`, no duplicates), and provenance (commit, tag, CMake/gcc/nvcc/CUDA versions,
  full L4T line, Jetson model, build date, host arch, `--version` output);
- `receipt.json` matches the server's strict receipt schema, and the archive is re-inspected the way the
  server does before registration. The server re-hashes the uploaded bytes and every member against the
  receipt; fleet artifacts become `archive_verified`.

Status: run on the project's board; the exact archive identity, receipt facts and registration ids
are in section 9.3, and the artifact has since served a CUDA deployment (9.4). **Do not rebuild on a
board whose artifact is already registered**: a rebuild produces a new archive identity and a new
artifact, never a replacement of the registered one. The receipt/archive format and
`register-runtime.sh` were first exercised in software against a local server with a placeholder
archive (recipe tuple check, upload, register, idempotent re-run, device-cache install, mismatch
refusal).

Backend evidence is collected when the agent starts the runtime: `/props.build_info`, the
`load_tensors` offload lines in the runtime log (`--verbosity 4`), and the intended-backend gate. A
build without GPU offload is recorded as `RUNTIME_BACKEND_MISMATCH` and fails qualification; sensor
or `nvidia-smi` presence never counts as inference proof.

## 5. Model provenance

The baseline model is **supervisor-supplied** and hash-pinned. The **device verifies it on download**
(sha256 and size must match before the file is renamed into the cache); the project's board did so
before its first deployment (section 9.4):

- repo `Qwen/Qwen2.5-1.5B-Instruct-GGUF`, commit `91cad51170dc346986eccefdc2dd33a9da36ead9`
- file `qwen2.5-1.5b-instruct-q4_k_m.gguf`, **1117320736** bytes
- sha256 `6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e`

Two further public, ungated candidates were pinned the same way for the comparison in section 9.6
(their full hashes are on their release pages; the release is the record, not this guide):
`Qwen/Qwen3-0.6B-GGUF` Q8_0 (sha256 prefix `9465e63a`) and `Qwen/Qwen3-4B-GGUF` Q4_K_M (sha256 prefix
`7485fe6f`), both with the reviewed non-thinking chat template in `docs/templates/` bound by hash.
Convoy never distributes Hugging Face tokens; only public, ungated files are used.

Downloads are HTTPS only, to `huggingface.co/<owner>/<name>/resolve/<40-hex commit>/<file>.gguf`
(redirects only to Hugging Face CDN hosts), resumable, fsynced and renamed atomically into
`/var/lib/convoy-agent/cache/models/<sha256>.gguf`. GGUF metadata is parsed with bounded readers; a
model without an embedded chat template needs a reviewed template file in the release.

## 5a. Model metadata and admission (what is advisory, what is authoritative)

A release records the model file's GGUF view with its **provenance** (`model.gguf_provenance`):

- `blob` (fixtures): read from stored bytes, byte-verified;
- `header_range_fetch` (Hugging Face / supplied): the server inspects the header at the pinned commit
  URL by bounded HTTP range requests (never more than 32 MiB, tensors are never fetched). Advisory:
  those bytes are not hash-verified;
- `operator_supplied`: counts given with the release when no header could be inspected; a supplied
  view that contradicts an inspected header is refused (422);
- `unavailable`: nothing could be inspected; the release is still created and its budget says
  "pending byte inspection" instead of a number.

On the device the manifest's counts are never trusted for the decision. `preflight` is cheap
eligibility (live meminfo, conservative disk room, policy/tuple/backend/artifact; the only memory
refusal is a release that cannot fit MemTotal at all). `stage` downloads and sha256-verifies the file,
reads its own header and **refuses a manifest whose counts contradict it** while the incumbent keeps
serving. `admission` sizes the KV cache from the verified header, credits the incumbent only by its
measured resident set, and is still provisional. The authoritative gate is in `cutover`: after the
incumbent has really exited, MemAvailable is re-read and the candidate is not started if it does not
fit (`CUTOVER_MEMORY`, followed by recovery of the incumbent). Releases are never mutated by any of
this; the device's view lives in the operation evidence.

## 5b. Benchmark through the gateway

`scripts/jetson/bench_gateway.py` (stdlib, Python 3.10; full guide in `docs/BENCHMARK.md`) drives
the loopback gateway only, never `llama-server` directly, and never actuates anything. Modes:
`--smoke` (the frozen 8-case deployment smoke, `bench_smoke_v1.jsonl`), the default task mode over
the frozen strict fixture `bench_cases_v1.jsonl` (34 synthetic cases scored `label | exact |
json_exact`; not customer acceptance; not tuned after results), and `--matrix` (input bands of
about 64/256/1024 rendered tokens x output caps 16/64/128 x 3 repeats per probe, actual prompt
tokens recorded per cell). Every attempted request, warmups included, is persisted to
`records.jsonl` as it completes with its exact request, bounded complete raw response, both trace
ids, finish reason and typed failure; an interrupted run still yields a partial report. TTFT is the
gateway's measured value or unavailable; TPOT only from the runtime's generation timings; gateway
latency and client wall time are reported separately; every percentile carries its sample size and
pooled mixtures are labelled as such. Optional evidence via the control-plane API: telemetry
snapshots and per-trace spans with requested/nonempty/empty/error counts and a bounded
reconciliation. `--render-check --template <file>` verifies a pinned template through the runtime's
`/apply-template` and `/tokenize` against the complete expected rendering (diagnostic; see
`docs/templates/README.md`).

## 6. Memory budget and cutover

Unified memory: CPU, GPU and the robot stack share the same 8 GB pool. The server computes a budget per
release and device (`GET /api/v1/releases/{id}/budget/{device_id}`) from **measured** values:

- weights: 1117320736 bytes -> ~1066 MB resident (`--gpu-layers all`, `--fit off`);
- KV cache for `--ctx-size 2048`, `--parallel 1`, computed from GGUF metadata (layers, KV heads, head
  size); prompt-cache growth cap is `--cache-ram 0` (a **cap**, not a preallocation);
- runtime overhead **700 MB** (hypothesis), robot reserve **1536 MB** (default, per-device setting),
  margin **512 MB** (default, per-device setting), disk margin 1024 MB;
- `mem_total_mb` / `mem_available_mb` from the device's last live report; a stale measurement (older
  than the live-budget age) or unknown values give `unknown`, never `fit`.

**Budget policy (one contract, server and device).** The required memory is
`model weights + KV cache + compute buffer + runtime_overhead_mb + robot_reserve_mb + margin_mb`, where
`KV cache = 2 * n_layers * n_kv_heads * head_dim * 2 bytes * ctx_size * parallel` (f16 K and V,
`kv_bytes_per_element = 2`) and `compute buffer = ((n_vocab or 152064) * ubatch + n_embd * ubatch * 16)
* 4 bytes` with `ubatch = config.ubatch_size` (128). Provenance order: `robot_reserve_mb` and `margin_mb`
come from the device settings, else the release `budget`, else the hardware profile; `runtime_overhead_mb`
from the release `budget`, else the profile (Jetson profile defaults: 700 overhead, 1536 reserve, 512
margin). `convoy_server.hardware.effective_budget` computes exactly this and is the single source used
by the planner (`GET .../budget/{device_id}` shows it as `effective_budget`) and by dispatch: every
deploy and recover operation carries the same values in its payload as
`effective_budget: {runtime_overhead_mb, robot_reserve_mb, margin_mb, ubatch_size, kv_bytes_per_element,
source: {<field>: "device.settings" | "release.budget" | "profile"}}` (ints; the device consumes these
numbers rather than re-deriving defaults). Raising a device's `robot_reserve_mb` raises the requirement by
the same amount in both places; the model counts themselves always come from the device's verified header.

Fixed runtime flags (`agent/convoy_agent/runtime_args.py`): `--fit off --parallel 1 --ctx-size 2048
--n-predict 128 --batch-size 256 --ubatch-size 128 --gpu-layers all --cache-ram 0 --no-context-shift
--flash-attn auto --offline --verbosity 4 --jinja --metrics --props --slots --no-webui --cors-origins ""
--no-cors-credentials --api-key-file <0600 file>` plus `--model`, `--chat-template-file`, `--host
127.0.0.1`, `--port`. The child is launched with a scrubbed environment (`PATH`, `HOME`, `LANG`,
`LC_ALL`, `LD_LIBRARY_PATH`, `CUDA_VISIBLE_DEVICES`, `TMPDIR` only; every `LLAMA_*`, `HF_*`, `GGML_*`
removed).

Cutover is **stop/start**: two runtimes never co-reside in memory. Sequence: bytes are staged while the
current release serves -> the agent asks for a grant (TTL 30 s by default, bounded by the maintenance
window) -> gateway closes (**503**) -> current child is stopped and its exit verified -> candidate starts
-> health + backend evidence -> eval mode -> production -> probation. The downtime is **measured** by
the agent and reported in the operation outcome: the board's first CUDA deployment measured
**7701.7 ms** (one operation, section 9.4; not a fleet figure). Failure at any step restores the
retained recovery release (two persisted attempts, then `Degraded`), and a failed-generation latch
blocks retries until a strictly newer operation. Both refusal paths have now happened on the board:
an eval-gate failure after a healthy CUDA start (9.6, Qwen3-0.6B) and a `CUTOVER_MEMORY` refusal after
the incumbent had stopped, with the candidate never started (9.6, Qwen3-4B); each ended with the
retained Qwen2.5 relaunched in one attempt.

## 7. Diagnosis

Everything below runs **on the Jetson**. The gateway is loopback-only; none of these URLs are reachable
from the hosted UI or a browser on another machine, by design.

```bash
# service and agent log
systemctl status convoy-agent
journalctl -u convoy-agent -n 200 --no-pager
journalctl -u convoy-agent -f

# local journal: active/recovery release, generation, health, current operation, spool lanes
sudo -u convoy-agent /opt/convoy-agent/venv/bin/convoy-agent status

# runtime (llama-server) logs, one per launch, last 5 kept; prompts are never logged
ls -l /var/lib/convoy-agent/runtime/runtime.*.log
tail -n 100 /var/lib/convoy-agent/runtime/runtime.<n>.log

# platform detection the agent reports
head -n1 /etc/nv_tegra_release; cat /usr/local/cuda/version.json; tegrastats --interval 1000 | head -n1
```

Gateway and runtime checks (on the Jetson; the gateway port is printed in the agent log line
`agent … up: … gateway=127.0.0.1:<port>`; pin it with `convoy-agent run --gateway-port <port>` or
`gateway_port` in `agent.json`):

```bash
# gateway health-ish probe: an empty POST is rejected with a JSON error, which proves the gateway is up
curl -s -X POST http://127.0.0.1:<gateway-port>/v1/chat/completions -H 'Content-Type: application/json' -d '{}'

# the llama-server itself listens on a separate loopback port with a per-launch API key
# (/var/lib/convoy-agent/runtime/runtime.key, 0600, deleted on stop); its port is in the runtime log.
sudo -u convoy-agent cat /var/lib/convoy-agent/runtime/runtime.key   # only for local debugging
curl -s -H "Authorization: Bearer $(sudo -u convoy-agent cat /var/lib/convoy-agent/runtime/runtime.key)" \
  http://127.0.0.1:<runtime-port>/health
```

### 7a. The project's bench transport (how this board reaches the control plane; not a product feature)

The control plane for this milestone is the preserved Compose installation on the operator's Mac, the
same one that hosts the ten simulated devices. Three different `localhost`s are involved, and none of
them is a Convoy feature:

| where | address | what it is |
|---|---|---|
| operator's browser, on the Mac | `http://localhost:18083` | the Compose API's plain-HTTP loopback port: the console the operator uses |
| the Convoy server, on the Mac | `https://localhost:18445` | the TLS port with the installation's private CA (`docs/HOSTING.md`) |
| the Jetson agent's server URL | `https://localhost:18445` **on the Jetson** | a loopback-only **reverse SSH forward** from the Jetson's own loopback to the Mac's TLS port. The agent still validates the certificate (`--ca-file` with the private CA root); nothing is exposed on any network interface of either machine |
| robot / benchmark client, on the Jetson | `http://127.0.0.1:9070` | the agent's loopback gateway (section 8; `gateway_port` pinned in `agent.json`) |

The SSH forward was opened by hand: first over the USB link used for bring-up, later over Wi-Fi. It is
a transport the operator owns, so it does not survive on its own: after the unplanned reboot in 9.5
the **agent recovered by itself** (it re-verified the retained release's bytes, passed launch
admission and relaunched Qwen2.5 with a fresh child and production gateway) while the **reverse
forward had to be reopened manually** before the device could report again. Convoy's recovery covers
the runtime it supervises; it is not network failover, and a production device should reach its
server over a route that comes back without an operator. When the board became unreachable during
the soak (9.8), the control plane saw exactly what it is designed to see: the device's last report
time and status `offline`, with its last-known active release and generation preserved; the console
cannot tell a lost transport from a powered-off board, and this guide does not guess either.

Status of this transport on 2026-09-13 (a dated observation, not durable access): the Wi-Fi BSSID pin
that had been tried was rolled back to its original empty value; other-AP associations and DHCP
interruptions continued regardless, and the driver/AP cause is unproven. After a later boot
(`c3fee69e-7ef0-4ebf-ad1c-2b0bdfa2a6a4`) the agent retained the baseline release by itself. USB first
restored the strict-host-key, CA-verified reverse tunnel; at 17:10:10 UTC the reverse forward moved to
a Wi-Fi IPv6 SSH master (TLS health and report seq 2529 verified; Wi-Fi verified after USB was unplugged
at 17:11:25 UTC; the USB master stays open with its forward cancelled; Ethernet NO-CARRIER). The Mac then
left the home LAN, USB is disconnected and every known Jetson IPv4/mDNS/IPv6 path times out; the **last
physical report is 17:12:33.277626 UTC, seq 2538**. This is loss of the verified path, not evidence
about the board and not attributable to Jetson Wi-Fi instability; the earlier handoff is historical, not
a current connection. Live access needs the Mac back on the same LAN or USB; no public access or
Tailscale route is configured. The earlier Wi-Fi address and pin are not current durable access.

Common signals:

- `RUNTIME_BINARY_MISSING`: the release's artifact does not contain `bin/llama-server` or the archive
  was not staged; check the artifact on the server and `cache/runtime/<sha>/` locally.
- `RUNTIME_BACKEND_MISMATCH`: no CUDA offload evidence; rebuild with `-DGGML_CUDA=ON` and check
  `libggml-cuda` is in the archive (the build script warns when it is not in the closure).
- budget verdict `unknown`: the device has not sent a fresh live report; wait for a heartbeat or check
  connectivity.
- device `offline`: no report for `CONVOY_OFFLINE_AFTER_S` (default 90 s); the agent only makes outbound
  requests, so check DNS/TLS from the Jetson (`curl -v https://<convoy-host>/api/health`).
- credential revoked / quarantine: re-enroll with a rebind token (section 3).

## 8. Robot integration

The robot application talks to the **loopback gateway**, never to `llama-server` directly. Loopback
means the Jetson host's own `127.0.0.1`: the robot process must run on the Jetson (or in a container
that shares the host network namespace). A laptop, a remote container or a container with its own
network namespace has a *different* `localhost` and does not reach the gateway; there is no inbound
port on the device for it to use.

- `POST http://127.0.0.1:<gateway-port>/v1/chat/completions`, `Content-Type: application/json`, body
  `{"messages":[{"role":"user","content":"..."}], "max_tokens": <= 128, "temperature"?, "seed"?}` —
  **text messages only** (max 64), body <= 256 KiB, no `Origin` header (any non-empty `Origin` -> 403).
  This is the whole surface the board has used so far: the operational smoke, the frozen benchmark
  fixtures and the timing matrix all went through this endpoint on port 9070 with single system+user
  turns (section 9.6); no tools, images, streaming or multi-turn history have been exercised.
- `max_tokens` <= the release's `n_predict` (128 by default); the prompt is rendered once with the
  pinned template, tokenized with `add_special=true, parse_special=true`, and must satisfy
  `len(ids) < n_ctx` and `len(ids) + max_tokens <= n_ctx`.
- Unsupported features return **400** `unsupported_feature`: `tools`, `tool_choice`, `response_format`,
  `stream`, image/audio content parts, `chat_template_kwargs`, `reasoning_*`.
- **503** while the gateway is closed (cutover or recovery), in eval mode, when the queue (depth 4) is
  full, or when the slot is not free before the deadline; concurrency is 1. Robots must treat 503 as
  "retry later" and keep their own safe behaviour.
- The gateway holds request ownership until completion or confirmed idle; if it cannot confirm, the
  child is stopped and restarted before the next request.
- The controller / safety stack stays **external** to Convoy: Convoy manages the language runtime only
  and never assumes the robot can wait for it. Size `robot_reserve_mb` for the robot stack's real
  footprint and keep it updated per device.

## 8a. Connectivity the device needs

- **Control plane (outbound HTTPS to the server)**: first enrollment, rebind, runtime artifact
  registration and download, every deploy/recover cutover (grants are issued by the server and bound
  to the live report) and evidence upload. A device that cannot reach the control plane keeps serving
  the active release and journals evidence locally, but cannot enroll, take a grant or cut over.
- **Model staging (outbound HTTPS to Hugging Face and its CDN)**: the pinned model bytes are fetched
  from the public, ungated repository at the pinned commit and hash-verified on the device. Convoy
  never proxies model bytes and never distributes Hugging Face tokens.
- **The runtime artifact cache is not an air-gap installer**: `--storage device` places a verified
  runtime archive in the local cache so a later cutover does not re-download it, but the release
  manifest, grant and model bytes still come over the network as above.

## 9. What has and has not been verified

Latest resumed acceptance is recorded in [VERIFICATION.md §3](VERIFICATION.md): source 530f036,
bounded shutdown during two independently observed network stalls, a fresh fifteen-minute offline
inference population, reconciled usage/traces, and the genuine all-eleven nightly backup plus
isolated restore. That dated section contains the source, device, release, trace and backup IDs.
The earlier observations below remain historical. For interactive text inference from the browser,
see [Chat with a Jetson model](CHAT.md); the separate source4df467b browser check is recorded in VERIFICATION.md §3 (two Cedar replies,
traces tr_84bda42928dcdb9b and tr_36f283c928a85c69, exact 2/106/6 usage reconciliation).

Everything in this section was run by the operator on the project's Jetson Orin Nano Developer Kit
Super on 2026-09-13 against the preserved control plane (section 7a) and is recorded here **as
reported, with its ids**, so that each claim can be checked against the operation, eval or artifact it
names. Scopes are kept apart on purpose: packaging evidence is not inference evidence, an operational
smoke is not customer acceptance, and a single measurement is not a fleet figure.

### 9.1 Board and toolchain identity (measured)

- Host: L4T 36.4.7 (`/etc/nv_tegra_release`), CUDA toolkit 12.6.11, `nvcc` 12.6.68, `/usr/bin/c++`
  11.4.0, CMake 3.22.1, Python 3.10.12, 7619 MiB memory, power mode `MAXN_SUPER` (read, never changed).
- GPU: `nvidia-smi --query-gpu=name,compute_cap` → "Orin (nvgpu), 8.7", measured by the agent's service
  user and carried in the device inventory (report seq 481 was the first service report to carry it,
  after the unit correction in 9.2 and the ingestion allowlist fix bf3f4c3).
- Sensors: real CPU/GPU/SoC temperatures, memory, disk, GPU utilisation and VDD_IN power through
  `tegrastats`; the three `cv*-thermal` zones answer EAGAIN on this board and are reported as unknown,
  never 0 (the per-zone read fix was the first defect the board found).

### 9.2 Agent installation and hardened unit

- `install-agent.sh` from checkpoint 5fa9422 installed the stdlib-only agent as the `convoy-agent`
  service; the device enrolled once as a physical (non-simulated) device with the private CA passed as
  `--ca-file`. The same installation, device id and journal have been in use since.
- Unit hardening correction (ce0cd2e): with the full hardening, `nvidia-smi --query-gpu=name,compute_cap`
  failed under the service (exit 255, `NvRmMemInitNvmap: Operation not permitted`, NVML init failed) and
  the agent reported a null compute capability, while the same command succeeded interactively.
  Reproduced in an isolated transient unit with the exact user/groups/environment/hardening; changing
  only `ProtectClock=no` made it succeed ("Orin (nvgpu), 8.7", exit 0, 95 ms). Cause: `ProtectClock=yes`
  implies `DeviceAllow=char-rtc r`, a closed device policy that denies the Tegra GPU nodes. The unit
  now sets `ProtectClock=no` and keeps the clock protection explicitly (`CapabilityBoundingSet=~CAP_SYS_TIME
  CAP_WAKE_ALARM`, `SystemCallFilter=~@clock`); no device nodes are listed and no other hardening
  changed (`server/tests/test_deploy_unit.py` pins this). Applied on the board by re-running the
  installer; the next inventory report carried the measured SM.

### 9.3 Native runtime artifact (track `l4t3647`)

- Clean native CUDA build (`build-llama-cpp.sh --track l4t3647 --jobs 2`, source from checkpoint
  809dae2): exit 0. After it fully exited, `build-llama-cpp.sh --track l4t3647 --jobs 2 --reuse-build`
  (64c6258) ran on the same tree: exit 0, 0 compile/link steps, cache and source identity unchanged,
  build-tree-hidden `ldd`, `--version` and `--help` checks passed.
- Archive **150,125,737 bytes**, sha256
  `f89110ce8a9b8387830222682c9bd687815c58cd85d09e984410af58014df0b3`, byte-identical between the clean
  build and the reuse run; all 16 regular members re-read and re-hashed independently after transfer.
  `bin/llama-server` sha256 `ab2701f9ff18e903ba3cc1915f90fb7a09010f017faf9bac96c6fea902509aae`.
- Receipt: `track: l4t3647`, `jetpack_established: false`, `verification.clean_build_dir: false`,
  `verification.incremental_build_steps: 0`, full `nvcc_version` 12.6.68.
- Registered against recipe `rcp_bmhvrd7tddpe` as artifact `art_3slhxm2pcwap` (storage server, scope
  this device); the same archive bytes were then verified on the device before every cutover below.

### 9.4 First CUDA deployment (operational smoke, not customer acceptance)

Release `rel_7horo87k6lxs`, plan `plan_v8ntm2rnstyu`, operation `op_xa9x8pz4nk7j`, generation 1,
**SUCCEEDED at 2026-09-13T07:39:27.860352Z**:

- model 1,117,320,736 bytes, archive 150,125,737 bytes and all 16 member hashes verified on the device;
- cutover **7701.7 ms** (stop/start, measured by the agent);
- CUDA0 offloaded **29/29** layers, `intended_backend_ok: true`, `simulated: false`;
- qualification eval `evr_587f655243c4`: 5/5 gates passed, quality 7/8 (one robot-JSON parse failure),
  0 errors, 8/8 coverage; completion latency p50 181.42 ms / p95 899.76 ms; TTFT p50 68.15 ms / p95
  109.75 ms (gateway-measured); 3 sensor samples: max 43.937 °C, peak 12.838 W, minimum MemAvailable
  5036.176 MiB;
- probation 60.3 s, 61 health checks, 0 production requests.

This is the deployment smoke on the frozen 8-case fixture; it is **not** customer acceptance, and the
latencies are from eight short requests, not a throughput claim.

### 9.5 Unplanned reboot (cause unknown)

The board rebooted between 07:39:36 and 07:40:58 UTC, nine seconds after the success above. The
operator did not request it; no kernel OOM or panic evidence was found and pstore was empty; the cause
is **unknown** and no inference (thermal, power supply or otherwise) is made here. What is known:

- the agent recovered by itself on boot: it re-verified the retained release's full sha256, passed
  launch admission and relaunched the same release at generation 1 with a fresh child and a production
  gateway; no operator action on the device;
- the operator reopened the Wi-Fi reverse SSH forward (section 7a) by hand before the device could
  report again. Runtime recovery is automatic; transport recovery on this bench is not.

No second reboot was observed during the later operations below.

### 9.6 Three candidates under the same fixed gates

The plan's gates were fixed before the first candidate and never lowered; the benchmark fixtures
(`bench_cases_v1.jsonl`, 34 strict cases; the 9-cell timing matrix) were frozen at checkpoint 5fa9422
and not tuned after results. All requests went through the loopback gateway on port 9070 as text-only
single-turn chat completions.

| candidate | operation | result |
|---|---|---|
| Qwen2.5-1.5B-Instruct Q4_K_M (baseline) | `op_xa9x8pz4nk7j`, gen 1 | succeeded (9.4): the original eight-case **app** operational smoke scored 7/8 and passed its required 0.8 gate. Standalone strict **gateway diagnostic** (the 34-case fixture with system prompts and whole-JSON scoring; a script fixture, not a Convoy eval set): all 34 timed tasks plus 34 warmups and all 27 timed matrix cases plus 9 warmups completed; all 104 Convoy traces reconciled, every request HTTP 200. Timed task quality **33/34** (`sem-green` answered "yellow" instead of "green" in both its warmup and timed runs); all 10 strict-JSON cases passed. Matrix: nine cells × 3 timed requests at actual prompt bands 92 / 239 / 947 input tokens and caps 16 / 64 / 128: every cap-16 request truncated by design (9/9 failed JSON); all 18 cap-64 and cap-128 requests passed with actual outputs of 23 / 19 / 19 tokens. A cell p95 with N = 3 is the observed maximum, not tail-latency confidence. It alone qualified for further testing |
| Qwen3-0.6B Q8_0 (non-thinking template) | `op_kgw4wnt384qh`, gen 2 | **failed at 07:51:03.802774Z, `EVAL_FAILED`**: app smoke quality 4/8 below the fixed 0.8 gate (a quality refusal; no dependent task, matrix or soak benchmark exists for it); CUDA 29/29, 0 errors, 8/8 coverage, p95 321.51 ms. Eval `evr_af1db5584651` failed `math-1`, `math-2`, `color-1`, `lang-1` (the persisted evidence carries verdicts, not the raw outputs). Recovery relaunched Qwen2.5 in one attempt; the baseline recovery release persisted and generation 2 is latched as failed |
| Qwen3-4B Q4_K_M (non-thinking template) | `op_cj0vp2oakm9g`, gen 3 | **failed at 08:06:43.657136Z, `CUTOVER_MEMORY`**: after the incumbent stopped cleanly (SIGTERM, exit 0, 0.315 s), MemAvailable was 5445.6914 MiB against a required 5511.8 MiB (headroom −66.1 MiB) under the unchanged budget (robot 1536 + margin 512 + runtime 700 MiB, ctx 2048); `candidate_started: false`, so no 4B inference, eval or throughput claim exists. Recovery relaunched Qwen2.5 in one attempt. This is a policy refusal that preserved the reserve and margin, not a measured OOM and not proof the model cannot run under another configuration |

The pinned Qwen3 non-thinking template renders identically to the runtime's own rendering except for a
stripped final newline (verified through `--render-check`). No memory reserve was reduced, no retry
added and no gate changed to obtain a different outcome.

### 9.7 Control plane during the milestone

- The worker's nightly backup was found holding the tick thread for minutes on the live database
  (lease expired, worker health failing while the API stayed live): fixed by the bounded attempt
  (5e419a3) and the pinned source snapshot (a201352, d494170); `docs/BACKUP_RESTORE.md`. Live
  acceptance of 5e419a3: 13 containers healthy, all 11 reporters (ten simulated, one physical)
  advanced over 64.917 s, fence 11 renewed across 4 observations, two deferrals of 12.604 s and
  12.293 s with 60 s then 120 s backoff and own-staging cleanup, no backup published.
- The console's mixed-fleet banner distinguishes the physical device from the ten simulated ones in
  the assets built from 5e419a3. The Runs detail's human-readable outcome summary (8952db1, 9d52a0d)
  and the worker's logging initialization (d748f75, so a successful backup's INFO line is visible)
  were reviewed against the real outcomes above.
- API/worker upgrade to 34c9f10 (image `sha256:848ab180003a2640e79fa6337bd4e7c1105a022a714ef3956597cec0c4e760bc`)
  completed 08:36:33.769–08:36:41.005 UTC: identity, storage and history preserved for all 11 devices
  and the intended 13-container topology, healthy worker fence 12. The physical agent was **not**
  upgraded (it runs 5fa9422); the two sources are deliberate provenance.
- API/worker upgrade to 5b7af1c (immutable ARM64 image
  `sha256:6dda6c25265e18aba47614bc7c23ad8857a9c9359b062f7c1e2432e1fa5bed61`, pinned SQLite 3.51.3 with
  the exact official source id mapped from `/usr/local/lib/libsqlite3.so.0.8.6`) with the explicit offline
  DELETE→WAL transition (FULL): stop requested 20:34:10.481399Z, both services healthy 20:36:38.093838Z
  (147.612439 s upper interval including a private cold-copy verification); old API and worker exited 0;
  the ten simulator reporters and Caddy never stopped; all eleven identities, credentials, bindings,
  generations/releases/latches, schedules, catalogs, schema 3 and history preserved; no rollback used.
  The physical agent is still not upgraded. Root's desktop console (1470×775 only; no mobile claim)
  showed the CUTOVER_MEMORY outcome readable (required 5511.8 / available 5445.7 / headroom −66.1 MiB,
  candidate never started, baseline recovered), the physical-only usage comparison (3,463 historical
  requests), the new 16.7 s backup entry and Settings reporting SQLite 3.51.3 / WAL / FULL.

### 9.8 Fifteen-minute soak on the retained Qwen2.5 (records recovered afterwards)

The complete-batch soak (frozen 34-case bytes, warmup and timed batches through the gateway) was started
after the generation-3 refusal on boot `3f981b3d-37c8-469a-9e07-c3f6ae34d7ae`. At 08:14:04.451650Z the
board stopped reporting (the transport in 7a was lost); the benchmark kept running locally and its
records were recovered and independently reconciled once the board was reachable again:

- 08:07:53.484258–08:23:03.496315 UTC, **910.012088766 monotonic seconds**, **49 complete serial
  batches**, each 34 timed + 34 warmup requests. All **3,332 requests HTTP 200**, zero recorded request
  errors, zero length truncations.
- Timed quality **1,617/1,666**; warmups separately **1,617/1,666**. The only failure is `sem-green`
  (expected `green`, raw answer `yellow`), once per phase in every batch. Total observed tokens across
  both populations: 178,360 input / 23,716 output.
- All 3,332 distinct server traces reconciled to device/release, status, tokens and the available
  timing fields; no empty, error or unattempted trace fetch. All 98 saved records/report hashes match;
  frozen source/fixture hashes, raw requests and scoring were checked independently.
- Timed-only pooled latency, N = 1,666: gateway completion p50/p95 **121.87 / 636.27 ms**, on-board
  client round trip 150.70 / 663.24 ms, internal TTFT 59.06 / 76.18 ms. These pool 34 deterministic
  tasks each repeated 49 times; the elapsed run includes warmups and recording overhead (the last batch
  finishes after the 900 s target); client measurements exclude Mac/Wi-Fi transit; non-streaming
  internal TTFT is not user-visible first-token latency; none of it establishes a 100 ms SLA or customer
  task acceptance. The harness's `tpot_ms = predicted_ms / predicted_n` differs from the runtime's
  per-token timing (denominator n−1); the values are kept as recorded, not relabelled.
- Sensors are sparse: 30 persisted samples strictly inside the soak bounds (first 08:08:08.924554, last
  08:22:15.126095; maximum gap 79.397344 s; 15.440296 s leading and 48.370220 s trailing unsampled),
  clock confidence unknown. Observed ranges: MemAvailable 4914.355–4936.277 MiB, GPU 0–99 %,
  46.125–58.625 °C, 6.803–15.806 W. No continuous coverage, no missed-peak exclusion, no energy or
  thermal-envelope claim.

What this shows: local inference continued through this particular control-plane outage and the
evidence arrived later. It does not qualify every offline-recovery mode.

### 9.9 Managed deadline trial (a separate lab configuration, not a release)

A 0.5 s configuration trial was rejected before generation and is kept separately. A **2.0 s
agent-configuration** trial then returned HTTP 504 after 2.280692 s client time: server span
`StreamDeadline`, **69 non-empty content events**, 54 prompt tokens, `needs_restart=false` (the counter
named `tokens_streamed` is not independently tokenized usage). Native idle confirmation, the same
runtime child and a following raw exact `READY` / HTTP 200 support the idle-confirmed recovery path. The
original 30 s configuration (bytes, ownership, mode), credential/CA fingerprints, the retained release
and device generation 3, CUDA 29/29, the unchanged unit and the 6 GiB cap were restored; final raw exact
`READY` / HTTP 200. All three raw bodies/headers and the captured traces reconciled. This is one
shorter-deadline trial: not a new release, not a test of the default 30 s timeout, and not proof of
client-disconnect cancellation.

### 9.10 Agent stops (not passed), control-plane upgrade, and backup acceptance (ten-simulator control plane accepted; all-eleven pending)

- **Stops: clean shutdown is not claimed.** Exactly one forced stop: agent 9594 was SIGKILLed at the
  45 s limit (recorded interval 45.245969 s). The second stop, agent 9993, exited 0 after 44.354969 s;
  TLS returned near that exit without causal proof. A 21.8 s unknown-coverage interval is preserved. The
  offline SIGTERM fix (5b4b81c, corrected in 6cb9307; `SHUTDOWN_BUDGET_S = 35`) is verified in software
  only (`agent/tests/test_shutdown_bounded.py`, real-process measurements on the author's host) and
  must be reported on the board only from fresh evidence. **Pending: the physical offline-stop retest.**
- **Upgrade:** the 34c9f10 API/worker upgrade in 9.7 preserved every device identity; the physical
  agent was not upgraded.
- **Automatic backup acceptance: did not pass.** On the 34c control plane two bounded attempts deferred
  after 3.004 s and 3.280 s source-lock holds, about 32.33 % and 34.35 % of the pages copied; staging was
  cleaned, there was no busy/restart churn, and zero new backup ids were completed. The 15 existing
  backups are not new successes. That result stands as recorded.
- **Automatic backup acceptance on the 5b7af1c WAL control plane: accepted for the ten-simulator
  control plane** (no manual trigger, no reporter pause): attempt `bka_8mg0ov`, backup
  `bak_198qi6js5efd` registered 20:36:49.103441Z, 2,275,872,768 bytes, sha256 `b0305612…9eee8c2a9`,
  16,727.7 ms; WAL snapshot held 14.358 s (20:36:32.363800Z–20:36:46.721643Z), 555,633 pages in 2,171
  steps, 0 restarts, 0 busy, remaining 0; post-copy PASSIVE checkpoint busy 0, 1,999/1,999 frames, WAL
  8,355,392 bytes after and stable at 8,359,512 bytes; 138 report/spool handlers from all ten simulators
  started and completed inside the snapshot window (median 3.738 ms, max 154.663 ms of handler time,
  not client latency); all ten advanced across 95.10 s with no new unknown or loss record; 20 lease
  samples over 101.685 s (starting 10.054 s after the release: not an in-copy renewal test) kept the
  same owner and fence. The registered file was independently verified (exact markers, image and Python
  inventory, size/hash/integrity/foreign keys/schema/catalog/all 11 identities) and restored into an
  isolated fixture where the WAL-configured API served HTTP 200 health with no network before exiting 0,
  all 11 ids retained, quarantine and dispatch pause entered, old credentials invalid. **This board was
  stationary and unreachable throughout (report seq 2538, all three lanes)**, so all-eleven live
  continuity, a >60 s copy, and an in-copy lease renewal are not claimed; nothing was rebound, no
  quarantine lifted, no artifact recovered.

### 9.11 Where it stands (pending items stay pending)

- Board access: lost from the operator's Mac at the time of writing (7a; last report 17:12:33.277626
  UTC, seq 2538). At 17:06:28 UTC one fresh request had returned HTTP 200 with raw exact `READY` (trace
  `tr_b344eeacb76b22bd`, `rel_7horo87k6lxs`, `simulated: false`, 35 input / 2 output tokens; gateway
  330.43 ms, on-board client 355.019 ms: one first-request observation, not a benchmark), and at 17:07:49
  UTC the device was online on boot `c3fee69e…`, CUDA0 29/29, no active operation, generation 3 with its
  failed-generation latch 3 preserved, all three spool lanes at zero bytes with `next_seq = committed_seq
  + 1`. Point-in-time recovery facts; not tunnel stability, not a re-run of the measured populations.
- Pending: the physical bounded-stop and spool-replay retest (9.10) with the new agent installed on the
  board (it still runs 5fa9422); all-eleven live backup continuity with this board reporting; the final
  CI result on the branch head. Deployed: the 5b7af1c control plane in WAL (9.7). Done in software:
  root's real-native CPU integration tests on the exact 5b7af1c tree (2/2 passed, Linux ARM64 / Python
  3.10, pinned llama.cpp with the Qwen fixture: active-generation cancellation HTTP 504 at a calibrated
  16.52 s deadline after 46 content events, same child kept, next request 200, clean stop; host CPU
  component evidence, not Jetson CUDA and not a physical stop acceptance; `docs/VERIFICATION.md` §1).
  `main` stays held until root reviews this documentation and the head's CI passes.

### 9.12 Verified in software only

Protocol, journal/spool, extraction rules, gateway contracts, argv generation, receipt/archive format,
`register-runtime.sh` against a local server, bounded agent shutdown (including root's real-native CPU
cancellation and stop probes on the 5b7af1c tree), the pinned SQLite 3.51.3 WAL runtime with the
single-flight backup task (accepted live only with the ten simulators, 9.10), and every regression suite
in `docs/VERIFICATION.md`.
Not yet measured on hardware: sustained thermal and power behaviour beyond the sparse soak samples,
`MemoryMax` sizing, multi-device rollouts to physical devices, the default 30 s deadline under load,
and any customer workload.
