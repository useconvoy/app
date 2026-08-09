/**
 * The Systems board: every catalog system with its connection state and an
 * enterprise connect flow (paste a provider credential — a service account
 * key or a workspace token — no consent popups), plus the org's custom
 * systems (remote tool servers) with an add form that verifies the server
 * answers before anything is saved. Actions come bound from the server
 * page; results render inline and honestly, including "saved but the
 * provider rejected it".
 */
"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/Button";
import { Chip } from "@/components/Chip";
import type { SystemActionResult } from "@/lib/systems/actions";

export type ConnectionState = "connected" | "credentials_pending" | "needs_reauth" | "not_connected";

export interface SystemCard {
  systemId: string;
  displayName: string;
  provider: string | null;
  connectionId: string | null;
  state: ConnectionState | "no_provider";
  toolCount: number;
  sideEffecting: boolean;
}

export interface CustomSystemCard {
  connectionId: string;
  displayName: string;
  state: ConnectionState;
  toolCount: number;
  tools: string[];
}

export interface SystemsBoardProps {
  registryLinked: boolean;
  systems: SystemCard[];
  customSystems: CustomSystemCard[];
  connect: (systemId: string, secretValue: string) => Promise<SystemActionResult>;
  addCustom: (displayName: string, url: string, bearerToken: string) => Promise<SystemActionResult>;
  reattach: (connectionId: string, secretValue: string) => Promise<SystemActionResult>;
}

const STATE_LABEL: Record<SystemCard["state"], string> = {
  connected: "Connected",
  credentials_pending: "Credentials pending",
  needs_reauth: "Needs re-auth",
  not_connected: "Not connected",
  no_provider: "No provider yet",
};

/** Provider-specific paste instructions, in plain language. */
const PROVIDER_HELP: Record<string, { label: string; placeholder: string; steps: string[] }> = {
  google: {
    label: "Service account key (JSON)",
    placeholder: '{ "type": "service_account", ... }',
    steps: [
      "In your Google Cloud console, create a service account and download its JSON key.",
      "Turn on the Drive and Sheets APIs for that project.",
      "Share the folders this organization works in with the service account's email address.",
    ],
  },
  slack: {
    label: "Bot token",
    placeholder: "xoxb-...",
    steps: [
      "Create a Slack app for your workspace and install it.",
      "Give it the channel read and write scopes your routines need.",
      "Paste the bot token that starts with xoxb.",
    ],
  },
};

function stateChip(state: SystemCard["state"]) {
  const tone =
    state === "connected" ? "pass" : state === "no_provider" || state === "not_connected" ? "neutral" : "hold";
  return <Chip tone={tone}>{STATE_LABEL[state]}</Chip>;
}

function CredentialForm({
  label,
  placeholder,
  multiline,
  pendingLabel,
  onSubmit,
}: {
  label: string;
  placeholder: string;
  multiline: boolean;
  pendingLabel: string;
  onSubmit: (value: string) => Promise<SystemActionResult>;
}) {
  const router = useRouter();
  const [value, setValue] = useState("");
  const [result, setResult] = useState<SystemActionResult | null>(null);
  const [pending, startTransition] = useTransition();

  function submit() {
    startTransition(async () => {
      const outcome = await onSubmit(value);
      setResult(outcome);
      if (outcome.ok) {
        setValue("");
        router.refresh();
      }
    });
  }

  return (
    <div className="mt-3 space-y-2">
      <label className="block text-xs font-medium text-muted">{label}</label>
      {multiline ? (
        <textarea
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder={placeholder}
          rows={4}
          className="w-full rounded-md border border-line bg-card px-2 py-1.5 font-mono text-xs text-ink"
        />
      ) : (
        <input
          type="password"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder={placeholder}
          className="w-full rounded-md border border-line bg-card px-2 py-1.5 font-mono text-xs text-ink"
        />
      )}
      <div className="flex items-center gap-3">
        <Button onClick={submit} pending={pending} variant="secondary">
          {pendingLabel}
        </Button>
        {result && (
          <p className={`text-xs ${result.ok ? "text-pass-text" : "text-fail"}`} role="status">
            {result.message}
          </p>
        )}
      </div>
    </div>
  );
}

