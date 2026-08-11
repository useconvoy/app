/**
 * The lexicon: the only door between internal vocabulary and rendered copy.
 *
 * Code, APIs, the database, and events keep internal names (sandbox,
 * gate, tenant, artifact, eval). Users see the product vocabulary. Every
 * user-facing string that references a domain concept flows through this
 * module; `npm run check:lexicon` fails the build when an internal noun or
 * an em dash reaches rendered output directly.
 *
 * Copy rules bound to every entry: sentence case, no em dashes, friendly
 * dates via `lib/format`, monospace reserved for facts from the log.
 */

/** Internal noun -> user-facing term. */
export const terms = {
  agent: "agent",
  environment: "runtime binding",
  sandbox: "rehearsal",
  sandboxBinding: "rehearsal copy",
  connector: "system",
  connection: "system",
  capabilityMock: "stand-in",
  eval: "agent evaluation",
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
 * The three stuck states and their typed verbs. There is
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
  /**
   * The promotion review is the fourth inbox item kind: not a stuck
   * run but a rehearsal result waiting for a person before anything goes
   * live. It keeps the same one-verb discipline as the other three.
   */
  promotion: {
    label: "Promotion review",
    verb: "Review",
    description: "Look over rehearsal results before anything goes live.",
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
  simulated_effect: "Simulated connector effect",
  environment_provisioned: "Compute started",
  environment_checkpointed: "Agent state saved",
  environment_hibernated: "Compute released for pause",
  environment_restored: "Agent state restored",
  environment_terminated: "Compute released",
  landing_started: "Wrapping up",
  run_completed: "Landed",
  run_failed: "Failed",
};

/** Notification classes -> bell panel copy. */
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

/** Trigger kinds as plain phrases on routine surfaces. */
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

/** Membership roles as users see them. Keys are the memberships values. */
export const roleLabels: Record<string, string> = {
  admin: "Admin",
  operator: "Operator",
  member: "Member",
  viewer: "Viewer",
};

/** Runs list filter chips: All plus the four status groups. */
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
  standInOutbox: "What this agent would have done",
  promoteAction: "Promote",
  sendBackAction: "Send back",
  exportEvidenceBinder: "Export evidence binder",
  setAside: "set aside",
  howItFitsTitle: "How it fits together",
  howItFits:
    "An agent owns a goal and runtime setup. It operates through a shared workspace's connected systems, starts on demand or on a schedule, and holds at a checkpoint when it needs a person.",
  answeredBy: (name: string) => `Answered by ${name}`,
  standInFor: (system: string) => `Stand-in for ${system}`,
  actsOnVersion: (version: number) => `Acts on v${version}`,
  startRehearsalRun: "Start a rehearsal run",
  searchRuns: "Search runs",
  productionFilter: "Production",
  rehearsalsFilter: "Rehearsals",
  runsEmptyBody: "Runs appear here as soon as an agent starts working.",
  myRunsTitle: "My runs",
  myRunsIntro: "Runs you started, newest first.",
  myRunsEmptyBody: "Runs you start will appear here.",
  showAllRuns: "Show all runs",
  startRun: "Start a run",
  helperRuns: "Helper runs",
  operatorDetails: "Details for operators",
  copyRunId: "Copy run id",
  pinnedVersion: (version: number) => `Pinned to v${version}`,
  runNow: "Run now",
  runNowRehearsalNote:
    "A rehearsal starts this agent against the workspace's safe data plane, without changing live systems.",
  runNowProductionNote:
    "A live run starts this agent against the workspace's real connected systems, within its budget.",
  noRunsYet: "No runs yet",
  routinesEmptyTitle: "No agents yet",
  routinesEmptyBody:
    "Agents are the durable workers this organization configures. Create one to get started.",
  planEmptyNote: "No instructions were pinned. The agent can form a live plan when it starts.",
  checkpointsUnknownNote:
    "Checkpoints appear here when this agent's plan holds for a person.",
  workspacesEmptyTitle: "No workspaces yet",
  workspacesEmptyBody:
    "A workspace connects shared systems and spaces. Create one, then link as many agents to it as needed.",
  rehearsalCopyAutoNote:
    "Next, create an Agent that uses this shared Workspace. Rehearsals isolate writes; live runs use its real integrations.",
  rehearsalCopyExplainer:
    "Every agent can rehearse through its workspace. Rehearsal runs use stand-ins for anything that would touch the outside world.",
  upToPerRun: (amount: string) => `Up to ${amount} per run`,
  workspaceCardFact: (systems: number, agents: number) =>
    `Connects ${systems} ${systems === 1 ? "system" : "systems"} · used by ${agents} ${
      agents === 1 ? "agent" : "agents"
    }`,
  // Run controls: pause/resume/land, the steer composer, clock.
  pauseAction: "Pause",
  landAction: "Land",
  steerNote: "Note",
  steerRedirect: "Redirect",
  steerExplainer: "A redirect may propose a plan revision.",
  steerBodyLabel: "Guidance for this run",
  sendGuidance: "Send guidance",
  guidanceSent: "Guidance sent",
  advanceClock: "Advance clock",
  advanceClockLabel: "Move rehearsal time to",
  plusOneDay: "+1 day",
  plusOneWeek: "+1 week",
  answerAtVirtual: "Answer at a moment in rehearsal time",
  answerAtVirtualLabel: "Rehearsal moment",
  responseLabel: "Your answer",
  rejectReasonLabel: "What should change",
  rejectReasonRequired: "Say what should change before sending a plan back.",
  // Checkpoints inbox: triage keys, conflict flips, sent state.
  triageKeysHint: "Keys: j next · k previous · A approve · E edit · R reject",
  reviewNewerOnRun: "Review the newer version",
  checkpointsIntro: "Everything waiting on a person, each with its one action.",
  actionSent: "Sent. This clears once the run confirms.",
  // Land report + promotion review.
  landReportTitle: "Land report",
  outcomeTitle: "Outcome",
  outcomeLanded: (goal: string) => `Finished: ${goal}`,
  outcomeNotLanded: (goal: string) => `Stopped before finishing: ${goal}`,
  stepsSummary: (done: number, failed: number, skipped: number) =>
    `${done} done · ${failed} failed · ${skipped} skipped`,
  exceptionsTitle: "Exceptions",
  noExceptions: "No exceptions",
  filesTitle: "Files",
  spendVsCapTitle: "Spend",
  outboxWhat: "What",
  outboxWhere: "To whom or where",
  outboxContent: "What would have gone out",
  outboxDerivedNote:
    "Recorded by the rehearsal stand-ins; older runs are reconstructed from their steps and files.",
  outboxContentHeld: "Content held by the platform",
  outboxKeptWithFiles: "Kept with the run's files",
  outboxOutsideWorld: "Outside this organization, held by the stand-in",
  submitForPromotion: "Submit for promotion",
  promotionRequestedNote: "Promotion requested. A promoter will look it over.",
  promotionReviewTitle: "Promotion review",
  promotionNeedsPromoter: "Promoting needs a promoter.",
  sendBackNoteLabel: "What should change before this goes live",
  sendBackNoteRequired: "Say what should change before sending this back.",
  notTiedToRoutine: "This run is not tied to an agent, so there is nothing to promote.",
  viewersCannotAct: "Viewers cannot act on runs.",
  assignedToSomeoneElse: "This run's agent is assigned to someone else.",
  rehearsalOnlyAnswerAt: "Scheduled answers only work on rehearsal runs.",
  rehearsalOnlyPromotion: "Only rehearsal results can be submitted for promotion.",
  platformRefused: "The platform did not accept this action.",
} as const;

