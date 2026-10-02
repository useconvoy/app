# Registered timing on Jetson

The registered MuJoCo runtime and its enrollment/deployment/task pipeline were executed on the existing
Jetson Orin Nano Super on 2026-10-01. Both complete runs passed all six tests; the second retained
seven measurement reports. This is hardware verification of the joint-state reference path, not a
qualification of learned manipulation, camera processing, physical dynamics or cloud planning.

## Reproducible source and environment

- Source: `95f0dd613a89af214237c7bba6f754b972ece5f5` (runtime from `b03abbd`, plus measurement recording).
- L4T R36.4.7; Linux aarch64; Python 3.11.14; MuJoCo 3.3.0.
- Frozen `integrations/simulation/uv.lock`, installed with the managed extra in a separate environment.
- No changes to the running device agent, deployed models, motor interfaces or hosted services.
- The test API binds only to loopback and uses disposable enrollment, database and policy-worker state.
- Simulation: one actuated revolute joint, imported MJCF, 50 Hz control / 2 ms native physics timestep.
- Existing host services were left running. CPU/GPU load and thermal conditions were not controlled;
  results must not be treated as a bound under arbitrary system load.

Run from `integrations/simulation` on the target host:

```sh
uv sync --frozen --python 3.11 --extra managed
uv run --frozen --extra managed pytest \
  tests/test_realtime.py tests/test_qualification_pipeline.py -q \
  --basetemp=/tmp/convoy-registered-acceptance
```

Use a new scratch directory for `--basetemp`; pytest clears it. Measurement files are named
`timing-*.json` inside the test subdirectories. Copy only those files when retaining evidence; other
test files contain disposable credentials and enrollment state. CI retains only measurement JSONs.

## Recorded outcomes

| Case | Observed result |
| --- | --- |
| Managed HTTP reference worker | Reached target in 23 physics ticks with 7 applied commands. Observation-to-action p95 59.10 ms; maximum physics completion lag 2.29 ms. Short run: insufficient timing evidence. |
| Delayed HTTP worker | No actions applied. Physics advanced 10 ticks while waiting; the 200 ms observation budget expired. Timing failed as expected. |
| Sustained direct reference | 210 physics ticks and 210 applied targets. Observation-to-action p95 19.65 ms; maximum completion lag 3.40 ms. Observed deadlines met; task deliberately did not succeed. |
| Expired observation | Physics advanced 10 ticks; stale command was rejected before application. |
| Expired target | Previously applied target expired and the simulator entered position hold; timing failed. |
| Paused physics, capture | The next observation detected the scheduler stall and reported timing failure. |
| Paused physics, cleanup | Shutdown retained the scheduler stall even without another policy observation. |

The sustained direct-reference test uses a 100 ms physics-lag budget and 200 ms observation-age
budget. The HTTP test also uses a 100 ms physics-lag budget. These are explicit test contracts, not a
claim that every possible configuration or tighter lag limit has been qualified. The raw reports retain
each case's contract, distributions, sample counts and process identity. Timing includes transport and
queueing where present; it is not a pure neural inference benchmark.

[Recorded evidence](../evidence/registered-timing-jetson-2026-10-01.json) includes hashes of the runtime
and test files checked against the local source after retrieval, platform versions, acceptance counts
and all seven reports. The complete second acceptance took 47.816 seconds. Test subprocesses exited;
the pre-existing device agent remained running.

## Remaining hardware work

Pair an actual compatible learned edge policy with a remote planner, add camera-conditioned observations,
measure simulator overhead and device utilization/thermal conditions, and repeat under load and network
faults. Add the experiment runner and recordings to the project UI. These local hardware test tasks
were not created on the hosted site's database and do not appear in its task history.
