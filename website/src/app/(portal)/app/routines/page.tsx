import type { Metadata } from "next";
import Link from "next/link";

import { listRoutineViews } from "@/lib/routines/data";
import { requireRoutinesPage } from "@/lib/routines/gate";
import { listRoutines, orgTenantId } from "@/lib/routines/queries";
import { can } from "@/lib/permissions";
import { catalogCopy } from "@/lexicon";
import { RoutinesList } from "./RoutinesList";

export const metadata: Metadata = { title: "Routines" };
export const dynamic = "force-dynamic";

/**
 * Routines list: every role views; health comes from the latest real run
 * per routine. Routines arrive through the catalog install flow, so there
 * is no "New routine" button; the only affordance is "Install a routine"
 * under the Operator lens, and a fresh organization sees an honest empty
 * state pointing there.
 */
export default async function RoutinesPage() {
  const { session, membership } = await requireRoutinesPage();
  const [records, tenantId] = await Promise.all([
    listRoutines(session.orgId),
    orgTenantId(session.orgId),
  ]);
  const routines = await listRoutineViews(records, { actorId: session.userId, tenantId });
  const showInstall = can("manage_workspaces", membership.role, membership.capabilities);
  const installLink = (
    <Link
      href="/app/catalog"
      className="rounded-md border border-line bg-card px-3 py-1.5 text-sm font-medium text-ink hover:border-pine"
    >
      {catalogCopy.installRoutine}
    </Link>
  );
  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl text-ink">Routines</h1>
          <p className="mt-2 text-sm text-muted">
            The jobs this organization has handed over, and how each one is doing.
          </p>
        </div>
        {showInstall ? installLink : null}
      </header>
      <RoutinesList
        routines={routines}
        showHowItFits
        emptyAction={showInstall ? installLink : undefined}
      />
    </div>
  );
}
