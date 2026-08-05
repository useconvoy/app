/**
 * Routing resolution: team expansion, one notification per member,
 * relationship split by class, promoter targeting, prefs suppression, and
 * the no-assignment fallback. Pure resolveRecipients over in-memory
 * worlds; the DB loader is covered by the integration suite.
 */
import { describe, expect, it } from "vitest";

import { resolveRecipients, type RoutingWorld } from "@/notifier/routing";

const ROUTINE = "routine-access-review";

function world(overrides: Partial<RoutingWorld> = {}): RoutingWorld {
  return {
    assignments: [
      { routineId: ROUTINE, assigneeType: "user", assigneeId: "owner-1", relationship: "owner" },
      { routineId: ROUTINE, assigneeType: "team", assigneeId: "team-1", relationship: "approver" },
      { routineId: ROUTINE, assigneeType: "user", assigneeId: "watcher-1", relationship: "watcher" },
    ],
    teamMembers: new Map([["team-1", ["approver-1", "approver-2"]]]),
    members: [
      { userId: "owner-1", role: "member", capabilities: [], status: "active" },
      { userId: "approver-1", role: "member", capabilities: ["approver"], status: "active" },
      { userId: "approver-2", role: "member", capabilities: ["approver"], status: "active" },
      { userId: "watcher-1", role: "viewer", capabilities: [], status: "active" },
      { userId: "admin-1", role: "admin", capabilities: [], status: "active" },
      { userId: "operator-1", role: "operator", capabilities: [], status: "active" },
      { userId: "promoter-1", role: "member", capabilities: ["promoter"], status: "active" },
    ],
    prefs: [],
    ...overrides,
  };
}

describe("notifier routing", () => {
  it("expands a team assignment to every member exactly once", () => {
    const recipients = resolveRecipients(world(), "checkpoint_opened", ROUTINE);
    expect(recipients).toContain("approver-1");
    expect(recipients).toContain("approver-2");
    expect(new Set(recipients).size).toBe(recipients.length);
  });

  it("checkpoint classes go to approvers and owners, never watchers", () => {
    for (const cls of ["checkpoint_opened", "checkpoint_deadline"] as const) {
      const recipients = resolveRecipients(world(), cls, ROUTINE);
      expect([...recipients].sort()).toEqual(["approver-1", "approver-2", "owner-1"]);
    }
  });

  it("budget and run outcomes go to owners and watchers, never approvers", () => {
    for (const cls of ["budget_warning", "budget_exhausted", "run_failed", "run_landed"] as const) {
      const recipients = resolveRecipients(world(), cls, ROUTINE);
      expect([...recipients].sort()).toEqual(["owner-1", "watcher-1"]);
    }
  });

  it("a person on multiple routes gets one notification", () => {
    // owner-1 is also seated on the approver team.
    const w = world({
      teamMembers: new Map([["team-1", ["approver-1", "owner-1"]]]),
    });
    const recipients = resolveRecipients(w, "checkpoint_opened", ROUTINE);
    expect(recipients.filter((id) => id === "owner-1")).toHaveLength(1);
  });

  it("promotion_requested targets promoter capability plus operators", () => {
    const recipients = resolveRecipients(world(), "promotion_requested", ROUTINE);
    expect([...recipients].sort()).toEqual(["operator-1", "promoter-1"]);
  });

  it("prefs suppress a class without in_app; absent rows default on", () => {
    const w = world({
      prefs: [
        { userId: "watcher-1", notificationClass: "run_landed", channels: [] },
        { userId: "owner-1", notificationClass: "run_failed", channels: ["in_app"] },
      ],
    });
    expect(resolveRecipients(w, "run_landed", ROUTINE)).toEqual(["owner-1"]);
    // The suppression is class-scoped: watcher-1 still hears about failures.
    expect([...resolveRecipients(w, "run_failed", ROUTINE)].sort()).toEqual([
      "owner-1",
      "watcher-1",
    ]);
  });

  it("unknown routine or no assignments falls back to admins and operators", () => {
    expect([...resolveRecipients(world(), "checkpoint_opened", undefined)].sort()).toEqual([
      "admin-1",
      "operator-1",
    ]);
    const bare = world({ assignments: [] });
    expect([...resolveRecipients(bare, "run_failed", ROUTINE)].sort()).toEqual([
      "admin-1",
      "operator-1",
    ]);
  });

  it("inactive members never receive notifications", () => {
    const w = world({
      members: world().members.map((member) =>
        member.userId === "approver-2" ? { ...member, status: "suspended" } : member,
      ),
    });
    const recipients = resolveRecipients(w, "checkpoint_opened", ROUTINE);
    expect(recipients).not.toContain("approver-2");
    expect(recipients).toContain("approver-1");
  });
});