/** Promotion request lifecycle as users see it. */
export const promotionStatusLabels = {
  requested: "Waiting for review",
  promoted: "Promoted",
  sent_back: "Sent back",
} as const;

export type PromotionStatus = keyof typeof promotionStatusLabels;

/**
 * Live deadline countdown once a checkpoint is under a day out, e.g.
 * "due in 3h 12m". The inbox re-renders it each minute.
 */
export function dueInLabel(minutesLeft: number): string {
  if (minutesLeft <= 0) return "past due";
  const hours = Math.floor(minutesLeft / 60);
  const minutes = minutesLeft % 60;
  return hours > 0 ? `due in ${hours}h ${minutes}m` : `due in ${minutes}m`;
}

/** Format a checkpoint age for cards and lists, e.g. "HELD 26M". */
export function heldAgeLabel(minutes: number): string {
  if (minutes < 60) return `HELD ${Math.max(minutes, 0)}M`;
  if (minutes < 60 * 24) return `HELD ${Math.floor(minutes / 60)}H`;
  return `HELD ${Math.floor(minutes / (60 * 24))}D`;
}

/**
 * Notification titles. The notifier is the only caller. Each
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
  promotionRequested: () => "An agent finished rehearsing and is ready to review for promotion.",
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

/** Copy for the notification settings page (in-app only in v1). */
export const notificationSettingsCopy = {
  title: "Notifications",
  intro: "Choose which updates reach you in the app. Each one links straight to the action it needs.",
  inAppLabel: "In app",
  otherChannels: "Email and Slack are coming later.",
  save: "Save preferences",
} as const;

