import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { Chip } from "@/components/Chip";
import { friendlyDate } from "@/lib/format";
import { requireCatalogPage } from "@/lib/catalog/gate";
import { installedRoutines, updateAvailable } from "@/lib/catalog/installs";
import { getEntry } from "@/lib/catalog/queries";
import { systemCatalog } from "@/lib/fixtures/environments";
import { catalogCopy, grantLabels } from "@/lexicon";

export const metadata: Metadata = { title: "Catalog" };

/**
 * Entry detail: the storefront description, what the routine
 * needs in plain language, the test score floor ("ships only above 85"),
 * and the changelog of published snapshots. Install hangs off the header;
 * the pinned-version and update-available states render quietly.
 */
export default async function CatalogEntryPage({
  params,
}: {
  params: Promise<{ entryId: string }>;
}) {
  const { entryId } = await params;
  const { session } = await requireCatalogPage();
  const entry = await getEntry({ orgId: session.orgId, userId: session.userId }, entryId);
  if (!entry) notFound();

  const install = installedRoutines(session.orgId).find((row) => row.entryId === entry.id);
  const hasUpdate = updateAvailable(entry.version, install);
  const systemName = (systemId: string) =>
    systemCatalog.find((system) => system.id === systemId)?.displayName ?? systemId;

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs text-muted">
            <Link href="/app/catalog" className="underline">
              {catalogCopy.title}
            </Link>
          </p>
          <h1 className="mt-1 font-display text-3xl text-ink">{entry.storefront.name}</h1>
          <p className="mt-2 text-sm text-muted">{entry.storefront.tagline}</p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {install ? (
              <Chip tone="pass" mono>
                {catalogCopy.installedPinned(install.pinnedVersion)}
              </Chip>
            ) : null}
            {hasUpdate ? (
              <Chip tone="neutral" mono>
                {catalogCopy.updateAvailable}
              </Chip>
            ) : null}
          </div>
        </div>
        <Link
          href={`/app/catalog/${entry.id}/install`}
          className="rounded-md border border-pine bg-pine px-3 py-1.5 text-sm font-medium text-card hover:bg-pine-deep"
        >
          {catalogCopy.installRoutine}
        </Link>
      </header>

      <section className="rounded-md border border-line bg-card p-6">
        <p className="text-sm text-ink">{entry.storefront.description}</p>
      </section>

      <section className="rounded-md border border-line bg-card p-6">
        <h2 className="text-base font-medium text-ink">{catalogCopy.whatItNeeds}</h2>
        <ul className="mt-3 m-0 list-none space-y-2 p-0">
          {entry.requirements.systems.map((required) => (
            <li key={required.systemId} className="flex items-center gap-2 text-sm text-ink">
              {systemName(required.systemId)}
              <Chip mono>{grantLabels[required.scope]}</Chip>
            </li>
          ))}
        </ul>
        <p className="mt-4 font-mono text-xs uppercase text-muted">
          {catalogCopy.testScoreFloor(entry.thresholds.minScore)}
        </p>
      </section>

      <section className="rounded-md border border-line bg-card p-6">
        <h2 className="text-base font-medium text-ink">{catalogCopy.changelogTitle}</h2>
        <ul className="mt-3 m-0 list-none space-y-2 p-0">
          {[...entry.changelog].reverse().map((item) => (
            <li key={item.version} className="flex items-center gap-3 text-sm text-ink">
              <Chip mono>{catalogCopy.versionChip(item.version)}</Chip>
              <span>{item.note}</span>
              <span className="font-mono text-xs text-muted">{friendlyDate(item.at)}</span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
