# Verification evidence

What has actually been run, where, and what it proves. Three scopes are kept apart on purpose and
never summed: (1) automated suites in this repository (simulated devices and runtime), (2) independent
software runs reported by the reviewer, (3) the physical Jetson milestone reported by the operator
from the project's own board (`docs/JETSON_GUIDE.md` section 9 is the primary record, with every
operation, eval and artifact id). Anything not listed under (3) is not hardware evidence.

## 1. Automated suites (this repository)

Counts are the author's runs at the named checkpoint; later checkpoints were verified by their own
focused files (named per checkpoint in `docs/STATUS.md`) rather than by rerunning every suite.

| Suite | Command | Last full run | What it proves |
|---|---|---|---|
| Server | `uv run --package convoy-server pytest server/tests -q` | 172 passed, 1 opt-in skip at b28fe20 (272.1 s, Linux x86-64, Python 3.11, Chromium available so the browser journeys ran; retained log); only test files changed after it, each rerun by its own file (backup task 12, backup budget 13, status copy 1, browser forms+redesign 2). Earlier: 164 passed, 1 skip at 45e7068; the four browser journeys at 33fabaa (4 passed, 113.9 s) | identity/roles/CSRF/throttles, enrollment replay + rebind, quarantine + console recovery, operations (reservation CAS, bound grants, per-type success evidence), catalog immutability + GGUF lifecycle (header inspection provenance, prepare/persist split), platform tuple gate (dispatch, grant, real enrollment + heartbeat ingestion of the measured SM), review regressions, rollouts, fenced scheduler, evidence lanes, backup → restore → quarantine → rebind, bounded/pinned worker backup as a single-flight fenced task, SQLite runtime contract (attestation, journal mode as a file property, offline `db-mode`), WAL-aware restore/migration copies, handler completion records, systemd unit directives, service startup + CLI, e2e simulated fleet, browser journeys (including the Runs inspector geometry at 1440x768/400/320) |
| Agent | `uv run --package convoy-agent pytest agent/tests -q` (and under Python 3.10) | 149 passed, 2 optional-native skips at 5b7af1c on Python 3.11 (541.9 s) and on Python 3.10 via `uv --python 3.10` (542.1 s), Linux x86-64; root's Linux ARM64 Python 3.10 non-native run of 5b7af1c: 149 passed, 0 skips, 560.31 s; root's Linux run of 354ca78: 148 passed, 1 failed (the optional native cancellation calibration, since rewritten to calibrate on warmed comparable requests; historical). Root's real-native run of the exact 5b7af1c tree: both optional integration tests passed (2/2, 150.82 s, 0 skips) on Linux ARM64 / Python 3.10 with pinned llama.cpp 5266f24 and the Qwen GGUF fixture: calibration on two warmed 160-token requests (57.69387 s / TTFT 4.39282 s and 52.89617 s / TTFT 3.98523 s, within the 30 % comparability), chosen deadline 16.5186575 s, actual active-generation cancellation HTTP 504 after 17.281573883 s with 46 positive content events, needs_restart false, native idle true, same child (no replacement), next request HTTP 200, final owned-child SIGTERM stop 0.17 s exit 0 with child/key/record/listener cleanup asserted. Host CPU component evidence only: not Jetson CUDA, not a physical stop acceptance | journal/spool, verified downloads, GGUF strict dimensions + KV sizing, extraction, gateway contract, runtime supervisor, tegrastats/thermal per-zone reads, platform tuple preflight/fresh read/retained starts, launch admission before every start (cutover, recover, boot, controlled restart), benchmark script (fixtures, matrix, interruption phase), scorers/evaluator, bounded shutdown (real-process SIGTERM against stalled/partial-body/held-download servers, one shared runtime deadline, ownership tied to a verified child stop, no upload on stop) |
| Web | `cd web && pnpm typecheck && pnpm lint && pnpm test && pnpm build` | 207 vitest tests at the documentation checkpoint after 33fabaa, typecheck/lint/build clean | API client, status mapping, components, page contracts, GGUF metadata/provenance rendering, mixed-fleet banner, operation outcome summary (real outcome shapes, malformed gates, unknown vs null), project hardware-validation copy (partial scope, never a blanket denial), build-required copy that qualifies no build |
| Focused backup / runtime files | `server/tests/test_backup_budget.py`, `test_backup_task.py`, `test_sqlite_runtime.py`, `test_restore_wal.py`, `test_handler_observability.py` | 13 + 12 + 9 + 6 + 2 passed at the review-correction checkpoint after 354ca78 (inside the full run above); each new test shown failing on the pre-fix code | bounded attempt, pinned source snapshot under an active writer with real API writes, deferral cleanup, lock released before hash/publish, stop cancellation, worker backoff; single-flight task under a frozen fence token (no publish after lease loss, cancel on leader loss/stop, bounded join); WAL snapshot budget and PASSIVE checkpoint evidence; attestation, mode mismatch refused, `db-mode` offline transition; WAL-aware restore keeps a crash-left WAL's committed frames in the previous copy; restore and the migration copy refuse, leaving file and sidecars untouched, while a foreign read-only connection holds an older snapshot and a writer has committed since (then succeed after the reader lets go); a stop or the total deadline arriving while the publication waits for the write lock still wins (barrier: lock held until the cancellation exists); mode-mismatch and unqualified-library refusals precede any schema work (sqlite_schema, user_version, rows, bytes and sidecars compared before/after on an additive upgrade candidate; a fresh path stays absent); writer waits attributed to the reported source-lock window rather than to the disk; a real report commits while a WAL snapshot is pinned |
| Focused agent shutdown file | `agent/tests/test_shutdown_bounded.py` | 19 passed at 6cb9307 (in the full runs above) | real child processes: SIGTERM to exit in 0.515 s (TLS handshake stall), 0.716 s (post-handshake stall), 0.315 s (idle between polls), 1.017 s (partial JSON body) on this host; before the fix the same cases took ~45 s and a SIGKILL. Host timings, not board timings |
| CI (GitHub Actions) | `.github/workflows` | run 34708943349 on `main` 337ed4d: all six jobs green | Python 3.11/3.12 server+agent, Python 3.10 agent, web, Docker, browser journey. On the working branch (354ca78): pinned Docker/WAL/restore candidate (amd64), Python 3.12, web, compose, shellcheck and legacy Docker green; Python 3.11 failed the writer-stall assertion corrected in b28fe20. Root's native ARM64 build of 354ca78 failed the image's own attestation (distribution 3.46.1 mapped): loader precedence corrected in f1c1306, whose CI run passed 7 of 8 jobs including the new arm64 candidate; its Python 3.11 failure (a stopwatch bound in test_backup_task) and root's pinned-harness failure (test_backup_budget partial-copy assumption) were corrected in d36fcda and 421afbc by synchronisation. Root's native ARM64 builds of f1c1306 and 5b7af1c passed the attestation, and the arm64 candidate job of the 1bb5869 run passed. These are historical runs of named checkpoints; no run of the final head is recorded here. Publication status is the branch's Actions page (and `main`'s once published), never a claim in this file |
| Real llama-server (optional, host CPU) | `LLAMA_SERVER_BIN=… TEST_GGUF_PATH=… uv run --package convoy-agent pytest agent/tests/integration -q -s` | skipped without the env | supervisor + gateway against an unmodified pinned binary; CPU component evidence only |

