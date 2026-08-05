/**
 * Storefront cards (DESIGN §6), presentational: name and tagline from the
 * storefront jsonb, the test score floor as plain language, and a quiet
 * "update available" chip when an installed entry's snapshot moved past
 * the pinned version. Law 3: no ids, no version refs in the list.
 */
import Link from "next/link";

import { Chip } from "@/components/Chip";
import { EmptyState } from "@/components/EmptyState";
import { catalogCopy } from "@/lexicon";

export interface CatalogCardItem {
  id: string;
  name: string;
  tagline: string;
  testScoreFloor: number;
  installed: boolean;
  updateAvailable: boolean;
}

export interface CatalogCardsProps {
  entries: CatalogCardItem[];
}

export function CatalogCards({ entries }: CatalogCardsProps) {
  if (entries.length === 0) {
    return <EmptyState title={catalogCopy.emptyTitle} body={catalogCopy.emptyBody} />;
  }
  return (
    <ul className="m-0 grid list-none gap-4 p-0 sm:grid-cols-2">
      {entries.map((entry) => (
        <li key={entry.id}>
          <Link
            href={`/app/catalog/${entry.id}`}
            className="block h-full rounded-lg border border-line bg-card p-6 hover:border-pine"
          >
            <div className="flex items-start justify-between gap-3">
              <h2 className="text-base font-medium text-ink">{entry.name}</h2>
              {entry.updateAvailable ? (
                <Chip tone="neutral" mono>
                  {catalogCopy.updateAvailable}
                </Chip>
              ) : entry.installed ? (
                <Chip tone="pass" mono>
                  Installed
                </Chip>
              ) : null}
            </div>
            <p className="mt-2 text-sm text-muted">{entry.tagline}</p>
            <p className="mt-4 font-mono text-xs uppercase text-muted">
              {catalogCopy.testScoreFloor(entry.testScoreFloor)}
            </p>
          </Link>
        </li>
      ))}
    </ul>
  );
}
