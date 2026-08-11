"use client";

import { useId, useState } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/Button";
import { Select } from "@/components/ui/select";
import type { CreateAgentPayload } from "@/lib/agents/actions";

export interface AgentWorkspaceOption {
  id: string;
  name: string;
  systemCount: number;
}

export function CreateAgentModal({
  workspaces,
  create,
}: {
  workspaces: AgentWorkspaceOption[];
  create: (payload: CreateAgentPayload) => Promise<{ id: string }>;
}) {
  const router = useRouter();
  const formId = useId();
  const [open, setOpen] = useState(false);
  const [workspaceId, setWorkspaceId] = useState(workspaces[0]?.id ?? "");
  const [name, setName] = useState("");
  const [purpose, setPurpose] = useState("");
  const [goal, setGoal] = useState("");
  const [instructions, setInstructions] = useState("");
  const [scheduleDescription, setScheduleDescription] = useState("");
  const [budgetCapUsd, setBudgetCapUsd] = useState("75");
  const [sandboxTemplate, setSandboxTemplate] = useState("convoy-devbox-python");
  const [browserEnabled, setBrowserEnabled] = useState(false);
  const [domains, setDomains] = useState("");
  const [persistBrowserProfile, setPersistBrowserProfile] = useState(true);
  const [errors, setErrors] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);

  function reset(): void {
    setWorkspaceId(workspaces[0]?.id ?? "");
    setName("");
    setPurpose("");
    setGoal("");
    setInstructions("");
    setScheduleDescription("");
    setBudgetCapUsd("75");
    setSandboxTemplate("convoy-devbox-python");
    setBrowserEnabled(false);
    setDomains("");
    setPersistBrowserProfile(true);
    setErrors([]);
  }

  async function submit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const found: string[] = [];
    if (!workspaceId) found.push("Choose a workspace.");
    if (name.trim().length < 2) found.push("Give the agent a name.");
    if (purpose.trim().length < 2) found.push("Describe what the agent does.");
    if (goal.trim().length < 2) found.push("Describe the outcome the agent owns.");
    const parsedBudget = Number(budgetCapUsd);
    if (!Number.isFinite(parsedBudget) || parsedBudget < 0) found.push("Choose a valid per-run budget.");
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
        goal: goal.trim(),
        planSteps: instructions.split("\n").map((step) => step.trim()).filter(Boolean),
        scheduleDescription: scheduleDescription.trim(),
        budgetCapUsd: parsedBudget,
        sandboxTemplate: sandboxTemplate.trim(),
        browserEnabled,
        allowedDomains: domains.split(/[\n,]/).map((domain) => domain.trim()).filter(Boolean),
        persistBrowserProfile,
        makeDefault: false,
      });
      setOpen(false);
      reset();
      router.push(`/app/agents/${result.id}`);
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
        New agent
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
              New agent
            </h2>
            <p className="mt-1 text-sm text-muted">
              Give the agent its work, connected systems, and runtime in one place.
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
                  placeholder="Operations agent"
                  className="mt-1 w-full rounded-md border border-line bg-card px-2 py-1.5 text-sm text-ink"
                />
              </label>
              <label className="block text-sm font-medium text-ink">
                Purpose
                <input
                  value={purpose}
                  onChange={(event) => setPurpose(event.target.value)}
                  placeholder="Owns recurring revenue operations work"
                  className="mt-1 w-full rounded-md border border-line bg-card px-2 py-1.5 text-sm text-ink"
                />
              </label>
              <label className="block text-sm font-medium text-ink">
                Goal
                <textarea
                  value={goal}
                  onChange={(event) => setGoal(event.target.value)}
                  placeholder="Review the shared pipeline sheet, identify at-risk renewals, and post a concise recovery summary to the operations channel."
                  rows={4}
                  className="mt-1 w-full rounded-md border border-line bg-card px-2 py-1.5 text-sm text-ink"
                />
              </label>
              <label className="block text-sm font-medium text-ink">
                Instructions <span className="font-normal text-muted">(one step per line)</span>
                <textarea
                  value={instructions}
                  onChange={(event) => setInstructions(event.target.value)}
                  placeholder={"Read the pipeline sheet\nFlag renewals that need attention\nPost the summary to the operations channel"}
                  rows={5}
                  className="mt-1 w-full rounded-md border border-line bg-card px-2 py-1.5 text-sm text-ink"
                />
              </label>
              <div className="grid gap-4 sm:grid-cols-2">
                <label className="block text-sm font-medium text-ink">
                  Schedule <span className="font-normal text-muted">(optional)</span>
                  <input
                    value={scheduleDescription}
                    onChange={(event) => setScheduleDescription(event.target.value)}
                    placeholder="Mondays at 9am"
                    className="mt-1 w-full rounded-md border border-line bg-card px-2 py-1.5 text-sm text-ink"
                  />
                </label>
                <label className="block text-sm font-medium text-ink">
                  Per-run budget (USD)
                  <input
                    type="number"
                    min="0"
                    step="0.01"
                    value={budgetCapUsd}
                    onChange={(event) => setBudgetCapUsd(event.target.value)}
                    className="mt-1 w-full rounded-md border border-line bg-card px-2 py-1.5 text-sm text-ink"
                  />
                </label>
              </div>
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
                  Give this agent browser access
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
                This agent&apos;s files and memory are checkpointed automatically. Pausing releases compute; resuming restores them on fresh compute.
              </div>
              <div className="flex justify-end gap-2">
                <Button variant="secondary" onClick={() => { setOpen(false); reset(); }}>
                  Cancel
                </Button>
                <Button type="submit" pending={submitting}>Create agent</Button>
              </div>
            </form>
          </div>
        </div>
      )}
    </>
  );
}
