/**
 * Table-driven mirror of the intended permissions matrix. Each row below
 * transcribes one line of the role/permission table (true = allowed,
 * false = denied, "cap" = grantable capability), so an accidental drift
 * in permissions.ts fails here, not in production.
 */
import { describe, expect, it } from "vitest";

import { can, type Action, type Capability, type Role } from "@/lib/permissions";

type Cell = boolean | "cap";

interface MatrixRow {
  designRow: string;
  action: Action;
  admin: Cell;
  operator: Cell;
  member: Cell;
  viewer: Cell;
  /** The capability that satisfies this row's "cap" cells, if any. */
  capability?: Capability;
}

const DESIGN_MATRIX: MatrixRow[] = [
  {
    designRow: "View runs, routines, evaluations, logs, changelog",
    action: "view_runs",
    admin: true,
    operator: true,
    member: true,
    viewer: true,
  },
  {
    designRow: "Export evidence binder",
    action: "export_evidence",
    admin: true,
    operator: true,
    member: true,
    viewer: true,
  },
  {
    designRow: "Answer checkpoints assigned to them / their team",
    action: "answer_checkpoints",
    admin: true,
    operator: true,
    member: true,
    viewer: false,
  },
  {
    designRow: "Create/edit routines in rehearsal; rehearse",
    action: "edit_routines_rehearsal",
    admin: true,
    operator: true,
    member: true,
    viewer: false,
  },
  {
    designRow: "Trigger on-demand production run of an assigned routine (within caps)",
    action: "trigger_production_run",
    admin: true,
    operator: true,
    member: true,
    viewer: false,
  },
  {
    designRow: "Give feedback; review improvements queue",
    action: "give_feedback",
    admin: true,
    operator: true,
    member: true,
    viewer: false,
  },
  {
    designRow: "Submit routine for promotion",
    action: "submit_promotion",
    admin: true,
    operator: true,
    member: true,
    viewer: false,
  },
  {
    designRow: "Promote rehearsal -> production; edit triggers of live routines",
    action: "promote",
    admin: "cap",
    operator: true,
    member: "cap",
    viewer: false,
    capability: "promoter",
  },
  {
    designRow: "Manage workspaces and systems",
    action: "manage_workspaces",
    admin: true,
    operator: true,
    member: false,
    viewer: false,
  },
  {
    designRow: "Ship improvements (approve learning changes)",
    action: "ship_improvements",
    admin: true,
    operator: true,
    member: "cap",
    viewer: false,
    capability: "ship_improvements",
  },
  {
    designRow: "Manage members, teams, invites, SSO",
    action: "manage_members",
    admin: true,
    operator: false,
    member: false,
    viewer: false,
  },
  {
    designRow: "Org policies, budgets defaults, billing, org audit log",
    action: "manage_org_settings",
    admin: true,
    operator: false,
    member: false,
    viewer: false,
  },
  {
    designRow: "Publish to catalog (workshop org)",
    action: "publish_catalog",
    admin: true,
    operator: true,
    member: false,
    viewer: false,
  },
];

const ROLES: Role[] = ["admin", "operator", "member", "viewer"];
const ALL_CAPABILITIES: Capability[] = ["approver", "promoter", "ship_improvements"];

describe("permissions matrix matches the intended role table", () => {
  it("covers every action exactly once", () => {
    const actions = DESIGN_MATRIX.map((row) => row.action);
    expect(new Set(actions).size).toBe(actions.length);
    expect(actions).toHaveLength(13);
  });

  for (const row of DESIGN_MATRIX) {
    describe(row.designRow, () => {
      for (const role of ROLES) {
        const cell = row[role];
        if (cell === true) {
          it(`${role}: allowed without capabilities`, () => {
            expect(can(row.action, role)).toBe(true);
          });
        } else if (cell === false) {
          it(`${role}: denied even with every capability`, () => {
            expect(can(row.action, role)).toBe(false);
            expect(can(row.action, role, ALL_CAPABILITIES)).toBe(false);
          });
        } else {
          const capability = row.capability;
          it(`${role}: denied without the grant`, () => {
            expect(can(row.action, role)).toBe(false);
          });
          it(`${role}: allowed with the ${capability} capability`, () => {
            expect(can(row.action, role, [capability as Capability])).toBe(true);
          });
          it(`${role}: other capabilities do not satisfy the cell`, () => {
            const others = ALL_CAPABILITIES.filter((value) => value !== capability);
            expect(can(row.action, role, others)).toBe(false);
          });
        }
      }
    });
  }

  it("approver is a routing capability, never a permission escalation", () => {
    for (const row of DESIGN_MATRIX) {
      for (const role of ROLES) {
        if (row[role] === false) {
          expect(can(row.action, role, ["approver"])).toBe(false);
        }
      }
    }
  });
});
