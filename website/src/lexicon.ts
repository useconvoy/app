/**
 * The lexicon: the only door between internal vocabulary and rendered copy.
 *
 * Code, APIs, the database, and events keep internal names (agent, sandbox,
 * gate, tenant, artifact, eval). Users see the product vocabulary. Every
 * user-facing string that references a domain concept flows through this
 * module; `npm run check:lexicon` fails the build when an internal noun or
 * an em dash reaches rendered output directly.
 *
 * Copy rules bound to every entry: sentence case, no em dashes, friendly
 * dates via `lib/format`, monospace reserved for facts from the log.
 */

/** Internal noun -> user-facing term (DESIGN §1). */
export const terms = {
  agent: "routine",
  environment: "workspace",
  sandbox: "rehearsal",
  sandboxBinding: "rehearsal copy",
  connector: "system",
  connection: "system",
  capabilityMock: "stand-in",
  eval: "routine evaluation",
  evalScore: "test score",
  telemetry: "logs",
  artifact: "file",
  artifacts: "files",
  tenant: "organization",
  gate: "checkpoint",
} as const;

export type InternalTerm = keyof typeof terms;

/** Run statuses as users see them. Keys are the runtime's status values. */
export const runStatusLabels: Record<string, string> = {
  planning: "Planning",
  awaiting_approval: "Waiting for approval",
  running: "Running",
  paused: "Paused",
  blocked_on_human: "Held for you",
  landing: "Landing",
  landed: "Landed",
  completed: "Landed",
  failed: "Failed",
  budget_exhausted: "Out of budget",
};

/** Step statuses inside the plan board and route motif. */
export const stepStatusLabels: Record<string, string> = {
  pending: "Queued",
  queued: "Queued",
  running: "In progress",
  active: "In progress",
  done: "Done",
  failed: "Failed",
  skipped: "Skipped",
  blocked_on_human: "Held",
};

/**
 * The three stuck states and their typed verbs (DESIGN §5 law 1). There is
 * exactly one verb per state and no generic "resolve" anywhere.
 */
export const checkpointKinds = {
  paused: {
    label: "Paused",
    verb: "Resume",
    description: "This run is paused and waiting for someone to resume it.",
  },
  awaiting_approval: {
    label: "Plan approval",
    verb: "Approve plan",
    description: "This run has a plan waiting for review before it starts work.",
  },
  blocked_on_human: {
    label: "Question",
    verb: "Respond",
    description: "This run asked a question and is holding until someone answers.",
  },
} as const;

export type CheckpointKind = keyof typeof checkpointKinds;

/**
 * Gate timeout behaviors as plain sentences. Keys are the runtime's
 * on_timeout values; every checkpoint card states what happens if nobody
 * answers in time.
 */
export const timeoutBehaviorLabels = {
  pause: "If no one answers in time, the run pauses.",
  skip: "If no one answers in time, this step is skipped.",
  fail: "If no one answers in time, the run fails.",
} as const;

export type TimeoutBehavior = keyof typeof timeoutBehaviorLabels;

/**
 * Event catalog -> plain-language timeline copy. Keys are the runtime's
 * RunEventType values; every event renders through here, never raw.
 */
export const eventLabels: Record<string, string> = {
  run_started: "Run started",
  plan_created: "Plan drafted",
  revision_applied: "Plan updated",
  revision_approved: "Plan approved",
  revision_rejected: "Plan sent back",
  step_started: "Step started",
  step_done: "Step finished",
  step_failed: "Step failed",
  step_skipped: "Step skipped",
  gate_opened: "Held for a person",
  gate_answered: "Answered",
  gate_timed_out: "Checkpoint timed out",
  steer_received: "Guidance received",
  paused: "Paused",
  resumed: "Resumed",
  budget_warning: "Approaching budget",
  budget_exhausted: "Out of budget",
  child_spawned: "Helper run started",
  child_landed: "Helper run finished",
  compaction_applied: "Notes tidied",
  landing_started: "Wrapping up",
  run_completed: "Landed",
  run_failed: "Failed",
};

/** Notification classes -> bell panel copy (DESIGN §4). */
export const notificationClassLabels: Record<string, string> = {
  checkpoint_opened: "Held for you",
  checkpoint_deadline: "Deadline approaching",
  promotion_requested: "Promotion to review",
  budget_warning: "Approaching budget",
  budget_exhausted: "Out of budget",
  run_failed: "Run failed",
  run_landed: "Run landed",
  improvement_ready: "Improvement ready to review",
};

/** System grant scopes as users see them. */
export const grantLabels: Record<string, string> = {
  read: "View only",
  write: "Can update",
};

/** Trigger kinds (DESIGN D1) as plain phrases on routine surfaces. */
export const triggerKindLabels = {
  schedule: "On a schedule",
  manual: "On demand",
  event: "When something happens",
} as const;

export type TriggerKind = keyof typeof triggerKindLabels;

/** Workspace clock modes and what they mean for the people watching. */
export const clockModeLabels = {
  wall: "Wall clock",
  virtual: "Virtual time",
} as const;

export const clockModeDescriptions = {
  wall: "Runs here use real time.",
  virtual: "Rehearsal runs here can move the clock forward without waiting.",
} as const;

export type ClockMode = keyof typeof clockModeLabels;

/** Runs list filter chips (DESIGN §5): All plus the four status groups. */
export const runFilterLabels = {
  all: "All",
  held: "Held",
  running: "Running",
  failed: "Failed",
  landed: "Landed",
} as const;

