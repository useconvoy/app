import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";

import { EmptyState } from "@/components/EmptyState";
import { publishEntry } from "@/lib/catalog/actions";
import { requireCatalogPage } from "@/lib/catalog/gate";
import { can } from "@/lib/permissions";
import { listRoutines } from "@/lib/routines/queries";
import { catalogSystem } from "@/lib/workspaces/system-catalog";
import { catalogCopy, copy } from "@/lexicon";
import { PublishForm } from "./PublishForm";

export const metadata: Metadata = { title: "Publish an Agent template" };
export const dynamic = "force-dynamic";

/**
 * Workshop-org publish, minimal: pick one of the org's own routines, then
 * the storefront copy plus a snapshot version and test score floor.
 * Capability requirements derive from the routine's systems:
 * side-effecting systems need a write grant, the rest read.
 * TODO(environments-E0): the registry supplies real requirement shapes
 * (including vendor-specific tools) per routine version.
 */
export default async function PublishPage() {
  const { session, membership } = await requireCatalogPage();
  if (!can("publish_catalog", membership.role, membership.capabilities)) {
    redirect("/app/catalog");
  }
  const routines = await listRoutines(session.orgId);
  const options = routines.map((routine) => ({
    id: routine.id,
    name: routine.name,
    requirements: {
      systems: routine.systems.map((systemId) => ({
        systemId,
        scope: (catalogSystem(systemId)?.sideEffecting ? "write" : "read") as "read" | "write",
      })),
      vendorSpecificTools: [],
    },
  }));
  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <header>
        <p className="text-xs text-muted">
          <Link href="/app/catalog" className="underline">
            {catalogCopy.title}
          </Link>
        </p>
        <h1 className="mt-1 font-display text-3xl text-ink">{catalogCopy.publishTitle}</h1>
        <p className="mt-2 text-sm text-muted">{catalogCopy.publishIntro}</p>
      </header>
      {options.length > 0 ? (
        <PublishForm routines={options} publish={publishEntry} />
      ) : (
        <EmptyState title={copy.routinesEmptyTitle} body={copy.routinesEmptyBody} />
      )}
    </div>
  );
}
