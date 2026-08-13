import type { Metadata } from "next";

import { requireCatalogPage } from "@/lib/catalog/gate";
import { installedRoutines, updateAvailable } from "@/lib/catalog/installs";
import { listCatalog } from "@/lib/catalog/queries";
import { catalogCopy } from "@/lexicon";
import { CatalogCards } from "./CatalogCards";

export const metadata: Metadata = { title: "Catalog" };

/**
 * The storefront: every convoy-visible Agent template, behind the
 * Operator lens. Publishing is platform-side only for now (the deploy
 * seeds Convoy's entries); a self-serve publish surface returns later.
 */
export default async function CatalogPage() {
  const { session } = await requireCatalogPage();
  const ctx = { orgId: session.orgId, userId: session.userId };
  const [entries, installs] = await Promise.all([
    listCatalog(ctx),
    installedRoutines(ctx).then((rows) => new Map(rows.map((row) => [row.entryId, row]))),
  ]);
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
      <header>
        <h1 className="font-display text-3xl text-ink">{catalogCopy.title}</h1>
        <p className="mt-2 text-sm text-muted">{catalogCopy.intro}</p>
      </header>
      <CatalogCards entries={cards} />
    </div>
  );
}
