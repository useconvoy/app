"use client";

/**
 * The install flow shell: pick a workspace, read the
 * compatibility report the server computed for it, then confirm with the
 * version pinned. The confirm stays disabled while systems are missing;
 * the server action re-checks the same line. Completion records nothing
 * domain-side (see lib/catalog/installs).
 */
import Link from "next/link";
import { useState } from "react";
import { useRouter } from "next/navigation";

import type { CompatibilityReport } from "@/lib/api/environments";
import { Button } from "@/components/Button";
import { catalogCopy } from "@/lexicon";
import { CompatibilityReportView } from "../../CompatibilityReportView";

export interface InstallOption {
  agentId: string;
  agentName: string;
  workspaceId: string;
  workspaceName: string;
  report: CompatibilityReport;
}

export interface InstallFlowProps {
  entryId: string;
  version: number;
  options: InstallOption[];
  systemNames: Record<string, string>;
  install: (entryId: string, agentId: string) => Promise<{ pinnedVersion: number; agentId: string }>;
}

export function InstallFlow({ entryId, version, options, systemNames, install }: InstallFlowProps) {
  const router = useRouter();
  const [agentId, setAgentId] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [installedAgentId, setInstalledAgentId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const selected = options.find((option) => option.agentId === agentId);
  const blocked = selected !== undefined && selected.report.connectPrompts.length > 0;

  async function confirm() {
    if (!selected || blocked || busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await install(entryId, selected.agentId);
      setInstalledAgentId(result.agentId);
      router.refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  if (installedAgentId) {
    return (
      <div role="status" className="rounded-md border border-line bg-card p-4 text-sm text-ink">
        <p>{catalogCopy.installedNote}</p>
        <Link href={`/app/agents/${installedAgentId}`} className="mt-2 inline-block text-sm text-ink underline">
          View installed Agent
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <fieldset className="rounded-md border border-line bg-card p-4">
        <legend className="px-1 text-sm font-medium text-ink">Choose an agent</legend>
        <p className="text-xs text-muted">The template adds its goal and requirements to this Agent. The Workspace remains shared with any other Agents using it.</p>
        <div className="mt-3 space-y-2">
          {options.map((option) => (
            <label key={option.agentId} className="flex items-center gap-2 text-sm text-ink">
              <input
                type="radio"
                name="agent"
                value={option.agentId}
                checked={agentId === option.agentId}
                onChange={() => setAgentId(option.agentId)}
                className="h-4 w-4 accent-pine"
              />
              <span>{option.agentName} <span className="text-muted">· {option.workspaceName}</span></span>
            </label>
          ))}
        </div>
      </fieldset>

      {selected ? (
        <div className="rounded-md border border-line bg-card p-4">
          <CompatibilityReportView report={selected.report} systemNames={systemNames} />
          <div className="mt-4 flex items-center gap-3 border-t border-line-soft pt-4">
            <Button onClick={confirm} disabled={blocked || busy}>
              {catalogCopy.installPinned(version)}
            </Button>
            {blocked ? <p className="text-xs text-hold-text">{catalogCopy.installBlocked}</p> : null}
          </div>
        </div>
      ) : null}

      {error ? (
        <p role="alert" className="text-sm text-fail">
          {error}
        </p>
      ) : null}
    </div>
  );
}
