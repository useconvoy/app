# Architecture

## Boundary model

The simulator deliberately keeps four concepts separate:

1. A **workspace connection** owns a credential and provider configuration.
2. A **sandbox** is a resettable logical company state scoped to one rehearsal or evaluation.
3. A **synthetic principal** is the bot, service account, or app identity recognized by one simulated provider.
4. A **run** receives short-lived permission to call an allowed connection; it never owns the provider credential.

The disposable agent compute only receives the gateway capability. Provider credentials stay in the connector gateway. In the local simulator, fixture credentials are synthetic and are converted to SHA-256 hashes before state is written.

## Request path

```text
Agent run
  -> Convoy tool gateway (policy + idempotency key)
  -> real Convoy connector
  -> provider base URL selected by environment binding
       -> hermetic: Convoy Sandbox
       -> live: Slack / Google / GitHub
  -> provider-shaped response
  -> audit event and optional signed webhook
```

The provider simulators mutate only their own state partition. The runtime owns lifecycle, persistence, locking, identity verification, the idempotency journal, audit records, clock advancement, and webhooks.

## Lifecycle contract

Every provider participates in the same lifecycle:

- `provision`: create an isolated sandbox and seed each provider;
- `seed`: replace the baseline fixture;
- `inspect`: read state with credential material removed;
- `snapshot`: persist a named immutable point-in-time copy;
- `restore`: replace current state from a snapshot;
- `fork`: create another isolated sandbox from a snapshot;
- `reset`: return to the most recently seeded baseline;
- `advanceClock`: move deterministic time and deliver due webhooks;
- `destroy`: move the sandbox to recoverable destroyed storage, or purge explicitly.

Provider writes can include `Idempotency-Key` or `X-Convoy-Idempotency-Key`. The runtime journals the complete response and returns it on retries without repeating state changes or webhooks. Requests within one sandbox are serialized to make this guarantee hold for concurrent retries.

## Fidelity boundary

The local providers reproduce the subset used by Convoy's managed connectors, including response/error shapes. They intentionally do not attempt to mirror every vendor endpoint.

- Local simulator tests validate deterministic business semantics, safety, and retry behavior.
- The shared contract testkit validates response shapes against both targets.
- Credentialed official-sandbox tests validate OAuth, permissions, pagination, rate limits, and vendor drift.

New provider capabilities should be introduced as a vertical slice: connector tool, provider simulator behavior, fixture data, contract assertion, official-sandbox check, and scenario criterion where appropriate.
