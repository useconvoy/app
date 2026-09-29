# Owned local process prerequisite

`convoy_agent.owned_process.OwnedProcess` is an opt-in helper for a known,
foreground, single-process launcher. The native `RuntimeSupervisor` can opt in by
receiving a caller-held `process_owner`; `examples/manipulation/local_gateway.py`
uses it. The coordinator and bundle activation do not yet use it. The base agent remains dependency-free and
supports Python 3.10. Install `convoy-agent[local-host]` to use this helper.

```python
with OwnedProcess(private_role_directory) as owner:
    owner.recover()  # stop a verified previous child; never adopt it as healthy
    child = owner.start(lambda marker: [absolute_python, "-m", known_module,
                                        "--ownership-marker", marker])
    # Check the component's own readiness and identity separately.
    owner.stop()
```

One private directory represents one process slot. Its exclusive file lock stays
held for the context's lifetime; calls on the same object are also serialized.
Closing the context releases the lock, without claiming the child has stopped.
The owner must explicitly stop it during orderly shutdown. Known launchers must
not daemonize, fork background children, change user, or remove their marker from
argv. Only the direct foreground process is managed; no process-group signal or
name-based kill is used. This is not a sandbox against other processes of the same
user.

The helper creates a private unique marker file, then fsyncs the intent record
before calling Popen. The marker must be an entire argument; the launcher may use
that private file for its per-launch API key. After launch it records PID, kernel
creation identity, executable, and user. On recovery, a missing PID is resolved
only by one exact marker match with the expected executable and all three user IDs.
Multiple matches, no provable match, denied inspection, malformed records, or PID
reuse block signals and replacement. An intent without a discoverable child stays
blocked even when the launch may never have happened. Operators must investigate
that retained evidence; deleting the directory is not an automatic recovery path.
A still-live owner is different: its exact Popen handle can prove an immediate
child exit with waitpid, retain that exit result, and permit a later launch.

Termination rechecks identity before TERM and, if necessary, KILL. Each wait is
bounded (3 seconds by default, at most 30 seconds). It records stopped only after
PID absence or an exact-incarnation zombie is observed. The original unresolved
record is retained on failure, together with a bounded last-failure record. The
last completed record is retained when a later launch replaces it.

## Deliberately pinned identity backend

The optional dependency is exactly psutil 7.2.2; the helper rejects another version
or a platform other than Darwin/Linux. The installed Darwin implementation was
inspected and exercised with real processes. Two private interfaces are deliberate:

- `Process._ident[1]` is the unadjusted kernel creation identity used by psutil's own
  PID-reuse check. Public `create_time()` may be adjusted after clock changes.
- `Process._proc.exe()` is the native executable lookup. Public `Process.exe()` may
  guess from argv[0] when native lookup is denied or empty, which is insufficient
  for ownership proof.

psutil's signal path rechecks creation identity immediately before `os.kill`.
Neither Darwin nor Linux uses an atomic process-handle signal in this implementation:
a narrow check-to-signal PID reuse race remains (no Linux pidfd is used). This helper does not claim an OS security
boundary. Changing psutil requires reviewing those internals and rerunning the
native recovery checks. Native `NoSuchProcess` from executable/argv lookup alone
is not accepted as proof of exit: real Darwin tests observed that exception while
a dying PID still existed in the idle state.

`agent/tests/test_owned_process.py` exercises owner SIGKILL after a recorded launch
and after Popen but before PID persistence, verified restart, an untouched unrelated
sentinel, immediate successful/failed child exits, TERM-to-KILL escalation, lock
exclusion, ambiguous markers, malformed and
mismatched identities, and native lookup denial. The existing Python 3.10 CI job
runs the base agent first, then installs the optional extra and runs this file.

## Native launcher integration

The caller holds the owner context for the full supervisor lifetime. A pinned-model
preflight precedes recovery, and recovery precedes key rotation or replacement.
The unique marker file is passed to llama-server as its existing `--api-key-file`
argument. Owned mode does not write or trust the legacy `child.json`; if that old
record exists, startup and cleanup report unresolved ownership rather than
discarding it. Existing agent callers retain the legacy mode.

Shutdown requires both verified native exit and completed output drainage. A
missing ownership record cannot override a still-live local Popen handle. An
incomplete stop retains the child handle and key so a subsequent cleanup can
finish; a pending output reader prevents another owned launch.

Owned gateway identities include `owned_process.py`, `runtime_args.py`, and the
actual installed psutil Python/native file hashes in addition to `gateway.py`
and `runtime.py`. The planner accepts exactly the legacy or owned identity shape
and binds the complete shape into its artifact digest. This creates new artifacts;
historical manifests and evidence are preserved.

The development gateway supports only the two explicit context-size choices
2048 and 4096, with unchanged token and deadline bounds. Its command still requires
a new output directory. This is native ownership integration, not automatic bundle
activation or a persistent deployment service.

Clean source `a0d3bc5` passed a real local Qwen ownership check: one fixed-skill
request with context 2048, owner SIGKILL with the native child confirmed alive,
then a successor using the same ownership directory. The successor recorded the
old child's verified exit before starting context 4096 and serving the second
request. Both calls returned the expected skill (116 prompt/18 completion tokens
each); configuration and planner artifact digests differed. Final process,
listener and credential-marker cleanup passed, and the unrelated sentinel was
untouched. The [sanitized evidence](../../examples/manipulation/evidence/native-qwen-owner-recovery.json)
records observed request times of 0.723 and 0.709 seconds; other tests could run
concurrently, so these are not isolated latency benchmarks. This exercised the
actual supervisor through a qualification driver, not deployment automation.

Local verification passed all 13 cases on Darwin arm64/Python 3.12.13 and in a
disposable Linux 6.12.76 aarch64 container/Python 3.12.14, both with psutil 7.2.2.
The Linux tests ran as unprivileged UID 501. Python 3.10 execution is configured in
CI; local checks covered its syntax and dependency metadata, not a 3.10 test run.
