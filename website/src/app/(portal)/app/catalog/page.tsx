import type { Metadata } from "next";
import Link from "next/link";

import { requireCatalogPage } from "@/lib/catalog/gate";
import { installedRoutines, updateAvailable } from "@/lib/catalog/installs";
import { listCatalog } from "@/lib/catalog/queries";
import { can } from "@/lib/permissions";
import { catalogCopy } from "@/lexicon";
import { CatalogCards } from "./CatalogCards";

export const metadata: Metadata = { title: "Catalog" };

/**
 * The storefront: entries this org published plus every
 * convoy-visible one, behind the Operator lens. Publish renders only for
 * holders of publish_catalog; the action re-checks it regardless.
 */
export default async function CatalogPage() {
  const { session, membership } = await requireCatalogPage();
  const ctx = { orgId: session.orgId, userId: session.userId };
  const [entries, installs] = await Promise.all([
    listCatalog(ctx),
    installedRoutines(ctx).then((rows) => new Map(rows.map((row) => [row.entryId, row]))),
  ]);
  const showPublish = can("publish_catalog", membership.role, membership.capabilities);
  const cards = entries.map((entry) => {
    const install = installs.get(entry.id);
    return {
      id: entry.id,
      name: entry.storefront.name,
      tagline: entry.storefront.tagline,
      testScoreFloor: entry.thresholds.minScore,
      installed: install !== undefined,
      updateAvailable: updateAvailable(entry.version, install),
    };
  });
  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl text-ink">{catalogCopy.title}</h1>
          <p className="mt-2 text-sm text-muted">{catalogCopy.intro}</p>
        </div>
        {showPublish ? (
          <Link
            href="/app/catalog/publish"
            className="rounded-md border border-line bg-card px-3 py-1.5 text-sm font-medium text-ink hover:border-pine"
          >
            {catalogCopy.publishAction}
          </Link>
        ) : null}
      </header>
      <CatalogCards entries={cards} />
    </div>
  );
}
