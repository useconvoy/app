/**
 * Create-workspace modal: name, purpose, the system picker with View
 * only / Can update grants, and the automatic rehearsal-copy note. One
 * hard rule lives here: any side-effecting system granted Can update
 * must take its stand-in before submit; the server action re-validates
 * the same rule.
 */
"use client";

import { useId, useState } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/Button";
import type { CreateWorkspacePayload } from "@/lib/workspaces/actions";
import { copy, grantLabels } from "@/lexicon";
import { Select } from "@/components/ui/select";

export interface SystemOption {
  id: string;
  displayName: string;
  sideEffecting: boolean;
  standInNote: string | null;
}

interface SystemChoice {
  included: boolean;
  scope: "read" | "write";
  useStandIn: boolean;
}

export interface CreateWorkspaceModalProps {
  systems: SystemOption[];
  create: (payload: CreateWorkspacePayload) => Promise<{ id: string }>;
}

const EMPTY_CHOICE: SystemChoice = { included: false, scope: "read", useStandIn: false };

export function CreateWorkspaceModal({ systems, create }: CreateWorkspaceModalProps) {
  const router = useRouter();
  const formId = useId();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [purpose, setPurpose] = useState("");
  const [choices, setChoices] = useState<Record<string, SystemChoice>>({});
  const [errors, setErrors] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);

  function choiceFor(systemId: string): SystemChoice {
    return choices[systemId] ?? EMPTY_CHOICE;
  }

  function updateChoice(systemId: string, patch: Partial<SystemChoice>): void {
    setChoices((current) => ({
      ...current,
      [systemId]: { ...choiceFor(systemId), ...patch },
    }));
  }

  function reset(): void {
    setName("");
    setPurpose("");
    setChoices({});
    setErrors([]);
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const included = systems.filter((system) => choiceFor(system.id).included);
    const found: string[] = [];
    if (name.trim().length < 2) found.push("Give the workspace a name.");
    if (purpose.trim().length < 2) found.push("Say in a sentence what runs here.");
    if (included.length === 0) found.push("Connect at least one system.");
    for (const system of included) {
      const choice = choiceFor(system.id);
      if (system.sideEffecting && choice.scope === "write" && !choice.useStandIn) {
        found.push(copy.standInRequired(system.displayName));
      }
    }
    if (found.length > 0) {
      setErrors(found);
      return;
    }
    setErrors([]);
    setSubmitting(true);
    try {
      await create({
        name: name.trim(),
        purpose: purpose.trim(),
        systems: included.map((system) => {
          const choice = choiceFor(system.id);
          return { systemId: system.id, scope: choice.scope, useStandIn: choice.useStandIn };
        }),
      });
      setOpen(false);
      reset();
      router.refresh();
    } catch {
      setErrors(["That did not work. Check the details and try again."]);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <Button onClick={() => setOpen(true)}>New workspace</Button>
      {open && (
        <div className="fixed inset-0 z-40 flex items-start justify-center overflow-y-auto bg-ink/40 p-6">
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby={`${formId}-title`}
            className="w-full max-w-lg rounded-lg border border-line bg-card p-6 shadow-lg"
          >
            <h2 id={`${formId}-title`} className="font-display text-xl text-ink">
              New workspace
            </h2>
            <form onSubmit={handleSubmit} className="mt-4 space-y-4" noValidate>
              {errors.length > 0 && (
                <div role="alert" className="rounded-md border border-fail-soft bg-fail-soft p-3">
                  <ul className="m-0 list-none space-y-1 p-0 text-sm text-fail">
                    {errors.map((error) => (
                      <li key={error}>{error}</li>
                    ))}
                  </ul>
                </div>
              )}
              <div>
                <label htmlFor={`${formId}-name`} className="text-sm font-medium text-ink">
                  Name
                </label>
                <input
                  id={`${formId}-name`}
                  type="text"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  className="mt-1 w-full rounded-md border border-line bg-card px-2 py-1.5 text-sm text-ink"
                />
              </div>
              <div>
                <label htmlFor={`${formId}-purpose`} className="text-sm font-medium text-ink">
                  Purpose
                </label>
                <input
                  id={`${formId}-purpose`}
                  type="text"
                  value={purpose}
                  onChange={(event) => setPurpose(event.target.value)}
                  placeholder="What runs here"
                  className="mt-1 w-full rounded-md border border-line bg-card px-2 py-1.5 text-sm text-ink"
                />
              </div>
              <fieldset className="rounded-md border border-line p-3">
                <legend className="px-1 text-sm font-medium text-ink">Systems</legend>
                <ul className="m-0 list-none space-y-3 p-0">
                  {systems.map((system) => {
                    const choice = choiceFor(system.id);
                    const needsStandIn =
                      choice.included && system.sideEffecting && choice.scope === "write";
                    return (
                      <li key={system.id} className="border-b border-line-soft pb-3 last:border-b-0 last:pb-0">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <label className="flex items-center gap-2 text-sm text-ink">
                            <input
                              type="checkbox"
                              checked={choice.included}
                              onChange={(event) =>
                                updateChoice(system.id, { included: event.target.checked })
                              }
                            />
                            {system.displayName}
                          </label>
                          {choice.included && (
                            <label className="flex items-center gap-2 text-xs text-muted">
                              Access
                              <Select
                                value={choice.scope}
                                onChange={(event) =>
                                  updateChoice(system.id, {
                                    scope: event.target.value === "write" ? "write" : "read",
                                  })
                                }
                                className="rounded-md border border-line bg-card px-2 py-1 text-sm text-ink"
                              >
                                <option value="read">{grantLabels.read}</option>
                                <option value="write">{grantLabels.write}</option>
                              </Select>
                            </label>
                          )}
                        </div>
                        {needsStandIn && (
                          <div className="mt-2 rounded-md border border-dashed border-graphite bg-graphite-soft p-2">
                            <label className="flex items-start gap-2 text-sm text-graphite">
                              <input
                                type="checkbox"
                                checked={choice.useStandIn}
                                onChange={(event) =>
                                  updateChoice(system.id, { useStandIn: event.target.checked })
                                }
                              />
                              <span>
                                Use the stand-in
                                {system.standInNote && (
                                  <span className="mt-0.5 block text-xs">{system.standInNote}</span>
                                )}
                              </span>
                            </label>
                          </div>
                        )}
                      </li>
                    );
                  })}
                </ul>
              </fieldset>
              <p className="rounded-md border border-dashed border-graphite bg-graphite-soft p-3 text-sm text-graphite">
                {copy.rehearsalCopyAutoNote}
              </p>
              <div className="flex justify-end gap-2">
                <Button
                  variant="secondary"
                  onClick={() => {
                    setOpen(false);
                    reset();
                  }}
                >
                  Cancel
                </Button>
                <Button type="submit" pending={submitting}>
                  Create workspace
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}
    </>
  );
}
