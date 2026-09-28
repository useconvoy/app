# Owned local process prerequisite

`convoy_agent.owned_process.OwnedProcess` is an opt-in helper for a known,
foreground, single-process launcher. It is not yet connected to RuntimeSupervisor,
the coordinator, or bundle activation. The base agent remains dependency-free and
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

Local verification passed all 13 cases on Darwin arm64/Python 3.12.13 and in a
disposable Linux 6.12.76 aarch64 container/Python 3.12.14, both with psutil 7.2.2.
The Linux tests ran as unprivileged UID 501. Python 3.10 execution is configured in
CI; local checks covered its syntax and dependency metadata, not a 3.10 test run.
