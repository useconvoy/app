/**
 * The permissions matrix, enforced server-side on every route
 * handler and server action. The UI lens system hides unauthorized areas
 * but is never the enforcement point.
 *
 * Some cells are capability grants carried on the
 * membership: `approver` (checkpoint routing target) and `promoter`
 * (rehearsal -> production promotion; Operators hold it implicitly).
 */

export type Role = "admin" | "operator" | "member" | "viewer";
export type Capability = "approver" | "promoter" | "ship_improvements";

export type Action =
  | "view_runs"
  | "export_evidence"
  | "answer_checkpoints"
  | "edit_routines_rehearsal"
  | "trigger_production_run"
  | "give_feedback"
  | "submit_promotion"
  | "promote"
  | "manage_workspaces"
  | "ship_improvements"
  | "manage_members"
  | "manage_org_settings"
  | "publish_catalog";

type Grant = boolean | "cap";

const MATRIX: Record<Action, Record<Role, Grant>> = {
  view_runs: { admin: true, operator: true, member: true, viewer: true },
  export_evidence: { admin: true, operator: true, member: true, viewer: true },
  answer_checkpoints: { admin: true, operator: true, member: true, viewer: false },
  edit_routines_rehearsal: { admin: true, operator: true, member: true, viewer: false },
  trigger_production_run: { admin: true, operator: true, member: true, viewer: false },
  give_feedback: { admin: true, operator: true, member: true, viewer: false },
  submit_promotion: { admin: true, operator: true, member: true, viewer: false },
  promote: { admin: "cap", operator: true, member: "cap", viewer: false },
  manage_workspaces: { admin: true, operator: true, member: false, viewer: false },
  ship_improvements: { admin: true, operator: true, member: "cap", viewer: false },
  manage_members: { admin: true, operator: false, member: false, viewer: false },
  manage_org_settings: { admin: true, operator: false, member: false, viewer: false },
  publish_catalog: { admin: true, operator: true, member: false, viewer: false },
};

/** Capability that satisfies a `cap` cell, per action. */
const CAP_FOR_ACTION: Partial<Record<Action, Capability>> = {
  promote: "promoter",
  ship_improvements: "ship_improvements",
};

export function can(action: Action, role: Role, capabilities: readonly string[] = []): boolean {
  const grant = MATRIX[action][role];
  if (grant === true) return true;
  if (grant === false) return false;
  const required = CAP_FOR_ACTION[action];
  return required !== undefined && capabilities.includes(required);
}

/** Nav lens groups per role: which portal areas render at all. */
export function visibleAreas(role: Role): string[] {
  const base = ["overview", "runs", "evaluation", "learning"];
  if (role === "viewer") return base;
  const member = [...base, "checkpoints", "workspaces", "logs"];
  if (role === "member" || role === "operator") return member;
  return [...member, "admin"];
}
