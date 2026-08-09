import { describe, expect, it } from "vitest";

import {
  coversSystems,
  firstCoveringWorkspace,
  missingSystems,
  selectWorkspace,
  systemDisplayNames,
} from "@/lib/workspaces/fit";

const ws = (id: string, systems: string[]) => ({
  id,
  systems: systems.map((systemId) => ({ systemId, displayName: systemId.replace("_", " ") })),
});

describe("workspace fit", () => {
  it("reports missing systems in required order", () => {
    const workspace = ws("a", ["document_store"]);
    expect(missingSystems(["messaging", "document_store", "crm"], workspace)).toEqual([
      "messaging",
      "crm",
    ]);
    expect(coversSystems(["document_store"], workspace)).toBe(true);
  });

  it("auto-matches the first covering workspace", () => {
    const partial = ws("partial", ["messaging"]);
    const full = ws("full", ["messaging", "document_store"]);
    const later = ws("later", ["messaging", "document_store"]);
    expect(
      firstCoveringWorkspace(["messaging", "document_store"], [partial, full, later])?.id,
    ).toBe("full");
    expect(firstCoveringWorkspace(["crm"], [partial, full])).toBeNull();
  });

  it("prefers a compatible explicit assignment over the auto-match", () => {
    const first = ws("first", ["messaging"]);
    const assigned = ws("assigned", ["messaging"]);
    const selection = selectWorkspace(
      { systems: ["messaging"], workspaceId: "assigned" },
      [first, assigned],
    );
    expect(selection.workspace?.id).toBe("assigned");
    expect(selection.staleAssignment).toBeNull();
  });

  it("falls back with a stale report when the assignment stops covering", () => {
    const assigned = ws("assigned", ["messaging"]);
    const covering = ws("covering", ["messaging", "document_store"]);
    const selection = selectWorkspace(
      { systems: ["messaging", "document_store"], workspaceId: "assigned" },
      [assigned, covering],
    );
    expect(selection.workspace?.id).toBe("covering");
    expect(selection.staleAssignment?.workspace.id).toBe("assigned");
    expect(selection.staleAssignment?.missingSystems).toEqual(["document_store"]);
  });

  it("treats a vanished assignment as unassigned, without a stale report", () => {
    const covering = ws("covering", ["messaging"]);
    const selection = selectWorkspace(
      { systems: ["messaging"], workspaceId: "gone" },
      [covering],
    );
    expect(selection.workspace?.id).toBe("covering");
    expect(selection.staleAssignment).toBeNull();
  });

  it("names systems from the catalog and the workspaces' own grants", () => {
    const names = systemDisplayNames([
      { systems: [{ systemId: "custom_crm", displayName: "Internal CRM" }] },
    ]);
    expect(names.custom_crm).toBe("Internal CRM");
    expect(names.document_store).toBeTruthy();
  });
});
