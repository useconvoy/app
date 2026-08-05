/**
 * The canonical fixture world, reused across unit, component, and E2E
 * suites and by the fixture adapters that stand in for services that are
 * not live yet. One world everywhere: the two demo routines plus the
 * attestation chase, generic J. Doe-style people, and the canonical
 * budget shapes ($75 / $40 / $30).
 */

export interface FixturePerson {
  id: string;
  name: string;
  email: string;
  role: "admin" | "operator" | "member" | "viewer";
}

export const people: FixturePerson[] = [
  { id: "user-jdoe", name: "J. Doe", email: "j.doe@example.com", role: "admin" },
  { id: "user-rroe", name: "R. Roe", email: "r.roe@example.com", role: "operator" },
  { id: "user-msmith", name: "M. Smith", email: "m.smith@example.com", role: "member" },
  { id: "user-apoe", name: "A. Poe", email: "a.poe@example.com", role: "viewer" },
];

export interface FixtureRoutine {
  id: string;
  name: string;
  /** Plain descriptor shown in lists: no jargon, no ids. */
  descriptor: string;
  systems: string[];
  budgetCapUsd: number;
  planSteps: string[];
}

export const routines: FixtureRoutine[] = [
  {
    id: "routine-access-review",
    name: "Quarterly user access review",
    descriptor: "Looks up people and their access, reconciles differences, and chases sign-offs.",
    systems: ["identity_provider", "hris", "document_store", "messaging"],
    budgetCapUsd: 75,
    planSteps: [
      "Pull the current list of people and their access",
      "Compare against the HR system and note differences",
      "Write an exception memo for each difference",
      "Chase anyone who has not responded",
      "Assemble the final review packet",
    ],
  },
  {
    id: "routine-vendor-check",
    name: "Vendor due-diligence document refresh",
    descriptor: "Collects updated documents from vendors and files them where they belong.",
    systems: ["crm", "document_store", "messaging"],
    budgetCapUsd: 40,
    planSteps: [
      "List vendors whose documents are due for refresh",
      "Request updated documents from each vendor",
      "Check received documents for completeness",
      "File documents and update vendor records",
    ],
  },
  {
    id: "routine-attestation-chase",
    name: "Policy attestation chase",
    descriptor: "Reminds people to confirm they have read the policies, and keeps score.",
    systems: ["messaging", "document_store"],
    budgetCapUsd: 30,
    planSteps: [
      "List people with outstanding attestations",
      "Send a reminder to each person",
      "Record confirmations as they arrive",
      "Summarize who is still outstanding",
    ],
  },
];

/** Canonical budget shapes: cap / spent / reserved. */
export const budgets = {
  comfortable: { cap_usd: "75", spent_usd: "12.40", reserved_usd: "6.00" },
  warning: { cap_usd: "40", spent_usd: "33.10", reserved_usd: "4.50" },
  breached: { cap_usd: "30", spent_usd: "30.00", reserved_usd: "0" },
} as const;