/**
 * Catalog copy. Product vocabulary only: Agent templates, systems,
 * workspaces, test scores. The stored eval_thresholds column never reaches
 * the screen by that name; users see a "test score floor".
 */
export const catalogCopy = {
  title: "Catalog",
  intro: "Agent templates published by Convoy, ready to add to an Agent in one of your workspaces.",
  installRoutine: "Install Agent template",
  publishAction: "Publish to catalog",
  publishTitle: "Publish an Agent template",
  publishIntro:
    "Publishing takes a snapshot of an Agent's goal and requirements and puts it on the storefront.",
  whatItNeeds: "What it needs",
  testScoreFloorLabel: "Test score floor",
  testScoreFloor: (score: number) => `Ships only above ${score}`,
  updateAvailable: "Update available",
  installedPinned: (version: number) => `Installed · pinned to v${version}`,
  chooseWorkspace: "Choose a workspace",
  chooseWorkspaceHint: "Pick the shared workspace this Agent should operate through.",
  compatibilityTitle: "How it fits this workspace",
  allGreen: "Everything this Agent needs is already connected.",
  connectedCheck: (system: string) => `${system} is connected`,
  needsMapping: (system: string) => `Choose which ${system} this Agent should use`,
  connectFirst: (system: string) => `Connect ${system} first`,
  portableExcept: (count: number) =>
    `Portable except ${count} vendor-specific ${count === 1 ? "tool" : "tools"}`,
  fullyPortable: "Fully portable across vendors",
  installPinned: (version: number) => `Install pinned to v${version}`,
  installBlocked: "Connect the missing systems before installing.",
  installedNote: "Installed. The Agent now carries this goal and its requirements.",
  viewInstalledRoutine: "Open the Agent",
  installNeedsWorkspace:
    "Create a shared workspace first, so Agents have connected systems to use.",
  goToWorkspaces: "Go to workspaces",
  changelogTitle: "Changelog",
  versionChip: (version: number) => `v${version}`,
  publishedBy: (publisher: string) => `Published by ${publisher}`,
  emptyTitle: "No Agent templates are available yet",
  emptyBody:
    "Agent templates published to the catalog appear here. Your Convoy contact can help publish the first one.",
} as const;

/** Billing copy; billing is invoice-first Stripe. */
export const billingCopy = {
  title: "Billing",
  intro: "Your plan, invoices, and payment details.",
  handledByConvoy: "Billing is handled by your Convoy contact.",
  managePayment: "Manage payment details",
  invoicesTitle: "Invoices",
  planLabel: "Plan",
  statusLabel: "Status",
  noPlanYet: "No plan on file yet",
  sampleInvoicesNote: "Sample invoices are shown until billing is connected.",
  noInvoicesYet: "No invoices yet",
  viewInvoice: "View invoice",
} as const;

