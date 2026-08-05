import type { Metadata } from "next";

import { listRoutineViews } from "@/lib/routines/data";
import { requireRoutinesPage } from "@/lib/routines/gate";
import { orgTenantId } from "@/lib/routines/queries";
import { RoutinesList } from "./RoutinesList";

export const metadata: Metadata = { title: "Routines" };

/**
 * Routines list (DESIGN §5): every role views; health comes from the
 * latest real run per routine. No "New routine" button in phase 1.
 */
export default async function RoutinesPage() {
  const { session } = await requireRoutinesPage();
  const tenantId = await orgTenantId(session.orgId);
  const routines = await listRoutineViews({ actorId: session.userId, tenantId });
  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header>
        <h1 className="font-display text-3xl text-ink">Routines</h1>
        <p className="mt-2 text-sm text-muted">
          The jobs this organization has handed over, and how each one is doing.
        </p>
      </header>
      <RoutinesList routines={routines} showHowItFits />
    </div>
  );
}
