import type { Metadata } from "next";
import Link from "next/link";

import { listRoutineViews } from "@/lib/routines/data";
import { requireRoutinesPage } from "@/lib/routines/gate";
import { orgTenantId } from "@/lib/routines/queries";
import { can } from "@/lib/permissions";
import { catalogCopy } from "@/lexicon";
import { RoutinesList } from "./RoutinesList";

export const metadata: Metadata = { title: "Routines" };

/**
 * Routines list (DESIGN §5): every role views; health comes from the
 * latest real run per routine. No "New routine" button in phase 1; the
 * catalog lives behind this list instead (DESIGN §6): staff Operators
 * install published routines, so the only affordance is "Install a
 * routine" under the Operator lens.
 */
export default async function RoutinesPage() {
  const { session, membership } = await requireRoutinesPage();
  const tenantId = await orgTenantId(session.orgId);
  const routines = await listRoutineViews({ actorId: session.userId, tenantId });
  const showInstall = can("manage_workspaces", membership.role, membership.capabilities);
  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl text-ink">Routines</h1>
          <p className="mt-2 text-sm text-muted">
            The jobs this organization has handed over, and how each one is doing.
          </p>
        </div>
        {showInstall ? (
          <Link
            href="/app/catalog"
            className="rounded-md border border-line bg-card px-3 py-1.5 text-sm font-medium text-ink hover:border-pine"
          >
            {catalogCopy.installRoutine}
          </Link>
        ) : null}
      </header>
      <RoutinesList routines={routines} showHowItFits />
    </div>
  );
}
