/**
 * Fixture data for the routine evaluation surfaces, part of the canonical
 * fixture world: scenario suites, test-score trends, per-run scorecards,
 * and scored-run trajectories for the three fixture routines. The shapes
 * mirror what the agent-evals service will serve; the FixtureEvalsClient in
 * `lib/api/evals` is the only consumer. TODO(evals): retire this file when
 * the service is live.
 *
 * Trend shapes by design: the access review climbs 72 -> 91 across eight
 * rehearsals, the vendor check holds steady around 85, and the attestation
 * chase is sparse (scored twice). Every rehearsal trajectory carries
 * sandbox: true; the single production trajectory is unlabeled, since
 * production is always the unlabeled default.
 */
import type { EvalSuite, RunScorecard, TestScorePoint, Trajectory } from "@/lib/api/evals";

export const fixtureSuites: EvalSuite[] = [
  {
    id: "suite-access-core",
    routineId: "routine-access-review",
    name: "Core review scenarios",
    scenarioCount: 12,
  },
  {
    id: "suite-access-edge",
    routineId: "routine-access-review",
    name: "Difficult cases",
    scenarioCount: 5,
  },
  {
    id: "suite-vendor-core",
    routineId: "routine-vendor-check",
    name: "Document refresh scenarios",
    scenarioCount: 8,
  },
  {
    id: "suite-attest-core",
    routineId: "routine-attestation-chase",
    name: "Reminder scenarios",
    scenarioCount: 6,
  },
];

export const fixtureTrends: Record<string, TestScorePoint[]> = {
  "routine-access-review": [
    { at: "2026-06-16T09:10:00Z", score: 72 },
    { at: "2026-06-23T09:12:00Z", score: 74 },
    { at: "2026-06-30T09:08:00Z", score: 78 },
    { at: "2026-07-07T09:15:00Z", score: 77 },
    { at: "2026-07-14T09:11:00Z", score: 82 },
    { at: "2026-07-21T09:09:00Z", score: 84 },
    { at: "2026-07-28T09:13:00Z", score: 88 },
    { at: "2026-08-04T09:10:00Z", score: 91 },
  ],
  "routine-vendor-check": [
    { at: "2026-07-01T10:20:00Z", score: 84 },
    { at: "2026-07-10T10:24:00Z", score: 86 },
    { at: "2026-07-19T10:18:00Z", score: 85 },
    { at: "2026-07-28T10:22:00Z", score: 85 },
    { at: "2026-08-04T10:19:00Z", score: 86 },
  ],
  "routine-attestation-chase": [
    { at: "2026-07-22T14:05:00Z", score: 79 },
    { at: "2026-08-03T14:02:00Z", score: 83 },
  ],
};

export const fixtureTrajectories: Record<string, Trajectory[]> = {
  "routine-access-review": [
    {
      runId: "run-eval-ar-8",
      at: "2026-08-04T09:10:00Z",
      sandbox: true,
      headline: "Reconciled every difference and chased both non-responders on time",
    },
    {
      runId: "run-eval-ar-7",
      at: "2026-07-28T09:13:00Z",
      sandbox: true,
      headline: "Wrote clean exception memos but chased one person a day late",
    },
    {
      runId: "run-eval-ar-6",
      at: "2026-07-21T09:09:00Z",
      sandbox: true,
      headline: "Missed one access difference in the HR comparison",
    },
    {
      runId: "run-eval-ar-live-1",
      at: "2026-07-02T09:00:00Z",
      sandbox: false,
      headline: "Quarterly review landed with all sign-offs collected",
    },
  ],
  "routine-vendor-check": [
    {
      runId: "run-eval-vc-3",
      at: "2026-08-04T10:19:00Z",
      sandbox: true,
      headline: "Collected every due document and filed them correctly",
    },
    {
      runId: "run-eval-vc-2",
      at: "2026-07-19T10:18:00Z",
      sandbox: true,
      headline: "Filed documents correctly but asked one vendor twice",
    },
  ],
  "routine-attestation-chase": [
    {
      runId: "run-eval-ac-2",
      at: "2026-08-03T14:02:00Z",
      sandbox: true,
      headline: "Reminded everyone outstanding and kept an accurate tally",
    },
    {
      runId: "run-eval-ac-1",
      at: "2026-07-22T14:05:00Z",
      sandbox: true,
      headline: "Sent reminders on time but the summary missed two confirmations",
    },
  ],
};

export const fixtureScorecards: Record<string, RunScorecard> = {
  "run-eval-ar-8": {
    runId: "run-eval-ar-8",
    score: 91,
    criteria: [
      { name: "Finds every access difference", pass: true, note: "All 14 differences found" },
      { name: "Exception memos are complete", pass: true, note: "Each memo names the person and the difference" },
      { name: "Chases non-responders on schedule", pass: true, note: "Both reminders went out on the right day" },
      { name: "Final packet is ready to sign", pass: false, note: "One appendix was out of order" },
    ],
  },
  "run-eval-ar-7": {
    runId: "run-eval-ar-7",
    score: 88,
    criteria: [
      { name: "Finds every access difference", pass: true, note: "All 14 differences found" },
      { name: "Exception memos are complete", pass: true, note: "Clear and correctly addressed" },
      { name: "Chases non-responders on schedule", pass: false, note: "One reminder went out a day late" },
      { name: "Final packet is ready to sign", pass: true, note: "Packet assembled in order" },
    ],
  },
  "run-eval-ar-6": {
    runId: "run-eval-ar-6",
    score: 84,
    criteria: [
      { name: "Finds every access difference", pass: false, note: "Missed one contractor account" },
      { name: "Exception memos are complete", pass: true, note: "Memos covered what was found" },
      { name: "Chases non-responders on schedule", pass: true, note: "Reminders on time" },
      { name: "Final packet is ready to sign", pass: true, note: "Packet assembled in order" },
    ],
  },
  "run-eval-vc-3": {
    runId: "run-eval-vc-3",
    score: 86,
    criteria: [
      { name: "Requests reach every due vendor", pass: true, note: "All 6 vendors asked once" },
      { name: "Received documents are checked", pass: true, note: "Two incomplete documents caught" },
      { name: "Records end up in the right place", pass: false, note: "One document filed under the old vendor name" },
    ],
  },
  "run-eval-vc-2": {
    runId: "run-eval-vc-2",
    score: 85,
    criteria: [
      { name: "Requests reach every due vendor", pass: false, note: "One vendor was asked twice" },
      { name: "Received documents are checked", pass: true, note: "Checks were thorough" },
      { name: "Records end up in the right place", pass: true, note: "All filings correct" },
    ],
  },
  "run-eval-ac-2": {
    runId: "run-eval-ac-2",
    score: 83,
    criteria: [
      { name: "Every outstanding person gets a reminder", pass: true, note: "All 9 reminded" },
      { name: "Confirmations are recorded promptly", pass: true, note: "Recorded within the hour" },
      { name: "The summary matches the record", pass: false, note: "Summary lagged one late confirmation" },
    ],
  },
  "run-eval-ac-1": {
    runId: "run-eval-ac-1",
    score: 79,
    criteria: [
      { name: "Every outstanding person gets a reminder", pass: true, note: "All reminded on time" },
      { name: "Confirmations are recorded promptly", pass: false, note: "Two confirmations recorded late" },
      { name: "The summary matches the record", pass: false, note: "Summary missed the late two" },
    ],
  },
};
