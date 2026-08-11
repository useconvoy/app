# Connector sandbox

`sandbox/` contains disposable, stateful stubs for the enterprise systems an
agent can use. They speak provider-shaped HTTP APIs, so the real Convoy
connector code performs the reads and writes. The only difference between a
rehearsal and production connector call is the endpoint and a synthetic
credential; there is no test-only tool-call transport.

The currently implemented services are:

- `sandbox_slack` — Slack Web API-shaped channels, messages, and posting;
- `sandbox_drive` — Google Drive API-shaped file listing and sharing rules;
- `sandbox_sheets` — Google Sheets API-shaped reads and row appends;
- `sandbox_github` — GitHub REST-shaped file reads and issue creation.

Drive and Sheets share the `google` provider route because the production
Google connector authenticates both with the same service-account credential.

This package implements the hybrid design from the connector-sandbox research:

- deterministic local simulations on every pull request;
- provider-shaped HTTP APIs and synthetic provider-native identities;
- snapshots, restore, fork, reset, simulated time, audit events, and idempotent writes;
- signed webhooks with deterministic retry scheduling;
- semantic scenarios and objective state-based evaluation;
- a small AutomationBench compatibility adapter;
- configurable Convoy connectors, so the same connector targets either this simulator or an official provider sandbox;
- official-provider contract tests as a separate, credentialed nightly layer.

## Repository layout

```text
sandbox/
  packages/
    runtime/                 # lifecycle, persistence, identity, audit, idempotency
    scenarios/               # deterministic outcome evaluation
    contract-testkit/        # shared simulator/provider contract assertions
    webhook-sink/            # signatures, delivery, retries, replayable state
  providers/
    slack/                   # Slack Web API-shaped simulation
    google-workspace/        # OAuth, Drive, and Sheets simulation
    github/                  # GitHub REST-shaped simulation
  adapters/
    automationbench/         # semantic tool-call translation spike
  apps/
    sandbox-control-plane/   # lifecycle and provider HTTP API
  fixtures/                  # resettable fake data and synthetic identities
  scenarios/                 # cross-provider tasks and scoring rules
```

## Run it

Node 22 or newer is the only local dependency.

```bash
npm test
npm run demo
npm start
```

Or without npm:

```bash
node --test tests/*.test.mjs
node tests/http.integration.mjs
node examples/demo.mjs
node apps/sandbox-control-plane/src/server.mjs
```

The server listens on `http://localhost:8790`.

Provision the included fixture:

```bash
curl -X POST http://localhost:8790/v1/sandboxes \
  -H 'content-type: application/json' \
  -d '{"sandboxId":"dev","fixtureName":"connector-development"}'
```

Call the provider APIs using the synthetic identities from the fixture:

```bash
curl 'http://localhost:8790/s/dev/slack/api/conversations.list' \
  -H 'authorization: Bearer xoxb-convoy-sandbox'

curl 'http://localhost:8790/s/dev/google/drive/v3/files?q=%27folder-shared%27%20in%20parents' \
  -H 'authorization: Bearer google-convoy-sandbox'

curl 'http://localhost:8790/s/dev/github/repos/sandbox/example-service/issues' \
  -H 'authorization: Bearer github-convoy-sandbox'
```

Create and restore a snapshot:

```bash
curl -X POST http://localhost:8790/v1/sandboxes/dev/snapshots \
  -H 'content-type: application/json' \
  -d '{"label":"before-agent-run"}'

curl -X POST http://localhost:8790/v1/sandboxes/dev/restore \
  -H 'content-type: application/json' \
  -d '{"snapshotId":"snap_0001"}'
```

Set `SANDBOX_ADMIN_TOKEN` outside local development to require bearer authentication on lifecycle APIs. Provider routes always require their sandbox-specific synthetic credential. Raw fixture tokens are hashed before runtime state is persisted.

## Convoy connector configuration

Hermetic connections select this control plane through non-secret connection configuration. Production remains the default when these fields are absent.

```json
{
  "slack": {
    "apiBaseUrl": "http://sandbox:8790/s/dev/slack/api"
  },
  "google": {
    "tokenUrl": "http://sandbox:8790/s/dev/google/oauth2/token",
    "driveBaseUrl": "http://sandbox:8790/s/dev/google/drive/v3",
    "sheetsBaseUrl": "http://sandbox:8790/s/dev/google/sheets/v4"
  },
  "github": {
    "apiBaseUrl": "http://sandbox:8790/s/dev/github"
  }
}
```

See [Architecture](docs/architecture.md), [Convoy integration](docs/convoy-integration.md), and [official sandbox testing](docs/official-sandboxes.md).