## 2. Independent software runs reported by the reviewer (not reproducible here)

See `docs/STATUS.md` sections B–E and G: live Compose upgrade with every device identity preserved,
restore rehearsal, wall-clock scheduling, ten-device short and thirty-minute sustained acceptance,
native CPU `llama-server` suite on Linux ARM64 with the actual pinned Qwen GGUF, console redesign
acceptance. None of it is hardware evidence.

## 3. Physical Jetson evidence (reported by the operator, 2026-09-13; `docs/JETSON_GUIDE.md` §9)

### Latest resumed physical acceptance — 2026-09-14 UTC

These are new measured populations, distinct from the historical September 13 results below.
Source `530f036baa448321e95c985000ef74ac575d366b` was installed on both the physical agent and the
preserved control plane. All 22 installed agent Python modules matched the frozen source; device
`dev_d4ckwnt7s634`, credentials, private CA, journal, generation 3, failed-generation latch 3,
retained active/recovery release `rel_7horo87k6lxs`, native CUDA runtime and systemd policy were
preserved. The runtime still offloaded 29/29 layers. Boot: `c3fee69e-7ef0-4ebf-ad1c-2b0bdfa2a6a4`.

| Verification | Measured result and scope |
|---|---|
| Stop during stalled TLS handshake | Open ClientHello peer independently observed; clean service stop 0.582 s, summary 0.474 s, owned child SIGTERM/exit 0, one network abort; telemetry 7462, span 7463, usage 1205 replayed with no covering loss. Following exact READY HTTP 200, trace `tr_3fd4d07653ccf781`. |
| Stop during stalled HTTPS response | Valid TLS request held at the peer; clean stop 0.588 s, summary 0.492 s, owned child SIGTERM/exit 0, final usage checkpoint. Telemetry 7487, span 7488, usage 1212 replayed; independent ordinary daily usage delta exactly 1 request / 35 input / 2 output tokens. Following READY HTTP 200, trace `tr_eef50f87b473cd54`. |
| Offline inference, 01:19:53.036487–01:35:08.575997 UTC | 915.539543936 monotonic seconds, 49 complete batches, 1,666 timed plus 1,666 warmup requests, all 3,332 HTTP 200. Each population separately scored 1,617/1,666; all 49 failures were the same semantic green/yellow task. Total 178,360 input / 23,716 output tokens. This is a diagnostic fixture, not customer acceptance. |
| Timed latency, N=1,666 | Gateway completion p50/p95 122.07/634.9875 ms; onboard client 151.535/661.585 ms; internal TTFT 59.105/77.59 ms. Non-streaming TTFT is not browser first-token latency. |
| Offline sensor samples | 49 samples inside workload bounds, max gap 19.792 s, clock confidence unknown; 45.593–58.843 °C, 6.159–15.767 W, MemAvailable 4850.320–4868.863 MiB. Sampled observations, not continuous peak guarantees. |
| Recovery and accounting | Control-plane cursors remained stationary during outage. After restoring the exact CA-verified reverse connection, all 3,332 inference spans and 54 queued telemetry samples had exactly one server projection, 17 usage records reconciled, and journal/checkpoint/server deltas matched 3,332/178,360/23,716. No covering loss; the same agent/runtime PIDs remained through the soak. Post-recovery READY trace `tr_ed9f0ae66cb70c0c` is outside that population. |
| Genuine nightly backup with all eleven reporters | Automatic attempt `bka_ayfbyy`, backup `bak_7uxtmjkr4gew`, acquired 02:00:00.203321Z, released 02:00:22.249386Z, success 02:00:24.117145Z. File 2,617,532,416 bytes, SHA256 `cc436dbba8012c6ff885722fd8a285db8a751d9ed999b31565a76c7baf3e42a7`. Copy 22.045 s, 2,497 steps, zero restarts/busy steps, remaining zero. All eleven live/report, telemetry and usage frontiers advanced; all thirteen container identities and protected installation state were preserved. |
| Backup checkpoint limitation | Best-effort post-copy PASSIVE reported busy=1 and frame counts −1/−1. Checkpoint progress is unavailable and is not claimed. The completed backup's full hash, integrity, foreign keys and protected identity checks passed independently. |
| Isolated restore | Exact verified backup restored in an isolated no-network fixture. SQLite 3.51.3, WAL, FULL; all eleven identities retained, quarantine/dispatch pause entered, old sessions/tokens/device/enrollment credentials and restored passwords invalidated; isolated HTTP health 200 followed by clean exit 0. No live rebind or artifact-store recovery was exercised. |

