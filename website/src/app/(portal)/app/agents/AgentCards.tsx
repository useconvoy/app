import Link from "next/link";

import { EmptyState } from "@/components/EmptyState";
import type { Agent } from "@/lib/api/environments";
import { money } from "@/lib/format";

export function AgentCards({
  agents,
  workspaceNames,
}: {
  agents: Agent[];
  workspaceNames: Record<string, string>;
}) {
  if (agents.length === 0) {
    return (
      <EmptyState
        title="No agents yet"
        body="Create an agent with a goal, connected workspace, and runtime."
      />
    );
  }
  return (
    <ul className="m-0 grid list-none gap-4 p-0 md:grid-cols-2">
      {agents.map((agent) => (
        <li key={agent.id}>
          <Link
            href={`/app/agents/${agent.id}`}
            className="block h-full rounded-lg border border-line bg-card p-5 hover:border-pine"
          >
            <div>
              <h2 className="text-base font-medium text-ink">{agent.name}</h2>
              <p className="mt-1 text-xs text-muted">
                {workspaceNames[agent.workspaceId] ?? "Workspace"}
              </p>
            </div>
            <p className="mt-3 text-sm text-muted">{agent.purpose}</p>
            <p className="mt-3 line-clamp-2 text-sm text-ink">
              {agent.automationConfigured ? agent.goal : "Needs a goal before it can run."}
            </p>
            <dl className="mt-4 grid grid-cols-3 gap-3 border-t border-line-soft pt-3 text-xs">
              <div>
                <dt className="text-muted">Compute</dt>
                <dd className="mt-1 truncate font-mono text-ink">{agent.sandboxTemplate}</dd>
              </div>
              <div>
                <dt className="text-muted">Browser</dt>
                <dd className="mt-1 text-ink">
                  {agent.browserPolicy
                    ? `${agent.browserPolicy.allowedDomains.length} allowed domains`
                    : "Not configured"}
                </dd>
              </div>
              <div>
                <dt className="text-muted">Budget</dt>
                <dd className="mt-1 text-ink">{money(agent.budgetCapUsd)} per run</dd>
              </div>
            </dl>
          </Link>
        </li>
      ))}
    </ul>
  );
}
