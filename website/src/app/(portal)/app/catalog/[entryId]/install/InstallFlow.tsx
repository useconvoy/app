"use client";

/**
 * The install flow shell: pick a workspace, read the
 * compatibility report the server computed for it, then confirm with the
 * version pinned. The confirm stays disabled while systems are missing;
 * the server action re-checks the same line. Completion records nothing
 * domain-side (see lib/catalog/installs).
 */
import { useState } from "react";
import { useRouter } from "next/navigation";

import type { CompatibilityReport } from "@/lib/api/environments";
import { Button } from "@/components/Button";
import { catalogCopy } from "@/lexicon";
import { CompatibilityReportView } from "../../CompatibilityReportView";

export interface InstallOption {
  workspaceId: string;
  workspaceName: string;
  report: CompatibilityReport;
}

export interface InstallFlowProps {
  entryId: string;
  version: number;
  options: InstallOption[];
  systemNames: Record<string, string>;
  install: (entryId: string, workspaceId: string) => Promise<{ pinnedVersion: number }>;
}

export function InstallFlow({ entryId, version, options, systemNames, install }: InstallFlowProps) {
  const router = useRouter();
  const [workspaceId, setWorkspaceId] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [installed, setInstalled] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selected = options.find((option) => option.workspaceId === workspaceId);
  const blocked = selected !== undefined && selected.report.connectPrompts.length > 0;

  async function confirm() {
    if (!selected || blocked || busy) return;
    setBusy(true);
    setError(null);
    try {
      await install(entryId, selected.workspaceId);
      setInstalled(true);
      router.refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  if (installed) {
    return (
      <p role="status" className="rounded-md border border-line bg-card p-4 text-sm text-ink">
        {catalogCopy.installedNote}
      </p>
    );
  }

  return (
    <div className="space-y-6">
      <fieldset className="rounded-md border border-line bg-card p-4">
        <legend className="px-1 text-sm font-medium text-ink">{catalogCopy.chooseWorkspace}</legend>
        <p className="text-xs text-muted">{catalogCopy.chooseWorkspaceHint}</p>
        <div className="mt-3 space-y-2">
          {options.map((option) => (
            <label key={option.workspaceId} className="flex items-center gap-2 text-sm text-ink">
              <input
                type="radio"
                name="workspace"
                value={option.workspaceId}
                checked={workspaceId === option.workspaceId}
                onChange={() => setWorkspaceId(option.workspaceId)}
                className="h-4 w-4 accent-pine"
              />
              {option.workspaceName}
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