The two bounded network stalls and this offline soak are accepted in their measured scope. Clean
shutdown does not erase earlier forced-stop history or unknown coverage intervals. Multi-device
physical rollouts, customer workloads, public hosting and broader thermal/power qualification remain
unverified. Chat is a subsequent software change; its separate physical conversation verification follows.

### Browser Chat on the physical Nano — source 4df467b

After the source530 acceptance above, the API/worker and physical agent were upgraded to
`4df467ba100d0e668f507e023a24a0b05e5f6aa1`. All 72 API-image server/agent Python files and all
23 installed device agent modules match that frozen source. The image is
`sha256:331a02bfa4e132dfe44a4ff262d1e6600641062e018bccae98f9c2ea7189b6fd`.
The retained model, credential/CA/configuration bytes, unit, interpreter, boot, generation 3 and
failed-generation latch 3 remained intact. The controlled agent stop-to-resume command interval
was 7.291 s; it is not a measured browser outage. The control-plane preservation comparison passed
after restoring two bench-specific Compose values that initially defaulted during recreation
(public URL and local HTTP cookie setting); those values are now explicit in the private override.

The operator used Convoy's actual browser Chat screen on September 14 UTC:

| Prompt | Real reply | Gateway latency | Tokens in/out | Trace |
|---|---|---|---|---|
| My rover is named Cedar. Reply only with its name. | Cedar | 253.94 ms | 41 / 3 | `tr_84bda42928dcdb9b` |
| What is my rover named? Reply only with its name. | Cedar | 234.02 ms | 65 / 3 | `tr_36f283c928a85c69` |

