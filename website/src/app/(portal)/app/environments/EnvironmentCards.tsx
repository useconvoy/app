import Link from "next/link";

import { Chip } from "@/components/Chip";
import { EmptyState } from "@/components/EmptyState";
import type { ExecutionEnvironment } from "@/lib/api/environments";

export function EnvironmentCards({
  environments,
  workspaceNames,
}: {
  environments: ExecutionEnvironment[];
  workspaceNames: Record<string, string>;
}) {
  if (environments.length === 0) {
    return (
      <EmptyState
        title="No environments yet"
        body="Create one to choose the compute, browser, and persistence settings runs use."
      />
    );
  }
  return (
    <ul className="m-0 grid list-none gap-4 p-0 md:grid-cols-2">
      {environments.map((environment) => (
        <li key={environment.id}>
          <Link
            href={`/app/environments/${environment.id}`}
            className="block h-full rounded-lg border border-line bg-card p-5 hover:border-pine"
          >
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <h2 className="text-base font-medium text-ink">{environment.name}</h2>
                <p className="mt-1 text-xs text-muted">
                  {workspaceNames[environment.workspaceId] ?? "Workspace"}
                </p>
              </div>
              {environment.isDefault && <Chip>Default</Chip>}
            </div>
            <p className="mt-3 text-sm text-muted">{environment.purpose}</p>
            <dl className="mt-4 grid grid-cols-2 gap-3 border-t border-line-soft pt-3 text-xs">
              <div>
                <dt className="text-muted">Compute</dt>
                <dd className="mt-1 truncate font-mono text-ink">{environment.sandboxTemplate}</dd>
              </div>
              <div>
                <dt className="text-muted">Browser</dt>
                <dd className="mt-1 text-ink">
                  {environment.browserPolicy
                    ? `${environment.browserPolicy.allowedDomains.length} allowed domains`
                    : "Not configured"}
                </dd>
              </div>
            </dl>
          </Link>
        </li>
      ))}
    </ul>
  );
}
