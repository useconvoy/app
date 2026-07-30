# Convoy Worlds

A World is a resettable slice of a company used to develop, demo, and evaluate
enterprise agents. It is not a scripted happy-path UI. Agents receive typed
tools, mutate state, encounter real policy gates, and are scored from the final
state.

## World package

Each `catalog/<world-id>` directory contains:

```text
world.json           identity, scenario, services, tools, policies, evaluator
initial-state.json   deterministic company snapshot
evaluator.mjs        state-based checkpoints and evidence
successful-run.json  reference trajectory used as a regression fixture
```

The runtime supplies reset, inspect, act, and evaluate over HTTP and MCP.

## Adding a use case

Prefer a new World when the company state is meaningfully different. Prefer a
new scenario/evaluator on an existing World when it reuses the same accounts,
people, channels, documents, systems, and policies.

1. Name the business outcome and the human who owns it.
2. Seed enough messy evidence for the mission to require judgment.
3. Define typed tools from reusable enterprise capabilities.
4. Put authorization and irreversible-action gates in code.
5. Score resulting state, not exact wording or one expected trajectory.
6. Include safety checkpoints and evidence strings that a dashboard can show.
7. Add one passing reference trajectory and at least one policy-denial test.
8. Validate that reset removes all prior actions.

## Evaluator rules

- Scores total 100 and declare a pass threshold in `world.json`.
- Checkpoints should test business outcomes such as coverage, accountability,
  evidence quality, human approval, and safety.
- Do not award points merely because an agent called a particular tool.
- Do not use model grading for facts already present in structured state.
- If model grading is eventually useful for memo quality, pin the rubric/model,
  retain its inputs and output, and keep critical safety checks deterministic.

## Shared capability roadmap

The current Worlds reuse chat, work, drive, approval, evidence, and CRM
primitives. High-leverage next capabilities are:

- calendar scheduling and attendance;
- email with draft/send separation;
- service desk tickets and change windows;
- finance/procurement records;
- data warehouse queries with row-level policy;
- browser tasks against resettable internal applications.

New capabilities belong in the common runtime only after two missions need
them. That keeps Convoy broad without turning the platform into a collection of
one-off demos.

## Portfolio-scale vendor assurance

`aurelia-vendor-assurance` is the generated scale World. Its default fixture
contains 50 vendors, 200 evidence requirements, and 20 hidden exceptions across
security, privacy, architecture, and legal domains. The agent-facing state and
typed tools do not expose `ground-truth.json`; only the evaluator reads that
ledger.

The checked-in reference trajectory is deliberately deterministic so it can
regression-test the environment and scoring contract. A model-backed or
recursive runner uses the same World API and MCP tools. See
`catalog/aurelia-vendor-assurance/README.md` for generation profiles and run
commands.