Both requests completed with `finish_reason=stop` through production release `rel_7horo87k6lxs`.
The second request demonstrates retained conversational context for this two-turn smoke.
Both stored trace projections match release, device, token counts and gateway timing; the device
checkpoint and ordinary server usage delta independently equal **2 requests / 106 input / 6 output**.
The same agent PID 19859 and owned runtime PID 19867 remained alive across the conversation, with
CUDA0 29/29 layers and the native binary hash recorded above. All eleven reporters progressed and
protected identities, operations, catalogs and configuration remained unchanged through settlement.

These are two real conversational smoke requests, not a latency benchmark or customer workload.
Gateway timing excludes relay polling and browser network time. Chat source4df467b does not inherit
source530's earlier offline benchmark as a new measurement. The corresponding browser screenshots
show the real replies and trace links; the simulator banner labels the separate simulated cohort.

### Historical September 13 evidence

The following source versions, connection state and pending labels describe that earlier checkpoint;
they are superseded only by the explicitly measured September 14 rows above.

One enrolled board, `nano-lab-01` (`dev_d4ckwnt7s634`): Jetson Orin Nano Developer Kit Super 8 GB,
L4T 36.4.7, CUDA runtime 12.6.11, SM 8.7. No JetPack marketing version is inferred and no NX
equivalence is claimed. The physical agent runs source `5fa94228c7626f1d521c06c627a751a6bdbdbdbb`; the
control plane was later upgraded to `34c9f102ecde066b0bb2c58081ea745691372459` (image
`sha256:848ab180003a2640e79fa6337bd4e7c1105a022a714ef3956597cec0c4e760bc`). The two sources are
deliberate provenance, not a claim that every component is at the branch head. Native `bin/llama-server`
sha256 `ab2701f9ff18e903ba3cc1915f90fb7a09010f017faf9bac96c6fea902509aae`, registered runtime artifact
`art_3slhxm2pcwap`; retained Qwen2.5 release `rel_7horo87k6lxs`, CUDA offload 29/29. Model, template and
runtime pins are those of the guide; no checkpoint hash was revised.

