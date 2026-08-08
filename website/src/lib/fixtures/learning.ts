/**
 * Fixture data for the learning surfaces, used by tests only: proposed
 * improvements with diffs and test-score evidence, plus baseline changelog
 * entries per routine. The shapes mirror what the learning service will
 * serve; production renders the queue empty until the service exists.
 */
import type { Improvement } from "@/lib/api/learning";

export const fixtureImprovements: Improvement[] = [
  {
    id: "improvement-ar-chase-earlier",
    routineId: "routine-access-review",
    title: "Chase non-responders before assembling memos",
    summary:
      "Recent rehearsals show sign-offs arrive faster when reminders go out before the memos are assembled, so the review finishes with fewer loose ends.",
    diff: [
      { kind: "change", text: "Move the chase step ahead of memo assembly" },
      { kind: "add", text: "Send a second reminder two days after the first" },
    ],
    evidence: {
      testScoreBefore: 84,
      testScoreAfter: 91,
      runIds: ["run-eval-ar-6", "run-eval-ar-7", "run-eval-ar-8"],
    },
    status: "proposed",
  },
  {
    id: "improvement-vc-single-ask",
    routineId: "routine-vendor-check",
    title: "Ask each vendor exactly once",
    summary:
      "One vendor received the same request twice in rehearsal. Tracking which vendors were already asked keeps the outreach polite and the score steady.",
    diff: [
      { kind: "add", text: "Keep a checklist of vendors already asked" },
      { kind: "remove", text: "Re-request documents when a reply is slow" },
    ],
    evidence: {
      testScoreBefore: 85,
      testScoreAfter: 86,
      runIds: ["run-eval-vc-2", "run-eval-vc-3"],
    },
    status: "proposed",
  },
  {
    id: "improvement-ac-tally-live",
    routineId: "routine-attestation-chase",
    title: "Update the tally as confirmations arrive",
    summary:
      "The summary lagged behind late confirmations. Recording each confirmation the moment it arrives keeps the final tally honest.",
    diff: [{ kind: "change", text: "Record each confirmation immediately instead of in a batch" }],
    evidence: {
      testScoreBefore: 79,
      testScoreAfter: 83,
      runIds: ["run-eval-ac-1", "run-eval-ac-2"],
    },
    status: "shipped",
    shippedAt: "2026-08-03T15:30:00Z",
  },
];

export interface FixtureChangelogEntry {
  routineId: string;
  at: string;
  note: string;
}

/** Baseline changelog entries; shipped improvements append after these. */
export const fixtureChangelog: FixtureChangelogEntry[] = [
  {
    routineId: "routine-access-review",
    at: "2026-06-12T09:00:00Z",
    note: "First version set up with the Convoy team",
  },
  {
    routineId: "routine-vendor-check",
    at: "2026-06-20T11:00:00Z",
    note: "First version set up with the Convoy team",
  },
  {
    routineId: "routine-attestation-chase",
    at: "2026-07-15T13:00:00Z",
    note: "First version set up with the Convoy team",
  },
];