/** Admin copy: policies, API access, org audit. */
export const adminCopy = {
  policiesTitle: "Policies",
  policiesIntro: "Budget defaults and export rules for this organization.",
  defaultRunBudgetCap: "Default budget per run",
  defaultRunBudgetCapHint: "New Agents start with this cap unless one is set for them.",
  monthlySpendNotice: "Monthly spend notice",
  monthlySpendNoticeHint: "Admins hear about it when monthly spend passes this amount.",
  viewerEvidenceExport: "Viewers can export the evidence binder",
  viewerEvidenceExportHint: "Turn this off to limit exports to members and above.",
  savePolicies: "Save policies",
  policiesSaved: "Policies saved.",
  amountInvalid: "Enter an amount above zero",
  noticeBelowCap: "The spend notice should not be below the per-run cap",
  apiAccessTitle: "API access",
  apiAccessIntro: "How this organization reaches the Convoy platform.",
  apiAccessManaged:
    "Access to the platform is managed by Convoy. Every call your organization makes travels through Convoy's bridge identity, so there are no keys to create, copy, or rotate here.",
  apiAccessNoKeys: "No key material is ever shown or stored on this site.",
  apiAccessSelfServe: "Need direct API access? Write to your Convoy contact and we will set it up with you.",
  controlPlaneUrlLabel: "Platform address",
  notificationDefaultsTitle: "Notification defaults",
  notificationDefaultsIntro:
    "Defaults for people who have not chosen their own notification preferences. Personal choices always win.",
  inAppFixedOn: "In app, always on",
  saveDefaults: "Save defaults",
  auditTitle: "Audit log",
  auditIntro: "Administrative actions taken on this site: who did what, and when.",
  auditFilterLabel: "Filter by action",
  auditFilterHint: "Start of an action name, like member or catalog",
  auditApplyFilter: "Filter",
  auditEmpty: "No entries match",
  auditFormerMember: "Former member",
  auditNewer: "Newer",
  auditOlder: "Older",
} as const;

/** Model provider display names. Keys are the code-facing provider ids. */
export const modelProviderLabels: Record<string, string> = {
  anthropic: "Anthropic",
  openai: "OpenAI",
};

/**
 * Copy for the model access admin surface, where an organization configures
 * its own model provider keys. The security model is stated in the empty
 * state on purpose: a key is verified, sealed, and can never be read back.
 * Only the last four characters are ever shown.
 */
export const modelAccessCopy = {
  title: "Model access",
  intro:
    "Set your organization's own Anthropic or OpenAI keys. When a key is set, your runs authenticate to that provider with your key.",
  emptyState:
    "No key is set. When you add one, it is verified with the provider, sealed, and stored so it can never be read back. Only the last four characters are ever shown.",
  keyFieldLabel: "API key",
  keyFieldHint: (provider: string, prefix: string) =>
    `Starts with ${prefix}. It is verified with ${provider} before it is saved.`,
  verifyAndSave: "Verify and save",
  rotate: "Rotate",
  rotateIntro:
    "Enter a new key. The key in use stays in place until the new one is verified and saved.",
  cancel: "Cancel",
  verify: "Verify",
  remove: "Remove",
  removeConfirmLabel: (provider: string) => `Type ${provider} to confirm`,
  removeConfirmHint: "Removing the key fails any run that is using it right now.",
  keyEnding: (last4: string) => `Key ending ····${last4}`,
  setByOn: (name: string, date: string) => `Set by ${name} on ${date}`,
  setOn: (date: string) => `Set on ${date}`,
  lastVerified: (date: string) => `Last verified ${date}`,
  notVerifiedYet: "Not verified yet",
  saved: (last4: string) => `Saved. Your runs now use the key ending ····${last4}.`,
  removed: "The key was removed.",
  verifiedOk: "Verified. The provider accepted this key.",
  /** The key prefix hint per provider; the value is code-facing, not a noun. */
  keyPrefix: { anthropic: "sk-ant-", openai: "sk-" } as Record<string, string>,
  /** Recorded verification outcomes, keyed by the stored status code. */
  verifyStatusLabel: {
    ok: "Verified",
    rejected: "The provider rejected this key",
    unreachable: "Could not reach the provider",
    malformed: "This does not look like a valid key",
  } as Record<string, string>,
  /** Action failure reasons, keyed by the reason code the action returns. */
  reasonMessage: {
    unknown_provider: "That is not a model provider we can store a key for.",
    malformed: "This does not look like a valid key. Check it and try again.",
    rejected: "The provider rejected this key. Check it and try again.",
    unreachable: "Could not reach the provider. Try again in a moment.",
    not_configured: "There is no key to act on.",
    confirm_mismatch: "The name did not match. Type it exactly to confirm removal.",
  } as Record<string, string>,
} as const;

