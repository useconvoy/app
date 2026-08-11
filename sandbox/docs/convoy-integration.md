# Convoy integration

The existing Convoy connector classes now accept provider endpoint overrides in their connection `config`:

| Provider | Configuration fields |
| --- | --- |
| Slack | `apiBaseUrl` |
| GitHub | `apiBaseUrl` |
| Google | `tokenUrl`, `driveBaseUrl`, `sheetsBaseUrl` |

Snake-case aliases are also accepted for backend-created records. Omitting the fields retains the official provider URLs.

A rehearsal environment should create one sandbox, then compile connection configuration using its sandbox ID:

```text
slack.apiBaseUrl = {sandboxOrigin}/s/{sandboxId}/slack/api
github.apiBaseUrl = {sandboxOrigin}/s/{sandboxId}/github
google.tokenUrl = {sandboxOrigin}/s/{sandboxId}/google/oauth2/token
google.driveBaseUrl = {sandboxOrigin}/s/{sandboxId}/google/drive/v3
google.sheetsBaseUrl = {sandboxOrigin}/s/{sandboxId}/google/sheets/v4
```

The binding should keep the real connection manifest and tool allowlist. Only the endpoint and synthetic credential change. This ensures rehearsal exercises the same connector implementation, side-effect classification, gateway authorization, promotion, and result handling as production.

Recommended control-plane flow:

1. Resolve a hermetic environment binding.
2. Provision a sandbox from the environment's fixture/scenario version.
3. Mint or retrieve provider-native synthetic principals for its connections.
4. Compile per-connection endpoint overrides and secret references.
5. Start the run with those immutable bindings.
6. Send the runtime idempotency key on every promoted connector call.
7. Snapshot on pause and restore on resume if the rehearsal must be replayable.
8. Evaluate final provider state and retain the report with run artifacts.
9. Destroy the sandbox according to retention policy.
