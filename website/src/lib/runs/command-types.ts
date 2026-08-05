/**
 * Result and handler shapes for the run command actions (W2). They live
 * apart from `commands.ts` because a "use server" module may only export
 * async functions; client components import these types and receive the
 * action references themselves as props from server pages.
 *
 * The discipline the shapes encode (CLAUDE.md rule 7): every write is a
 * 202 whose truth arrives via events; a conflict is shown, never retried;
 * a 404 is a 404. `refused` carries the server-side permission or
 * validation verdict as a plain sentence ready to render.
 */

export type CommandResult =
  | { kind: "accepted" }
  | { kind: "conflict"; detail: string }
  | { kind: "notFound" }
  | { kind: "refused"; message: string };

/** Steers are the one optimistic surface: the id lets the composer say
 * "Guidance sent" immediately and match the confirming steer_received. */
export type SteerResult =
  | { kind: "accepted"; steerId: string }
  | { kind: "conflict"; detail: string }
  | { kind: "notFound" }
  | { kind: "refused"; message: string };

export type SteerMode = "note" | "redirect";

/** The full set of run commands a page can hand to its client components. */
export interface RunCommandHandlers {
  pauseRun: (runId: string) => Promise<CommandResult>;
  resumeRun: (runId: string) => Promise<CommandResult>;
  landRun: (runId: string) => Promise<CommandResult>;
  steerRun: (runId: string, mode: SteerMode, body: string) => Promise<SteerResult>;
  approvePlan: (
    runId: string,
    planVersion: number,
    approve: boolean,
    reason?: string,
  ) => Promise<CommandResult>;
  respondToGate: (
    runId: string,
    stepId: string,
    response: string,
    atVirtual?: string,
  ) => Promise<CommandResult>;
  advanceClock: (runId: string, toIso: string) => Promise<CommandResult>;
}