/** Feedback kinds as users pick them in the composer. */
export const feedbackKindLabels = {
  rating: "Helpful",
  comment: "Comment",
  correction: "Correction",
} as const;

export type FeedbackKind = keyof typeof feedbackKindLabels;

/** Star labels for the 1..5 helpfulness rating, in rating order. */
export const ratingLabels: Record<number, string> = {
  1: "Not helpful",
  2: "Slightly helpful",
  3: "Somewhat helpful",
  4: "Helpful",
  5: "Very helpful",
};

/**
 * The learning handoff lifecycle in quiet, plain words. Keys are the
 * feedback table's learning_status values; the chip never says "queue" in
 * pipeline jargon, it says what happened to the person's words.
 */
export const learningStatusLabels = {
  new: "Noted",
  queued: "Queued for learning",
  consumed: "Applied",
} as const;

export type LearningStatus = keyof typeof learningStatusLabels;

/** Improvement lifecycle states on the learning queue. */
export const improvementStatusLabels = {
  proposed: "Proposed",
  approved: "Approved",
  shipped: "Shipped",
} as const;

export type ImprovementStatus = keyof typeof improvementStatusLabels;

/**
 * One-sentence summary of a test-score trend, the accessible text every
 * trend line carries, e.g. "Test score 91, up 19 points over 8 runs".
 */
export function testScoreSummary(latest: number, delta: number, runCount: number): string {
  if (runCount <= 1) return `Test score ${latest} from the first scored run`;
  const runs = `${runCount} runs`;
  if (delta > 0) return `Test score ${latest}, up ${delta} ${delta === 1 ? "point" : "points"} over ${runs}`;
  if (delta < 0) {
    const drop = Math.abs(delta);
    return `Test score ${latest}, down ${drop} ${drop === 1 ? "point" : "points"} over ${runs}`;
  }
  return `Test score ${latest}, steady over ${runs}`;
}

/**
 * Test-score evidence on an improvement card, e.g.
 * "Test score 84 -> 91 across 3 rehearsal runs".
 */
export function testScoreEvidence(before: number, after: number, runCount: number): string {
  return `Test score ${before} -> ${after} across ${runCount} rehearsal ${
    runCount === 1 ? "run" : "runs"
  }`;
}

/** Shared copy for the feedback composer and stream. */
export const feedbackCopy = {
  composerTitle: "Leave feedback",
  kindLegend: "What kind of feedback?",
  ratingLegend: "How helpful was this run?",
  bodyLabel: "Your feedback",
  bodyPlaceholderComment: "What should the team know about this run?",
  bodyPlaceholderCorrection: "What should have happened instead?",
  submit: "Send feedback",
  bodyRequired: "Write your feedback before sending.",
  ratingRequired: "Pick a star rating before sending.",
  noRunsYetNote: "Feedback attaches to a run. This Agent has not run yet.",
  attachesToLatestRun: "Your feedback attaches to this Agent's latest run.",
  emptyStream: "No feedback yet",
  sent: "Feedback sent",
  starsOutOfFive: (rating: number) => `${rating} of 5`,
} as const;

