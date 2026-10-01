# Configurations workspace

Data layer for `/app/configurations`: configurations (robot, edge hardware, edge and
cloud models, routing and safety, per revision), the robots attached to them, evaluation
suites and runs, rollouts, action traces and logs.

## Data flow

- **Document.** Each account owns one JSON document named `configurations`
  (`ConvoyWorkspace`, schema version 1, `types.ts`). `client.ts` reads it through
  `/api/platform/workspace-documents/configurations`, checks it with `validate.ts`, and
  keeps the document's `revision`. An account's own robots, sites and results live only
  in that document, never in this repository.
- **Writes (documents contract v2).** Every `PUT` names what it replaces:
  `If-Match: "<revision>"` for the stored document, `If-None-Match: *` while there is
  none, so the sample is never written over a document that appeared meanwhile. The
  response is metadata only (`revision`, `size_bytes`, `updated_at`); the document sent
  becomes the confirmed one. Changes show at once and roll back on failure. On `412`
  the store re-reads the document, re-applies the change (an updater function) once and
  retries; if that fails it shows a conflict notice. Each write has an
  `Idempotency-Key`, reused only for the identical retried write. `saveErrorMessage`
  tells apart the document limit and a reused key (both `409`), a conflict (`412`), a
  missing precondition (`428`), too many writes (`429`, with Retry-After) and a
  document that is too large (`413`). The store re-reads on window focus and when the
  tab becomes visible (at most every 10 s, never while a save is pending).
- **Fallback and import.** Without a document (404) the generic sample from `sample.ts`
  is shown and the first save creates the document. "Import workspace" checks the file
  size (2 MiB) before reading it, creates the document with `If-None-Match: *`, and asks
  for confirmation before it replaces a stored one (`If-Match`). An invalid or
  unreadable document also shows the sample, with one notice, and saving is disabled.
- **Live bindings.** A robot with `deviceId` (a device id, or `CONFIGURED_DEVICE` for
  the workspace's configured device) gets measured telemetry, series, edge latency and
  inference traces from the existing device endpoints. `live.ts` maps them and runs one
  shared poll (15 s, one request cycle at a time, paused while hidden, no extra poll when
  the last one is recent, backoff on 429/5xx). Freshness ("no recent report") is judged
  on the server's clock (the snapshot's `fetched_at`, else the response `Date` header),
  not the browser's. `LiveDeviceProvider` is
  mounted inside the signed-in session (`ConfigurationsRoot`), so signing out drops the
  poller and its readings.
- **Reading.** Pages use `useWorkspace()`, the selectors in `selectors.ts` (re-exported
  by `client.ts`), `format.ts` for numbers, times and evidence labels, `routes.ts` for
  URLs, `table.ts` for sorting (missing values last in both directions) and
  `mutations.ts` for changes. Pages re-render on a coarse 15 s clock; measured ages tick
  per second inside their badges only.

## Displayed health

One rule decides a robot's health everywhere (badges, filters, triage order, the
attention banner and the index counts): an open attention flag shows as `attention`;
otherwise an offline device shows `offline`; otherwise warnings show as `degraded`;
otherwise the stored or measured health. `displayHealth` and `attentionRobots` in
`selectors.ts` implement it; nothing else derives health on its own.

## Provenance rule

Every metric carries one provenance: `measured` (read from a connected device now),
`recorded` (stored evidence with a date, n and source), `sample` (illustrative) or
`not-reported` (missing, shown as "Not reported", never 0). Measured values are never
stored in the document: the validator rejects them, and a robot with a live binding
stores no telemetry. Measured and sample values are never combined in one figure.

## Tests

- Unit: `npm run test:configurations` (selectors, client contract, live poller, ...).
- End to end: `tests/e2e/configurations*.spec.ts`, with the documents API mocked
  (`tests/e2e/support/documents.ts`). `configurations-journeys.spec.ts` records video,
  a trace and milestone screenshots of six user journeys.
- Live: `npm run test:live` runs `tests/live` against a deployed console. It is skipped
  unless `CONVOY_LIVE_BASE_URL`, `CONVOY_LIVE_EMAIL` and `CONVOY_LIVE_PASSWORD` are set;
  journeys that write run only with `CONVOY_LIVE_ALLOW_WRITES=1` and restore the
  document they found. Artifacts go to `CONVOY_LIVE_OUTPUT_DIR` (default
  `test-results/live`).
