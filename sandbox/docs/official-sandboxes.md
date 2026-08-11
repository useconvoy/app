# Official provider sandbox testing

The local simulator is the fast test target, not a claim of complete vendor fidelity. Run a second contract layer against official or dedicated test tenants.

## Test cadence

- Every pull request: local provider suites, lifecycle tests, scenarios, idempotency, webhook behavior, and HTTP contracts.
- Nightly: safe read/write contracts against dedicated Slack, Google Workspace, and GitHub test organizations.
- Before releasing a connector expansion: permission-boundary, pagination, error, rate-limit, and webhook replay tests against the official provider.

## Identity rules

- Use one provider-native runtime principal per provider and environment.
- Use bot/app/service identities where supported.
- Add synthetic human users for sharing, role, and impersonation tests.
- Keep an MFA-protected break-glass administrator outside the runtime.
- Never use one interactive account across providers or customer organizations.
- Never run the contract suite against production customer data.

## Contract promotion

Each contract starts against the simulator. Once stable, the exact request and normalized assertions are run against the official sandbox. Provider-specific volatile fields—IDs, timestamps, hostnames, request IDs—should be normalized, while permissions, object transitions, error classes, and webhook effects remain asserted.

Official credentials belong in the CI secret store. The test process should receive short-lived tokens where possible, and the resulting logs must redact request authorization and provider response headers that may contain identity or quota information.