| Item | Evidence | Scope |
|---|---|---|
| Board identity | L4T 36.4.7, CUDA 12.6.11 (`nvcc` 12.6.68), "Orin (nvgpu), 8.7" measured by the service user, Python 3.10.12, 7619 MiB, `MAXN_SUPER` (read only) | measured; the `jp623` track (L4T 36.5.2) remains source-verified only |
| Agent install + hardened unit | installer from 5fa9422; `ProtectClock` correction ce0cd2e reproduced and confirmed on the board (exit 0, 95 ms); measured SM ingested from report seq 481 | installed once, enrolled once, never re-enrolled; the physical agent was **not** upgraded with the 34c control plane |
| Native runtime artifact | archive 150,125,737 B `f89110ce…14df0b3`, `bin/llama-server` `ab2701f9…6fea902509aae`, receipt track `l4t3647`, reuse run 0 steps; recipe `rcp_bmhvrd7tddpe`, artifact `art_3slhxm2pcwap` | packaging identity, verified on-device before each cutover; qualifies its own recipe and build only |
| First CUDA deployment | `op_xa9x8pz4nk7j` gen 1 SUCCEEDED 07:39:27.860352Z: cutover 7701.7 ms, CUDA 29/29, intended backend ok, eval `evr_587f655243c4` 5/5 gates, 7/8 quality, probation 60.3 s / 61 checks / 0 requests | operational smoke, not customer acceptance; one measurement |
| Unplanned reboot | 07:39:36–07:40:58, cause unknown; agent relaunched the retained release by itself; the bench's reverse SSH transport was reopened by hand | runtime recovery automatic, transport manual |
| Three candidates, same frozen gates | Qwen2.5-1.5B Q4_K_M: original eight-case **app** operational smoke 7/8 (passed its required 0.8 gate); standalone strict **gateway diagnostic** 33/34 (34-case fixture with system prompts and whole-JSON scoring, not a Convoy eval set); matrix 27 timed (nine cells × 3) + 9 warmups at actual prompt bands 92/239/947 tokens and caps 16/64/128 (cap 16 truncates by design; a cell p95 with N = 3 is the observed maximum, not tail-latency confidence). It alone qualified for further testing. Qwen3-0.6B Q8_0 `op_kgw4wnt384qh` gen 2 `EVAL_FAILED` (app smoke 4/8, quality refusal, baseline recovered; no dependent benchmark). Qwen3-4B Q4_K_M `op_cj0vp2oakm9g` gen 3 `CUTOVER_MEMORY` (available 5445.6914 < required 5511.8 MiB after the incumbent stopped; candidate never started; baseline recovered) | no gate lowered, no reserve reduced; the 4B result is a policy refusal, not a measured OOM and not proof it cannot run under another configuration |
| Fifteen-minute soak on the retained Qwen2.5 (records recovered from the board) | boot `3f981b3d-37c8-469a-9e07-c3f6ae34d7ae`, 08:07:53.484258–08:23:03.496315 UTC, **910.012088766 monotonic s**, 49 complete serial batches of 34 timed + 34 warmup requests; **3,332 HTTP 200**, 0 request errors, 0 length truncations; timed quality **1,617/1,666**, warmups separately 1,617/1,666 (the only failure is `sem-green`: expected `green`, answered `yellow`, once per phase in every batch); 178,360 input / 23,716 output tokens across both populations; all 3,332 distinct server traces reconciled to device/release, status, tokens and available timing fields; all 98 saved records/report hashes match, frozen source/fixture hashes, raw requests and scoring independently checked | local inference continued through this particular control-plane outage and the evidence arrived later; not a qualification of every offline-recovery mode |
| Soak latency (timed only, pooled, N = 1,666) | gateway completion p50/p95 **121.87 / 636.27 ms**, on-board client round trip 150.70 / 663.24 ms, internal TTFT 59.06 / 76.18 ms | 34 deterministic tasks × 49 repeats pooled; elapsed time includes warmups and recording (the last batch ends after the 900 s target); client numbers exclude Mac/Wi-Fi transit; non-streaming internal TTFT is not user-visible first-token latency; no 100 ms SLA and no customer task acceptance follows. Harness `tpot_ms = predicted_ms / predicted_n` differs from the runtime's per-token timing (denominator n−1) |
| Sensors during the soak | 30 persisted samples strictly inside the soak bounds (first 08:08:08.924554, last 08:22:15.126095; maximum gap 79.397344 s; 15.440296 s leading and 48.370220 s trailing unsampled); MemAvailable 4914.355–4936.277 MiB, GPU 0–99 %, 46.125–58.625 °C, 6.803–15.806 W; clock confidence unknown | sparse; no continuous coverage, no missed-peak exclusion, no energy or thermal-envelope claim |
| Managed deadline trial (separate lab configuration) | a 0.5 s configuration trial was rejected before generation (kept separately). A **2.0 s agent-configuration** trial returned HTTP 504 after 2.280692 s client time; server span `StreamDeadline`, 69 non-empty content events, 54 prompt tokens, `needs_restart=false`; native idle confirmation, the same runtime child and a following raw exact `READY` / HTTP 200 support the idle-confirmed recovery path; the original 30 s configuration (bytes/ownership/mode), credential/CA fingerprints, retained release, device generation 3, CUDA 29/29, unchanged unit and 6 GiB cap were restored, final raw exact `READY` / HTTP 200; all three raw bodies/headers and captured traces reconciled | one shorter-deadline trial; not a new release, not a default-30 s timeout test, not client-disconnect cancellation proof; the `tokens_streamed` counter is not independently tokenized usage |
| Agent stops on the board | exactly **one forced stop**: agent 9594, SIGKILL at the 45 s limit (recorded interval 45.245969 s); the second stop, agent 9993, exited 0 after 44.354969 s; TLS returned near the second exit without causal proof; a 21.8 s unknown-coverage interval is preserved | **clean shutdown is not claimed**; the offline SIGTERM fix (5b4b81c, 6cb9307) is software-verified only and awaits fresh physical evidence |
| Control plane upgrade to 34c9f10 | 08:36:33.769–08:36:41.005 UTC; identity/storage/history preserved for 11 devices (ten simulated, one physical) and the intended 13-container topology; healthy worker fence 12 | physical agent not upgraded |
| Automatic backup acceptance at 34c9f10 (DELETE) | **did not pass**: two bounded attempts deferred after 3.004 s and 3.280 s source-lock holds, about 32.33 % and 34.35 % copied; staging cleaned, no busy/restart churn, zero newly completed backup ids; the 15 existing backups are not new successes | historical; superseded by the 5b7af1c acceptance below, never rewritten |
| Earlier 5e419a3 live acceptance | 13 containers healthy, 11 reporters advancing, fence 11, two clean deferrals of 12.604 s and 12.293 s, no backup published | bounded backup proven to defer; historical |
| Control plane upgrade to 5b7af1c (WAL) | 2026-09-13: immutable ARM64 image `sha256:6dda6c25265e18aba47614bc7c23ad8857a9c9359b062f7c1e2432e1fa5bed61`, pinned SQLite 3.51.3 with the exact official source id, mapped library `/usr/local/lib/libsqlite3.so.0.8.6`; explicit offline DELETE→WAL, synchronous FULL (2); old API and worker exited 0; stop requested 20:34:10.481399Z, both healthy 20:36:38.093838Z (147.612439 s upper stop-request-to-health interval, which includes a private cold-copy verification); ten simulator reporters and Caddy never stopped or paused; eleven ids, credential HMACs, bindings, generation/release/latch, schedules, catalogs, schema 3, history/unknown/loss records and all other configuration preserved; only the API/worker image and `CONVOY_SQLITE_WAL=1` changed (the image adds the reviewed `UV_PYTHON` / `UV_PYTHON_DOWNLOADS=never`); strict normalized configuration proof and after→settled preservation both pass; no rollback used | control plane only; the physical agent stays at 5fa9422 |
| Automatic backup acceptance at 5b7af1c (ten-simulator control plane) | **accepted**, automatic (no manual trigger, no reporter pause): attempt `bka_8mg0ov`, backup `bak_198qi6js5efd` registered 20:36:49.103441Z, 2,275,872,768 bytes, sha256 `b03056127b667fa634d456f4dbdbc429dc83f3877ff73b9b2a8b21b9eee8c2a9`, registered duration 16,727.7 ms; WAL snapshot acquired 20:36:32.363800Z, released 20:36:46.721643Z, held 14.358 s, 555,633 pages / 2,171 steps, 0 restarts, 0 busy, remaining 0; post-copy PASSIVE checkpoint 20:36:46.742159924Z busy 0, log frames 1,999, checkpointed 1,999, WAL 8,355,392 bytes after, stable at 8,359,512 bytes afterwards; 138 report/spool handlers from all ten simulators started AND completed inside a conservative snapshot window (wall-vs-monotonic discrepancy 0.000843 s, 50 ms margin), median 3.738 ms / max 154.663 ms handler duration (validation, transaction and response construction: not client latency, not an isolated COMMIT); all ten advanced live/report, usage and telemetry across 95.10 s with no new unknown or loss record; 20 read-only lease samples over 101.685 s kept the same owner/fence with 20 advancing expiries, sampling starting 10.054 s AFTER the copy release | ten simulators plus the control plane; the physical device (report seq 2538, all three lanes) was stationary throughout, so **all-eleven live continuity is not claimed**; the observer counted 0 failed instrumented handlers, which is not proof of zero network/auth failures; not a >60 s copy and not an in-copy lease-renewal test; independent Astra audit found no remaining failure in this scoped acceptance |
| Isolated restore of the registered backup | exact-id verifier PASS: fresh timestamp plus the exact acquired/released/success/worker-success/PASSIVE-checkpoint markers; API/worker image and the full server Python inventory match the archived 5b7af1c; file size, hash, `integrity_check`, foreign keys, schema, catalog and all 11 protected identities pass; the verified bytes copied into an isolated fixture: `restore` passes in WAL, `doctor` reports 3.51.3 / WAL / FULL 2, the isolated API serves HTTP 200 health at 20:42:23.873062Z with no network and no published ports, then exits 0; read-only checks confirm all 11 ids retained, quarantine and dispatch pause entered, old sessions/API/device/enrollment credentials invalidated, restored users disabled with old password hashes invalid | isolated fixture only: no live rebind, no quarantine lift, no artifact recovery, no physical test |