export function SystemsBoard({
  registryLinked,
  systems,
  customSystems,
  connect,
  addCustom,
  reattach,
}: SystemsBoardProps) {
  const router = useRouter();
  const [openSystem, setOpenSystem] = useState<string | null>(null);
  const [showCustomForm, setShowCustomForm] = useState(false);
  const [customName, setCustomName] = useState("");
  const [customUrl, setCustomUrl] = useState("");
  const [customToken, setCustomToken] = useState("");
  const [customResult, setCustomResult] = useState<SystemActionResult | null>(null);
  const [customPending, startCustom] = useTransition();

  if (!registryLinked) {
    return (
      <div className="rounded-lg border border-line bg-card p-6 text-sm text-muted">
        This deployment is not linked to the systems registry, so connections cannot be managed
        here yet.
      </div>
    );
  }

  function submitCustom() {
    startCustom(async () => {
      const outcome = await addCustom(customName, customUrl, customToken);
      setCustomResult(outcome);
      if (outcome.ok) {
        setCustomName("");
        setCustomUrl("");
        setCustomToken("");
        setShowCustomForm(false);
        router.refresh();
      }
    });
  }

  return (
    <div className="space-y-6">
      <ul className="m-0 list-none space-y-3 p-0">
        {systems.map((system) => {
          const help = system.provider ? PROVIDER_HELP[system.provider] : undefined;
          const open = openSystem === system.systemId;
          const connectable = system.state !== "no_provider" && help !== undefined;
          return (
            <li key={system.systemId} className="rounded-lg border border-line bg-card p-5">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-medium text-ink">{system.displayName}</p>
                  <p className="mt-0.5 text-xs text-muted">
                    {system.state === "connected" || system.state === "credentials_pending"
                      ? `${system.toolCount} ${system.toolCount === 1 ? "tool" : "tools"}`
                      : system.state === "no_provider"
                        ? "Grants for this system stay website-side for now."
                        : "Routines can use this once it is connected."}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  {stateChip(system.state)}
                  {connectable && (
                    <Button
                      variant="secondary"
                      onClick={() => setOpenSystem(open ? null : system.systemId)}
                    >
                      {system.state === "connected" ? "Replace credential" : "Connect"}
                    </Button>
                  )}
                </div>
              </div>
              {open && help && (
                <div className="mt-4 rounded-md border border-dashed border-line p-4">
                  <ol className="m-0 list-decimal space-y-1 pl-5 text-xs text-muted">
                    {help.steps.map((step) => (
                      <li key={step}>{step}</li>
                    ))}
                  </ol>
                  <p className="mt-2 text-xs text-muted">
                    Hosted sign-in with the provider is a later step; pasting a credential your
                    admin controls is the server-to-server way.
                  </p>
                  <CredentialForm
                    label={help.label}
                    placeholder={help.placeholder}
                    multiline={system.provider === "google"}
                    pendingLabel={system.state === "connected" ? "Replace" : "Connect"}
                    onSubmit={(value) =>
                      system.connectionId && system.state !== "not_connected"
                        ? reattach(system.connectionId, value)
                        : connect(system.systemId, value)
                    }
                  />
                </div>
              )}
            </li>
          );
        })}
      </ul>

      <section>
        <div className="flex items-center justify-between">
          <h2 className="font-display text-xl text-ink">Custom systems</h2>
          <Button variant="secondary" onClick={() => setShowCustomForm((current) => !current)}>
            Add custom system
          </Button>
        </div>
        <p className="mt-1 text-sm text-muted">
          A tool server your team runs, spoken to over the open tool protocol. Registering checks
          the server answers and records the tools it offers.
        </p>
        {showCustomForm && (
          <div className="mt-3 rounded-lg border border-dashed border-line bg-card p-4 space-y-2">
            <input
              type="text"
              value={customName}
              onChange={(event) => setCustomName(event.target.value)}
              placeholder="Name, like Internal CRM"
              className="w-full rounded-md border border-line bg-card px-2 py-1.5 text-sm text-ink"
            />
            <input
              type="text"
              value={customUrl}
              onChange={(event) => setCustomUrl(event.target.value)}
              placeholder="https://tools.example.com/mcp"
              className="w-full rounded-md border border-line bg-card px-2 py-1.5 font-mono text-xs text-ink"
            />
            <input
              type="password"
              value={customToken}
              onChange={(event) => setCustomToken(event.target.value)}
              placeholder="Bearer token, if the server needs one"
              className="w-full rounded-md border border-line bg-card px-2 py-1.5 font-mono text-xs text-ink"
            />
            <div className="flex items-center gap-3">
              <Button onClick={submitCustom} pending={customPending}>
                Register
              </Button>
              {customResult && (
                <p className={`text-xs ${customResult.ok ? "text-pass-text" : "text-fail"}`} role="status">
                  {customResult.message}
                </p>
              )}
            </div>
          </div>
        )}
        {customSystems.length > 0 ? (
          <ul className="m-0 mt-3 list-none space-y-3 p-0">
            {customSystems.map((system) => (
              <li key={system.connectionId} className="rounded-lg border border-line bg-card p-5">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <p className="text-sm font-medium text-ink">{system.displayName}</p>
                    <p className="mt-0.5 text-xs text-muted">
                      {system.tools.length > 0 ? system.tools.join(" · ") : "No tools declared"}
                    </p>
                  </div>
                  {stateChip(system.state)}
                </div>
                {system.state === "needs_reauth" && (
                  <CredentialForm
                    label="Bearer token"
                    placeholder="Paste the new token"
                    multiline={false}
                    pendingLabel="Replace"
                    onSubmit={(value) => reattach(system.connectionId, value)}
                  />
                )}
              </li>
            ))}
          </ul>
        ) : (
          !showCustomForm && (
            <p className="mt-3 rounded-lg border border-line bg-card p-4 text-sm text-muted">
              No custom systems yet.
            </p>
          )
        )}
      </section>
    </div>
  );
}
