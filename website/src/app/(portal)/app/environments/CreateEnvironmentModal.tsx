"use client";

import { useId, useState } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/Button";
import { Select } from "@/components/ui/select";
import type { CreateExecutionEnvironmentPayload } from "@/lib/environments/actions";

export interface EnvironmentWorkspaceOption {
  id: string;
  name: string;
  systemCount: number;
}

export function CreateEnvironmentModal({
  workspaces,
  create,
}: {
  workspaces: EnvironmentWorkspaceOption[];
  create: (payload: CreateExecutionEnvironmentPayload) => Promise<{ id: string }>;
}) {
  const router = useRouter();
  const formId = useId();
  const [open, setOpen] = useState(false);
  const [workspaceId, setWorkspaceId] = useState(workspaces[0]?.id ?? "");
  const [name, setName] = useState("");
  const [purpose, setPurpose] = useState("");
  const [sandboxTemplate, setSandboxTemplate] = useState("convoy-devbox-python");
  const [browserEnabled, setBrowserEnabled] = useState(false);
  const [domains, setDomains] = useState("");
  const [persistBrowserProfile, setPersistBrowserProfile] = useState(true);
  const [makeDefault, setMakeDefault] = useState(true);
  const [errors, setErrors] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);

  function reset(): void {
    setWorkspaceId(workspaces[0]?.id ?? "");
    setName("");
    setPurpose("");
    setSandboxTemplate("convoy-devbox-python");
    setBrowserEnabled(false);
    setDomains("");
    setPersistBrowserProfile(true);
    setMakeDefault(true);
    setErrors([]);
  }

  async function submit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const found: string[] = [];
    if (!workspaceId) found.push("Choose a workspace.");
    if (name.trim().length < 2) found.push("Give the environment a name.");
    if (purpose.trim().length < 2) found.push("Describe what runs here.");
    if (sandboxTemplate.trim().length < 2) found.push("Choose a compute template.");
    if (found.length > 0) {
      setErrors(found);
      return;
    }
    setErrors([]);
    setSubmitting(true);
    try {
      const result = await create({
        workspaceId,
        name: name.trim(),
        purpose: purpose.trim(),
        sandboxTemplate: sandboxTemplate.trim(),
        browserEnabled,
        allowedDomains: domains.split(/[\n,]/).map((domain) => domain.trim()).filter(Boolean),
        persistBrowserProfile,
        makeDefault,
      });
      setOpen(false);
      reset();
      router.push(`/app/environments/${result.id}`);
      router.refresh();
    } catch (error) {
      setErrors([error instanceof Error ? error.message : "That did not work. Try again."]);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <Button onClick={() => setOpen(true)} disabled={workspaces.length === 0}>
        New environment
      </Button>
      {open && (
        <div className="fixed inset-0 z-40 flex items-start justify-center overflow-y-auto bg-ink/40 p-6">
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby={`${formId}-title`}
            className="w-full max-w-lg rounded-lg border border-line bg-card p-6 shadow-lg"
          >
            <h2 id={`${formId}-title`} className="font-display text-xl text-ink">
              New environment
            </h2>
            <p className="mt-1 text-sm text-muted">
              Choose where compute runs and what browser state it may retain.
            </p>
            <form onSubmit={submit} className="mt-5 space-y-4" noValidate>
              {errors.length > 0 && (
                <div role="alert" className="rounded-md border border-fail-soft bg-fail-soft p-3">
                  <ul className="m-0 list-none space-y-1 p-0 text-sm text-fail">
                    {errors.map((error) => <li key={error}>{error}</li>)}
                  </ul>
                </div>
              )}
              <label className="block text-sm font-medium text-ink">
                Workspace
                <Select
                  value={workspaceId}
                  onChange={(event) => setWorkspaceId(event.target.value)}
                  className="mt-1 w-full"
                >
                  {workspaces.map((workspace) => (
                    <option key={workspace.id} value={workspace.id}>
                      {workspace.name} ({workspace.systemCount} systems)
                    </option>
                  ))}
                </Select>
              </label>
              <label className="block text-sm font-medium text-ink">
                Name
                <input
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  placeholder="Production operations"
                  className="mt-1 w-full rounded-md border border-line bg-card px-2 py-1.5 text-sm text-ink"
                />
              </label>
              <label className="block text-sm font-medium text-ink">
                Purpose
                <input
                  value={purpose}
                  onChange={(event) => setPurpose(event.target.value)}
                  placeholder="Runs approved routines against live systems"
                  className="mt-1 w-full rounded-md border border-line bg-card px-2 py-1.5 text-sm text-ink"
                />
              </label>
              <label className="block text-sm font-medium text-ink">
                Compute template
                <input
                  value={sandboxTemplate}
                  onChange={(event) => setSandboxTemplate(event.target.value)}
                  className="mt-1 w-full rounded-md border border-line bg-card px-2 py-1.5 font-mono text-sm text-ink"
                />
              </label>
              <fieldset className="rounded-md border border-line p-3">
                <legend className="px-1 text-sm font-medium text-ink">Browser</legend>
                <label className="flex items-center gap-2 text-sm text-ink">
                  <input
                    type="checkbox"
                    checked={browserEnabled}
                    onChange={(event) => setBrowserEnabled(event.target.checked)}
                  />
                  Make a browser available to runs
                </label>
                {browserEnabled && (
                  <div className="mt-3 space-y-3">
                    <label className="block text-sm text-ink">
                      Allowed domains
                      <textarea
                        value={domains}
                        onChange={(event) => setDomains(event.target.value)}
                        placeholder="app.example.com, docs.example.com"
                        rows={3}
                        className="mt-1 w-full rounded-md border border-line bg-card px-2 py-1.5 font-mono text-sm text-ink"
                      />
                    </label>
                    <label className="flex items-center gap-2 text-sm text-ink">
                      <input
                        type="checkbox"
                        checked={persistBrowserProfile}
                        onChange={(event) => setPersistBrowserProfile(event.target.checked)}
                      />
                      Restore browser sign-in state after a pause
                    </label>
                  </div>
                )}
              </fieldset>
              <div className="rounded-md border border-pass-soft bg-pass-soft p-3 text-sm text-pass-text">
                Workspace files are checkpointed automatically. Pausing releases compute; resuming restores them on fresh compute.
              </div>
              <label className="flex items-center gap-2 text-sm text-ink">
                <input
                  type="checkbox"
                  checked={makeDefault}
                  onChange={(event) => setMakeDefault(event.target.checked)}
                />
                Use this environment by default for the workspace
              </label>
              <div className="flex justify-end gap-2">
                <Button variant="secondary" onClick={() => { setOpen(false); reset(); }}>
                  Cancel
                </Button>
                <Button type="submit" pending={submitting}>Create environment</Button>
              </div>
            </form>
          </div>
        </div>
      )}
    </>
  );
}
