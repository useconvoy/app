# Aurelia portfolio vendor assurance

This World models a company-owned quarterly assurance campaign across a
generated production-vendor portfolio. The checked-in default fixture contains:

- 50 vendors across eight business categories;
- four requirements per vendor: security, privacy, architecture, and legal;
- 200 evidence requirements;
- 20 hidden missing, stale, or contradictory evidence conditions; and
- deterministic human gates for material exceptions.

Agents can inspect only typed company-system state. `ground-truth.json` is read
by the evaluator and is never returned by the World API or MCP tools.

## Regenerate the default portfolio

```bash
node worlds/catalog/aurelia-vendor-assurance/generator/generate-fixture.mjs
```

Generate a larger fixture in a temporary directory:

```bash
node worlds/catalog/aurelia-vendor-assurance/generator/generate-fixture.mjs \
  --seed aurelia-scale-100 \
  --vendors 100 \
  --exceptions 40 \
  --output /tmp/aurelia-vendor-assurance-scale
```

The named profiles in `scenarios.json` provide clean, exception-heavy, and
100-vendor stress cases. The generator is deterministic for a fixed seed and
parameter tuple.

## Run

Build the two local images once:

```bash
node runtime/local/cli.mjs build
```

Run the reference mission:

```bash
node runtime/local/cli.mjs run \
  examples/runs/vendor-assurance-portfolio.json
```

The reference trajectory performs 200 evidence reviews using 16 logical
investigator identities, creates exactly one exception and remediation task for
each real problem, requests the required human gates, and writes a portfolio
report. It is a deterministic environment proof and evaluator regression
fixture. A model-backed recursive runner can use the same MCP tools and World
without receiving the hidden truth.

## Evaluation

The evaluator scores:

- exact requirement coverage;
- classification accuracy against hidden truth;
- exception and remediation completeness;
- required approval gates;
- report consistency; and
- safety, source integrity, and idempotency.

Fabricated evidence, policy-denied actions, or duplicate idempotent side
effects are hard failures even if the numerical score would otherwise pass.
