# Configurations workspace

Data layer for `/app/configurations`: configurations (robot, edge hardware, edge and
cloud models, routing and safety, per revision), the robots attached to them, evaluation
suites and runs, rollouts, action traces and logs.

## Data flow

- **Document.** Each account owns one JSON document named `configurations`
  (`ConvoyWorkspace`, schema version 1, `types.ts`). `client.ts` reads it through
  `/api/platform/workspace-documents/configurations`, checks it with `validate.ts`, and
  writes changes with `PUT` (an `Idempotency-Key` per change, optimistic display, a
  re-read and one re-apply on `409`). An account's own robots, sites and results live
  only in that document, never in this repository.
- **Fallback.** Without a document (404) the generic sample from `sample.ts` is shown
  and the first save or "Import workspace" creates the document. An invalid or
  unreadable document also shows the sample, with a notice, and saving is disabled.
- **Live bindings.** A robot with `deviceId` (a device id, or `CONFIGURED_DEVICE` for
  the workspace's configured device) gets measured telemetry, series, edge latency and
  inference traces from the existing device endpoints. `live.ts` maps them and runs one
  shared poll (15 s, one request cycle at a time, paused while hidden, backoff on
  429/5xx). `LiveDeviceProvider` in the `/app` layout owns it.
- **Reading.** Pages use `useWorkspace()`, the selectors in `selectors.ts` (re-exported
  by `client.ts`), `format.ts` for numbers, times and evidence labels, `routes.ts` for
  URLs and `mutations.ts` for changes.

## Provenance rule

Every metric carries one provenance: `measured` (read from a connected device now),
`recorded` (stored evidence with a date, n and source), `sample` (illustrative) or
`not-reported` (missing, shown as "Not reported", never 0). Measured values are never
stored in the document: the validator rejects them, and a robot with a live binding
stores no telemetry. Measured and sample values are never combined in one figure.