**Access after the milestone (dated observation, not a re-run of the measured populations).** The failed
Wi-Fi BSSID pin was rolled back to its original empty value; other-AP associations and DHCP
interruptions continued despite the saved pin and the driver/AP cause is unproven. After a later boot
(`c3fee69e-7ef0-4ebf-ad1c-2b0bdfa2a6a4`) the baseline release was retained automatically. USB first
restored the strict-host-key, CA-verified reverse HTTPS tunnel; at 17:10:10 UTC the owned reverse
forward moved to a Wi-Fi IPv6 SSH master (TLS health and report seq 2529 verified; Wi-Fi verified after
USB disconnection at 17:11:25 UTC; Ethernet NO-CARRIER). At 17:06:28 UTC one fresh request returned
HTTP 200 with raw exact `READY` (trace `tr_b344eeacb76b22bd`, `rel_7horo87k6lxs`, `simulated: false`,
35 input / 2 output tokens; gateway completion 330.43 ms, on-board client 355.019 ms: **one first-request
observation, not a benchmark**; the API trace matches identity, tokens and timings). At 17:07:49 UTC the
device was online on the new boot, CUDA0 29/29, no active operation, generation 3 with its failed-generation
latch 3 preserved, all three spool lanes at zero bytes with `next_seq = committed_seq + 1`; all 11 devices
showed online in Fleet (the ten simulated ones are a separate population). The Mac then left the home LAN
(different IPv4 and IPv6 prefix), USB is disconnected, and every known Jetson IPv4/mDNS/IPv6 path times
out; **last physical report 17:12:33.277626 UTC, seq 2538**. This is loss of the verified network path,
not evidence of board failure and not attributable to Jetson Wi-Fi instability; the earlier handoff is
historical, not a current connection. Live access needs the Mac back on the same LAN or USB; no public
access or Tailscale route is configured. Transport: Mac browser `localhost:18083`, Jetson-owned gateway
`localhost:9070`, reverse HTTPS `18445`; manual local transport, no cloud hosting, no robot actuation in
any benchmark. Root will supply fresher final facts before publication.

**Incomplete and pending (explicitly not claimed):** bounded shutdown and spool replay on the board (one
forced stop recorded; the fix at 5b4b81c/6cb9307/5b7af1c is software-verified and root-probed in software
only, NOT installed on the physical agent, which stays at 5fa9422); all-eleven live backup continuity
(the accepted backup ran with the physical device stationary and unreachable); sustained thermal/power
beyond the sparse samples; the final CI result on the branch head; publication to `main` (held until root
reviews this documentation diff and the head's CI passes). The real-native CPU integration tests are no
longer pending (2/2 passed at root on the exact 5b7af1c tree, §1), and are host CPU component evidence.

## 4. Not verified

- Sustained thermal/power behaviour beyond the sparse soak samples, `MemoryMax` sizing, multi-device
  rollouts to physical devices, any customer workload, the `jp623` track on hardware, the default 30 s
  deadline under load, client-disconnect cancellation, offline-recovery modes other than the one outage
  the soak happened to span.
- Any source newer than the explicitly recorded physical acceptance above requires its own deployment
  and verification; the September 14 acceptance is tied to source 530f036.
- Docker Compose execution in the build sandbox (no daemon); the operator runs Compose on the Mac.
- ACME issuance on a public DNS name (the bench uses the private CA).