/** Shared copy fragments reused across surfaces. */
export const copy = {
  rehearsalBanner: "You are looking at a rehearsal. Nothing here touches live systems.",
  exitRehearsal: "Exit rehearsal",
  allQuiet: "All quiet. Nothing needs you right now.",
  openCheckpoints: "Open checkpoints",
  heldForYourTeam: "Held for your team",
  planMoved: "This plan has a newer version",
  reviewNewerPlan: (version: number) => `Review v${version}`,
  disconnected: "Connection lost. Reconnecting",
  lastEventAt: (time: string) => `Last update ${time}`,
  standInOutbox: "What this routine would have done",
  promoteAction: "Promote",
  sendBackAction: "Send back",
  exportEvidenceBinder: "Export evidence binder",
  setAside: "set aside",
  howItFitsTitle: "How it fits together",
  howItFits:
    "A routine is a job that runs on a schedule, on demand, or when something happens. It works inside a workspace, through the systems connected there, and holds at a checkpoint when it needs a person.",
  answeredBy: (name: string) => `Answered by ${name}`,
  standInFor: (system: string) => `Stand-in for ${system}`,
  actsOnVersion: (version: number) => `Acts on v${version}`,
  startRehearsalRun: "Start a rehearsal run",
  searchRuns: "Search runs",
  productionFilter: "Production",
  rehearsalsFilter: "Rehearsals",
  runsEmptyBody: "Runs appear here as soon as a routine starts working.",
  helperRuns: "Helper runs",
  operatorDetails: "Details for operators",
  copyRunId: "Copy run id",
  pinnedVersion: (version: number) => `Pinned to v${version}`,
  runNow: "Run now",
  runNowRehearsalNote:
    "Run now starts a rehearsal in this workspace's rehearsal copy. Nothing touches live systems.",
  runNowProductionNote: "Run now starts a live run in this workspace, within this routine's budget.",
  noRunsYet: "No runs yet",
  rehearsalCopyAutoNote: "A rehearsal copy is created automatically.",
  rehearsalCopyExplainer:
    "Every workspace carries a rehearsal copy. Rehearsal runs use stand-ins for anything that would touch the outside world.",
  standInRequired: (system: string) =>
    `${system} makes changes outside this organization. Add a stand-in before creating the workspace.`,
  upToPerRun: (amount: string) => `Up to ${amount} per run`,
  workspaceCardFact: (systems: number, routines: number) =>
    `Connects ${systems} ${systems === 1 ? "system" : "systems"} · used by ${routines} ${
      routines === 1 ? "routine" : "routines"
    }`,
} as const;

/** Format a checkpoint age for cards and lists, e.g. "HELD 26M". */
export function heldAgeLabel(minutes: number): string {
  if (minutes < 60) return `HELD ${Math.max(minutes, 0)}M`;
  if (minutes < 60 * 24) return `HELD ${Math.floor(minutes / 60)}H`;
  return `HELD ${Math.floor(minutes / (60 * 24))}D`;
}

/**
 * Notification titles (DESIGN §4). The notifier is the only caller. Each
 * title is a short plain sentence; when the event carries the run's goal or
 * the checkpoint's own prompt (both already written in plain language) the
 * title leads with it.
 */
export const notificationTitles = {
  respond: (prompt?: string | null) =>
    prompt && prompt.trim().length > 0
      ? prompt.trim()
      : "A run asked a question and is waiting for an answer.",
  approve: (goal?: string | null) =>
    goal ? `The plan for "${goal}" is ready to review.` : "A plan is ready to review.",
  resume: () => "A run is paused and waiting for someone to resume it.",
  deadline: () => "A checkpoint is coming up on its deadline.",
  promotionRequested: () => "A routine finished rehearsing and is ready to review for promotion.",
  budgetWarning: (goal?: string | null) =>
    goal ? `"${goal}" is approaching its budget.` : "A run is approaching its budget.",
  budgetExhausted: (goal?: string | null) =>
    goal ? `"${goal}" ran out of budget.` : "A run ran out of budget.",
  runFailed: (goal?: string | null) => (goal ? `"${goal}" failed.` : "A run failed."),
  runLanded: (goal?: string | null) => (goal ? `"${goal}" landed.` : "A run landed."),
  improvementReady: () => "An improvement is ready to review.",
} as const;

/** Bell panel grouping: which classes read as held vs alert vs update. */
export const notificationClassGroups: Record<string, "held" | "alert" | "update"> = {
  checkpoint_opened: "held",
  checkpoint_deadline: "held",
  promotion_requested: "held",
  budget_warning: "alert",
  budget_exhausted: "alert",
  run_failed: "alert",
  run_landed: "update",
  improvement_ready: "update",
};

/** "3 held · 1 alert" style summary for the bell panel header. */
export function bellSummary(counts: { held: number; alert: number; update: number }): string {
  const parts: string[] = [];
  if (counts.held > 0) parts.push(`${counts.held} held`);
  if (counts.alert > 0) parts.push(counts.alert === 1 ? "1 alert" : `${counts.alert} alerts`);
  if (counts.update > 0) parts.push(counts.update === 1 ? "1 update" : `${counts.update} updates`);
  return parts.join(" · ");
}

/** Copy for the notification settings page (in-app only in v1, D6). */
export const notificationSettingsCopy = {
  title: "Notifications",
  intro: "Choose which updates reach you in the app. Each one links straight to the action it needs.",
  inAppLabel: "In app",
  otherChannels: "Email and Slack are coming later.",
  save: "Save preferences",
} as const;
