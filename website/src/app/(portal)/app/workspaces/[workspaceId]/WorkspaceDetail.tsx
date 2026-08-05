/**
 * Workspace detail, presentational: connected
 * systems with their grants, stand-ins per side-effecting system, the
 * rehearsal copy, the clock, versions, and the activity placeholder.
 */
import Link from "next/link";

import { Chip } from "@/components/Chip";
import { EmptyState } from "@/components/EmptyState";
import { SystemRow } from "@/components/SystemRow";
import type { Workspace } from "@/lib/api/environments";
import { friendlyDate } from "@/lib/format";
import { clockModeDescriptions, clockModeLabels, copy, terms } from "@/lexicon";

export interface WorkspaceDetailProps {
  workspace: Workspace;
  /** Names of routines that run in this workspace. */
  routineNames: string[];
}

export function WorkspaceDetail({ workspace, routineNames }: WorkspaceDetailProps) {
  const standIns = workspace.systems.filter((grant) => grant.standIn);
  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <header>
        <p className="text-xs text-muted">
          <Link href="/app/workspaces" className="underline">
            Workspaces
          </Link>
        </p>
        <h1 className="mt-1 font-display text-3xl text-ink">{workspace.name}</h1>
        <p className="mt-2 text-sm text-muted">{workspace.purpose}</p>
        <p className="mt-3 font-mono text-xs uppercase text-muted">
          Created {friendlyDate(workspace.createdAt)}
        </p>
      </header>

      <Section title="Connected systems">
        <ul className="m-0 list-none rounded-lg border border-line bg-card px-4 py-1">
          {workspace.systems.map((grant) => (
            <SystemRow
              key={grant.systemId}
              name={grant.displayName}
              grant={grant.scope}
              standInFor={grant.standIn ? grant.displayName : undefined}
            />
          ))}
        </ul>
      </Section>

      <Section title="Stand-ins">
        {standIns.length > 0 ? (
          <ul className="m-0 list-none space-y-2 p-0">
            {standIns.map((grant) => (
              <li
                key={grant.systemId}
                className="rounded-lg border border-dashed border-graphite bg-graphite-soft p-4 text-sm text-graphite"
              >
                {grant.standIn?.note}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted">
            Nothing here reaches outside the organization, so no stand-ins are needed.
          </p>
        )}
      </Section>

      <Section title="Rehearsal copy">
        <aside className="rounded-lg border border-dashed border-graphite bg-graphite-soft p-4">
          <p className="text-sm text-graphite">{copy.rehearsalCopyExplainer}</p>
          <p className="mt-2 font-mono text-xs uppercase text-graphite">{terms.sandboxBinding}</p>
        </aside>
      </Section>

      <Section title="Clock">
        <div className="rounded-lg border border-line bg-card p-5">
          <Chip mono>{clockModeLabels[workspace.clockMode]}</Chip>
          <p className="mt-2 text-sm text-muted">{clockModeDescriptions[workspace.clockMode]}</p>
        </div>
      </Section>

      <Section title="Versions">
        <ul className="m-0 list-none rounded-lg border border-line bg-card px-4 py-1">
          {workspace.versions.map((version) => (
            <li
              key={version.version}
              className="flex flex-wrap items-center gap-3 border-b border-line-soft py-3 last:border-b-0"
            >
              <span className="font-mono text-xs uppercase text-ink">v{version.version}</span>
              <span className="text-sm text-ink">{version.note}</span>
              <span className="ml-auto font-mono text-xs text-muted">
                {friendlyDate(version.createdAt)}
              </span>
            </li>
          ))}
        </ul>
      </Section>

      <Section title="Routines that run here">
        {routineNames.length > 0 ? (
          <ul className="m-0 flex list-none flex-wrap gap-2 p-0">
            {routineNames.map((name) => (
              <li key={name}>
                <Chip>{name}</Chip>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted">No routines run here yet.</p>
        )}
      </Section>

      {/* TODO(website): wire this activity feed to the cross-run event
          explorer behind Logs; until then it renders the empty state. */}
      <Section title="Activity">
        <EmptyState
          title="No activity yet"
          body="Changes to this workspace will appear here."
        />
      </Section>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section aria-label={title}>
      <h2 className="font-display text-lg text-ink">{title}</h2>
      <div className="mt-3">{children}</div>
    </section>
  );
}
