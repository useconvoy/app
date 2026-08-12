/**
 * Create-workspace modal: name, purpose, and the connector picker.
 * Connectors are searchable by name (organizations accumulate many), and
 * each selected connector expands into its declared actions so the
 * workspace enables exactly the actions it should. Access scope is
 * derived, not asked: enabling any action that makes changes turns the
 * grant into "Can update" and shows the rehearsal-isolation note.
 * Only healthy connectors are selectable: when an option carries a
 * connection status other than "connected", its checkbox is disabled and
 * the row points at the Connectors page instead. (Options without a
 * status, the unlinked dev fallback, stay selectable as before.)
 */
"use client";

import { useId, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { Button } from "@/components/Button";
import type { CreateWorkspacePayload } from "@/lib/workspaces/actions";
import { copy } from "@/lexicon";

export interface SystemOption {
  id: string;
  displayName: string;
  sideEffecting: boolean;
  standInNote: string | null;
  /** Connection state chip; absent keeps the pre-systems-page rendering. */
  status?: "connected" | "credentials_pending" | "needs_reauth" | "not_connected" | "no_provider";
  /** The connector's declared actions; absent keeps the whole surface. */
  tools?: Array<{ name: string; sideEffecting: boolean }>;
}

const STATUS_LABEL: Record<NonNullable<SystemOption["status"]>, string> = {
  connected: "Connected",
  credentials_pending: "Credentials pending",
  needs_reauth: "Needs re-auth",
  not_connected: "Not connected",
  no_provider: "No provider yet",
};

interface SystemChoice {
  included: boolean;
  /** Enabled action names; seeded with the safe (read-only) actions. */
  tools: Record<string, boolean>;
}

export interface CreateWorkspaceModalProps {
  systems: SystemOption[];
  create: (payload: CreateWorkspacePayload) => Promise<{ id: string }>;
}

/** The action's short name: the part after the provider prefix. */
function actionLabel(name: string): string {
  const dot = name.indexOf(".");
  return dot >= 0 ? name.slice(dot + 1) : name;
}

function defaultChoice(system: SystemOption): SystemChoice {
  return {
    included: false,
    tools: Object.fromEntries(
      (system.tools ?? []).map((tool) => [tool.name, !tool.sideEffecting]),
    ),
  };
}

export function CreateWorkspaceModal({ systems, create }: CreateWorkspaceModalProps) {
  const router = useRouter();
  const formId = useId();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [purpose, setPurpose] = useState("");
  const [query, setQuery] = useState("");
  const [choices, setChoices] = useState<Record<string, SystemChoice>>({});
  const [errors, setErrors] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);

  const byId = useMemo(() => new Map(systems.map((system) => [system.id, system])), [systems]);

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return systems;
    return systems.filter((system) => system.displayName.toLowerCase().includes(needle));
  }, [systems, query]);

  function choiceFor(systemId: string): SystemChoice {
    return choices[systemId] ?? defaultChoice(byId.get(systemId)!);
  }

  function updateChoice(systemId: string, patch: Partial<SystemChoice>): void {
    setChoices((current) => ({
      ...current,
      [systemId]: { ...choiceFor(systemId), ...patch },
    }));
  }

  function toggleTool(systemId: string, toolName: string, enabled: boolean): void {
    const current = choiceFor(systemId);
    updateChoice(systemId, { tools: { ...current.tools, [toolName]: enabled } });
  }

  /** Selected actions for a system, in declaration order. */
  function selectedTools(system: SystemOption): Array<{ name: string; sideEffecting: boolean }> {
    const choice = choiceFor(system.id);
    return (system.tools ?? []).filter((tool) => choice.tools[tool.name]);
  }

  function scopeFor(system: SystemOption): "read" | "write" {
    if (!system.tools || system.tools.length === 0) return "read";
    return selectedTools(system).some((tool) => tool.sideEffecting) ? "write" : "read";
  }

  function reset(): void {
    setName("");
    setPurpose("");
    setQuery("");
    setChoices({});
    setErrors([]);
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const included = systems.filter((system) => choiceFor(system.id).included);
    const found: string[] = [];
    if (name.trim().length < 2) found.push("Give the workspace a name.");
    if (purpose.trim().length < 2) found.push("Say which shared work this workspace supports.");
    if (included.length === 0) found.push("Pick at least one connector.");
    for (const system of included) {
      if ((system.tools ?? []).length > 0 && selectedTools(system).length === 0) {
        found.push(`Enable at least one action for ${system.displayName}.`);
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
          const scope = scopeFor(system);
          return {
            systemId: system.id,
            scope,
            // Internal compatibility field. Rehearsal runs always isolate
            // writes; production runs still use the real provider.
            useStandIn: system.sideEffecting && scope === "write",
            ...(system.tools && system.tools.length > 0
              ? { tools: selectedTools(system).map((tool) => tool.name) }
              : {}),
          };
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
                  placeholder="Which shared systems and work this supports"
                  className="mt-1 w-full rounded-md border border-line bg-card px-2 py-1.5 text-sm text-ink"
                />
              </div>
              <fieldset className="rounded-md border border-line p-3">
                <legend className="px-1 text-sm font-medium text-ink">Connectors</legend>
                {systems.length === 0 ? (
                  <p className="text-sm text-muted">
                    Nothing is connected yet. Set up the services this organization works
                    through on the <Link href="/app/connectors" className="underline">Connectors page</Link>,
                    then come back to bundle them into a workspace.
                  </p>
                ) : (
                  <div className="mb-3">
                    <label htmlFor={`${formId}-search`} className="sr-only">
                      Search connectors
                    </label>
                    <input
                      id={`${formId}-search`}
                      type="search"
                      value={query}
                      onChange={(event) => setQuery(event.target.value)}
                      placeholder="Search connectors"
                      className="w-full rounded-md border border-line bg-card px-2 py-1.5 text-sm text-ink"
                    />
                  </div>
                )}
                {systems.length > 0 && visible.length === 0 && (
                  <p className="text-sm text-muted" role="status">
                    No connector matches that search.
                  </p>
                )}
                <ul className="m-0 list-none space-y-3 p-0">
                  {visible.map((system) => {
                    const choice = choiceFor(system.id);
                    const connectable = system.status === undefined || system.status === "connected";
                    const isolatesWrites = choice.included && scopeFor(system) === "write";
                    return (
                      <li key={system.id} className="border-b border-line-soft pb-3 last:border-b-0 last:pb-0">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <label
                            className={`flex items-center gap-2 text-sm ${connectable ? "text-ink" : "text-muted"}`}
                          >
                            <input
                              type="checkbox"
                              checked={choice.included}
                              disabled={!connectable}
                              onChange={(event) =>
                                updateChoice(system.id, { included: event.target.checked })
                              }
                            />
                            {system.displayName}
                            {system.status && (
                              <span
                                className={`rounded-full border px-2 py-0.5 text-[11px] ${
                                  system.status === "connected"
                                    ? "border-pass-soft bg-pass-soft text-pass-text"
                                    : system.status === "no_provider" ||
                                        system.status === "not_connected"
                                      ? "border-line bg-card text-muted"
                                      : "border-hold-soft bg-hold-soft text-hold-text"
                                }`}
                              >
                                {STATUS_LABEL[system.status]}
                              </span>
                            )}
                          </label>
                          {!connectable && (
                            <Link
                              href="/app/connectors"
                              className="text-xs text-muted underline"
                            >
                              Connect it first
                            </Link>
                          )}
                        </div>
                        {choice.included && (system.tools ?? []).length > 0 && (
                          <div className="mt-2 rounded-md border border-line-soft p-2">
                            <p className="text-xs font-medium text-muted">Actions</p>
                            <ul className="m-0 mt-1 list-none space-y-1 p-0">
                              {(system.tools ?? []).map((tool) => (
                                <li key={tool.name}>
                                  <label className="flex items-center gap-2 text-xs text-ink">
                                    <input
                                      type="checkbox"
                                      checked={Boolean(choice.tools[tool.name])}
                                      onChange={(event) =>
                                        toggleTool(system.id, tool.name, event.target.checked)
                                      }
                                    />
                                    <span className="font-mono">{actionLabel(tool.name)}</span>
                                    {tool.sideEffecting && (
                                      <span className="rounded-full border border-hold-soft bg-hold-soft px-2 py-0.5 text-[11px] text-hold-text">
                                        Makes changes
                                      </span>
                                    )}
                                  </label>
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}
                        {isolatesWrites && (
                          <div className="mt-2 rounded-md border border-dashed border-graphite bg-graphite-soft p-2">
                            <p className="text-xs text-graphite">
                              Rehearsal runs isolate writes automatically. Live runs use the real
                              provider connection after you explicitly choose Run live.
                            </p>
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
