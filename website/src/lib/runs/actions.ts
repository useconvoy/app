/**
 * Server actions for run surfaces. W1 carries one seam: starting a fixture
 * routine's rehearsal run on demand, for demos and E2E. This is the D13
 * on-demand trigger in embryo. TODO(website-W4): fold this into the
 * Routines surface's triggers (runRoutineNow) and drop the direct seam.
 */
"use server";

import { revalidatePath } from "next/cache";

import { createRun } from "@/lib/api/runs";
import { routines } from "@/lib/fixtures/world";
import { can } from "@/lib/permissions";
import { recordRunTarget } from "@/lib/routines/data";
import { requireRunContext } from "./context";

/**
 * Start a rehearsal run of a fixture routine in the sandbox-bound local
 * workspace. Role-checked server-side; truth arrives via re-fetch and the
 * event stream, never an optimistic flip (CLAUDE.md rule 7).
 */
export async function startFixtureRun(routineId: string): Promise<{ runId: string }> {
  const { membership, actor } = await requireRunContext();
  if (!can("trigger_production_run", membership.role, membership.capabilities)) {
    throw new Error("You cannot start runs");
  }
  const routine = routines.find((candidate) => candidate.id === routineId);
  if (!routine) throw new Error("That routine does not exist");

  const { runId } = await createRun(actor, {
    goal: routine.name,
    environmentId: "stub-local",
    budgetUsd: String(routine.budgetCapUsd),
    routineId: routine.id,
  });
  recordRunTarget(runId, "rehearsal");

  revalidatePath("/app/runs");
  revalidatePath("/app");
  return { runId };
}
