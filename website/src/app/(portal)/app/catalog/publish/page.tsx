import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";

import { publishEntry } from "@/lib/catalog/actions";
import { requireCatalogPage } from "@/lib/catalog/gate";
import { catalogSystem } from "@/lib/fixtures/environments";
import { routines } from "@/lib/fixtures/world";
import { can } from "@/lib/permissions";
import { catalogCopy } from "@/lexicon";
import { PublishForm } from "./PublishForm";

export const metadata: Metadata = { title: "Publish a routine" };

/**
 * Workshop-org publish, minimal: the storefront copy plus a
 * snapshot version and test score floor. Capability requirements derive
 * from the routine's systems: side-effecting systems need a write grant,
 * the rest read. TODO(environments-E0): the registry supplies real
 * requirement shapes (including vendor-specific tools) per routine version.
 */
export default async function PublishPage() {
  const { membership } = await requireCatalogPage();
  if (!can("publish_catalog", membership.role, membership.capabilities)) {
    redirect("/app/catalog");
  }
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
      <PublishForm routines={options} publish={publishEntry} />
    </div>
  );
}