/** Shared copy for the Agent evaluation and learning surfaces. */
export const improveCopy = {
  evaluationTitle: "Agent evaluation",
  evaluationIntro: "How each Agent scores in rehearsal, and the runs behind the numbers.",
  evaluationEmptyTitle: "Nothing to evaluate yet",
  evaluationEmptyBody:
    "Once this organization has Agents, their test scores chart here run by run.",
  suitesTitle: "Scenario groups",
  scenarioCount: (count: number) => `${count} ${count === 1 ? "scenario" : "scenarios"}`,
  trendTitle: "Test score trend",
  noScoresYet: "No test scores yet",
  noScoresBody: "Rehearsal results will chart here once this Agent has been scored.",
  scorecardsTitle: "Run scorecards",
  trajectoriesTitle: "Recent scored runs",
  scoredRunsTitle: "Scored runs",
  scoredRunsEmptyBody: "Runs of this Agent appear here once they are scored.",
  scoreColumn: "Score",
  scoredAtColumn: "When",
  runColumn: "Run",
  learningTitle: "Learning",
  learningIntro: "What the Agents have learned, waiting for a person to approve it.",
  queueTitle: "Improvements to review",
  queueEmpty: "Nothing to review right now",
  queueEmptyBody: "Improvements appear here when an Agent has something worth changing.",
  approveAction: "Approve",
  shipAction: "Ship",
  changelogTitle: "Agent changelog",
  changelogEmpty: "No changes yet",
  changelogEmptyBody: "Changes shipped to this Agent will appear here.",
  changelogNoRoutines: "Changes appear here once this organization has Agents.",
  changelogInstalledNote: "Installed from the catalog",
  changelogCreatedNote: "Added to this organization",
  feedbackReviewTitle: "Recent feedback",
  readOnlyQueueNote: "You can read this queue. Approving and shipping need an operator.",
} as const;

/** Copy for the start-a-run picker. */
export const startRunCopy = {
  title: "Start a run",
  intro: "Pick an Agent and start it now.",
  pickRoutine: "Choose an Agent",
  planTitle: "What it will do",
  noRoutinesTitle: "Nothing to run yet",
  noRoutinesBody: "This organization has no configured Agents. Create one first.",
  browseCatalog: "Browse the catalog",
  startRehearsal: "Start a rehearsal run",
  startProduction: "Start a live run",
  noWorkspaceNote:
    "This Agent's shared workspace is missing a required system, so it cannot run yet.",
  viewersCannotStart: "Viewers cannot start runs.",
} as const;

/** Copy for the account surfaces and the account menu. */
export const accountCopy = {
  menuLabel: "Account menu",
  profileItem: "Profile",
  organizationItem: "Organization",
  myRunsItem: "My runs",
  settingsItem: "Settings",
  signOutItem: "Sign out",
  profileTitle: "Account",
  profileIntro: "Who you are on this site, and the organizations you belong to.",
  nameLabel: "Name",
  emailLabel: "Email",
  signInLabel: "Sign-in",
  ssoLinked: "Connected to single sign-on",
  ssoNotLinked: "Signed in with email",
  membershipsTitle: "Your organizations",
  currentOrgChip: "Current",
  organizationTitle: "Organization",
  organizationIntro: "The organization you are working in right now.",
  yourRoleLabel: "Your role",
  memberCountLabel: "Members",
  createdLabel: "Created",
  peopleCount: (count: number) => `${count} ${count === 1 ? "person" : "people"}`,
  adminLink: "Open the admin area",
  settingsTitle: "Settings",
  notificationSettingsLink: "Notification preferences",
} as const;

/** Copy for the portal's own page-not-found state. */
export const portalNotFoundCopy = {
  title: "This page does not exist",
  body: "The address may be old or mistyped. Nothing ran and nothing was recorded.",
  backToOverview: "Back to Overview",
} as const;

/** Shared copy for the logs surface. */
export const logsCopy = {
  title: "Logs",
  intro: "Every event from this organization's runs, newest first, with spend alongside.",
  emptyTitle: "No events yet",
  emptyBody: "Events appear here as soon as an Agent starts working.",
  filterEventType: "Event type",
  filterRun: "Run",
  filterLens: "Show",
  allEvents: "All events",
  allRuns: "All runs",
  allLenses: "Everything",
  apply: "Apply filters",
  timeColumn: "Time",
  eventColumn: "What happened",
  actorColumn: "Who",
  runColumn: "Run",
  payloadSummary: "Details",
  spendTitle: "Spend",
  totalSpend: "Total spend across these runs",
  spendByStep: "Spend by step",
} as const;
