# Sandbox — contract friction notes (v1)

Nothing blocked the build; two interface gaps in `src/sandbox/api.ts` (not modified) were worked
around inside sandbox-owned code:

1. **`ToolEmulator.handler(args, world, ctx)` has no clock parameter**, but `calendar.today` and
   `ams.list_expiring` must perceive SIM time. Workaround: `createWorldStore` returns
   `SimWorldStore extends WorldStore` with a `clockRef: ClockPort` property (`src/sandbox/world.ts`);
   emulators reach it via `worldClock()` in `src/sandbox/emulators/util.ts`. If api.ts ever adds a
   clock to the handler signature, delete `worldClock` and take it from there.

2. **`WorldEvent` includes `portal_transition`, but no `WorldStore` method emits it** (mutations only
   emit `message_sent/message_delivered/record_changed`). The counterparty engine needs to emit it on
   portal fulfillment. Workaround: `SimWorldStore.emitEvent(e)` on the same extension.

Interpretation choices (contract silent, documented so graders/runner agree):

- **Gateway dedupe event shape**: a replayed effectful invoke appends `tool_intent` + `tool_result`
  (recorded result) + `budget_debit` — no `tool_approved`/`tool_executed`, since nothing re-executed.
  Two identical effectful invokes therefore log 8 events, not 10. Handler errors are NOT recorded in
  the dedupe map: a retry after a thrown handler re-runs.
- **`missionBudgetUsd`** on the gateway is accepted but unused in v1 (flat 0.001/invoke metering;
  the USD guard lives in `runUntil` over `budget_debit` events).
- **Portal presets**: a `channel: 'portal'` script expands to no email rules; its profile governs
  fulfillment of pending `portal_request` records (silent = never; cooperative = 1-2d uniform;
  others = 1d; `params.document` names the pack file attached; `params.fulfillAfter` overrides).
- **`pack.json` threads**: shape used is
  `{ threadId, messages: [{ from, to, subject, body, direction?, ts?, attachments? }] }` —
  the manifest shape given for records/files did not specify threads. Seeding is silent (no
  WorldEvents), so counterparties never react to fixture history.
