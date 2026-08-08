/**
 * Workspace cards, presentational: name, purpose, and the connects/used-by
 * fact line ("Connects N systems · used by N routines"). With none, an
 * honest empty state; the create affordance lives in the page header.
 */
import Link from "next/link";

import { EmptyState } from "@/components/EmptyState";
import { copy } from "@/lexicon";

export interface WorkspaceCard {
  id: string;
  name: string;
  purpose: string;
  systemCount: number;
  routineCount: number;
}

export function WorkspaceCards({ workspaces }: { workspaces: WorkspaceCard[] }) {
  if (workspaces.length === 0) {
    return <EmptyState title={copy.workspacesEmptyTitle} body={copy.workspacesEmptyBody} />;
  }
  return (
    <ul className="m-0 grid list-none gap-4 p-0 sm:grid-cols-2">
      {workspaces.map((workspace) => (
        <li key={workspace.id}>
          <Link
            href={`/app/workspaces/${workspace.id}`}
            className="block h-full rounded-lg border border-line bg-card p-6 hover:border-pine"
          >
            <h2 className="text-base font-medium text-ink">{workspace.name}</h2>
            <p className="mt-2 text-sm text-muted">{workspace.purpose}</p>
            <p className="mt-4 font-mono text-xs uppercase text-muted">
              {copy.workspaceCardFact(workspace.systemCount, workspace.routineCount)}
            </p>
          </Link>
        </li>
      ))}
    </ul>
  );
}
