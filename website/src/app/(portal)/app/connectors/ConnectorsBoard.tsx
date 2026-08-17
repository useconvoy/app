/**
 * The Connectors board: the organization's whole connector inventory.
 * "Connected" lists every connection that exists, with its live health,
 * a Check health probe, and a reconnect flow; "Available" lists every
 * provider the platform serves that is not connected yet. Both filter
 * through one search box (the catalog grows), and the org's own MCP tool
 * servers register through the custom flow. Actions come bound from the
 * server page; results render inline and honestly, including "saved but
 * the provider rejected it".
 */
"use client";

import { useMemo, useState, useTransition } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/Button";
import { Chip } from "@/components/Chip";
import type { ConnectorActionResult } from "@/lib/connectors/actions";

export type ConnectionState = "connected" | "credentials_pending" | "needs_reauth" | "not_connected";

export interface ConnectorCard {
  systemId: string;
  displayName: string;
  provider: string | null;
  connectionId: string | null;
  state: ConnectionState;
  toolCount: number;
  sideEffecting: boolean;
  /** Registry-served copy; when absent the static fallbacks apply. */
  blurb?: string;
  credential?: { label: string; placeholder: string; multiline: boolean; steps: string[] };
  /** Hosted install door (e.g. Add to Slack); absent keeps paste-only. */
  oauth?: { href: string; label: string };
}

export interface CustomConnectorCard {
  connectionId: string;
  displayName: string;
  state: ConnectionState;
  toolCount: number;
  tools: string[];
}

export interface ConnectorsBoardProps {
  registryLinked: boolean;
  connectors: ConnectorCard[];
  customConnectors: CustomConnectorCard[];
  connect: (systemId: string, secretValue: string) => Promise<ConnectorActionResult>;
  addCustom: (
    displayName: string,
    url: string,
    bearerToken: string,
  ) => Promise<ConnectorActionResult>;
  reattach: (connectionId: string, secretValue: string) => Promise<ConnectorActionResult>;
  checkHealth: (connectionId: string) => Promise<ConnectorActionResult>;
}

const STATE_LABEL: Record<ConnectionState, string> = {
  connected: "Connected",
  credentials_pending: "Credentials pending",
  needs_reauth: "Needs re-auth",
  not_connected: "Not connected",
};

/** What each provider does for an Agent, in one line under the name. */
const PROVIDER_BLURB: Record<string, string> = {
  slack: "Read channels and messages, and post as this organization's app.",
  google: "List Drive files, read spreadsheets, and append rows.",
  github: "Read issues and files, and open issues in your repositories.",
  notion: "Search shared pages, read them, and create new ones.",
};

/** Provider-specific paste instructions, in plain language. */
const PROVIDER_HELP: Record<string, { label: string; placeholder: string; steps: string[] }> = {
  google: {
    label: "Service account key (JSON)",
    placeholder: '{ "type": "service_account", ... }',
    steps: [
      "In your Google Cloud console, create a service account and download its JSON key.",
      "Turn on the Drive and Sheets APIs for that project.",
      "Share the Drive folders and spreadsheets this organization works in with the service account's email address.",
    ],
  },
  slack: {
    label: "Bot token",
    placeholder: "xoxb-...",
    steps: [
      "Create a Slack app for your workspace and install it.",
      "Give it the channel read and write scopes your Agents need.",
      "Paste the bot token that starts with xoxb.",
    ],
  },
  github: {
    label: "Personal access token",
    placeholder: "github_pat_...",
    steps: [
      "In GitHub, create a fine-grained personal access token.",
      "Grant it read access to the repositories your Agents work in, and issues write access if they should open issues.",
      "Paste the token here.",
    ],
  },
  notion: {
    label: "Internal integration secret",
    placeholder: "ntn_...",
    steps: [
      "In Notion, create an internal integration for your workspace.",
      "Share the pages and databases your Agents should reach with that integration.",
      "Paste the integration secret here.",
    ],
  },
};

function stateChip(state: ConnectionState) {
  const tone = state === "connected" ? "pass" : state === "not_connected" ? "neutral" : "hold";
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
  onSubmit: (value: string) => Promise<ConnectorActionResult>;
}) {
  const router = useRouter();
  const [value, setValue] = useState("");
  const [result, setResult] = useState<ConnectorActionResult | null>(null);
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

function HealthCheck({
  connectionId,
  checkHealth,
}: {
  connectionId: string;
  checkHealth: (connectionId: string) => Promise<ConnectorActionResult>;
}) {
  const router = useRouter();
  const [result, setResult] = useState<ConnectorActionResult | null>(null);
  const [pending, startTransition] = useTransition();
  return (
    <>
      <Button
        variant="secondary"
        pending={pending}
        onClick={() =>
          startTransition(async () => {
            const outcome = await checkHealth(connectionId);
            setResult(outcome);
            router.refresh();
          })
        }
      >
        Check health
      </Button>
      {result && (
        <p className={`text-xs ${result.ok ? "text-pass-text" : "text-fail"}`} role="status">
          {result.message}
        </p>
      )}
    </>
  );
}

export function ConnectorsBoard({
  registryLinked,
  connectors,
  customConnectors,
  connect,
  addCustom,
  reattach,
  checkHealth,
}: ConnectorsBoardProps) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [openConnector, setOpenConnector] = useState<string | null>(null);
  const [showCustomForm, setShowCustomForm] = useState(false);
  const [customName, setCustomName] = useState("");
  const [customUrl, setCustomUrl] = useState("");
  const [customToken, setCustomToken] = useState("");
  const [customResult, setCustomResult] = useState<ConnectorActionResult | null>(null);
  const [customPending, startCustom] = useTransition();

  const needle = query.trim().toLowerCase();
  const matches = (name: string) => !needle || name.toLowerCase().includes(needle);

  const connected = useMemo(
    () => connectors.filter((connector) => connector.state !== "not_connected" && matches(connector.displayName)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [connectors, needle],
  );
  const available = useMemo(
    () => connectors.filter((connector) => connector.state === "not_connected" && matches(connector.displayName)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [connectors, needle],
  );
  const visibleCustom = customConnectors.filter((connector) => matches(connector.displayName));
  const nothingMatches =
    needle.length > 0 && connected.length === 0 && available.length === 0 && visibleCustom.length === 0;

  if (!registryLinked) {
    return (
      <div className="rounded-lg border border-line bg-card p-6 text-sm text-muted">
        This deployment is not linked to the connections registry, so connectors cannot be
        managed here yet.
      </div>
    );
  }

  function submitCustom() {
    startCustom(async () => {
      const outcome = await addCustom(
        customName,
        customUrl,
        customToken,
      );
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

  function connectorRow(connector: ConnectorCard) {
    const help =
      connector.credential ??
      (connector.provider ? PROVIDER_HELP[connector.provider] : undefined);
    const blurb =
      connector.blurb ?? (connector.provider ? PROVIDER_BLURB[connector.provider] : undefined);
    const open = openConnector === connector.systemId;
    const isConnected = connector.state !== "not_connected";
    return (
      <li key={connector.systemId} className="rounded-lg border border-line bg-card p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-sm font-medium text-ink">{connector.displayName}</p>
            <p className="mt-0.5 text-xs text-muted">
              {isConnected
                ? `${connector.toolCount} ${connector.toolCount === 1 ? "action" : "actions"}`
                : blurb ?? "Agents can use this once it is connected."}
            </p>
          </div>
          <div className="flex items-center gap-2">
            {stateChip(connector.state)}
            {isConnected && connector.connectionId && (
              <HealthCheck connectionId={connector.connectionId} checkHealth={checkHealth} />
            )}
            {connector.oauth && (
              <a
                href={connector.oauth.href}
                className="rounded-sm bg-pine px-3 py-1.5 text-sm font-medium text-card-alt hover:bg-pine-deep"
              >
                {connector.oauth.label}
              </a>
            )}
            {help && (
              <Button
                variant="secondary"
                onClick={() => setOpenConnector(open ? null : connector.systemId)}
              >
                {connector.state === "needs_reauth"
                  ? "Reconnect"
                  : isConnected
                    ? "Replace credential"
                    : connector.oauth
                      ? "Paste a token instead"
                      : "Connect"}
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
              {connector.oauth
                ? `${connector.oauth.label} above is the quickest path; pasting a credential your admin controls works the same way.`
                : "Pasting a credential your admin controls is the server-to-server way."}
            </p>
            <CredentialForm
              label={help.label}
              placeholder={help.placeholder}
              multiline={
                (help as { multiline?: boolean }).multiline ?? connector.provider === "google"
              }
              pendingLabel={isConnected ? "Replace" : "Connect"}
              onSubmit={(value) =>
                connector.connectionId && isConnected
                  ? reattach(connector.connectionId, value)
                  : connect(connector.systemId, value)
              }
            />
          </div>
        )}
      </li>
    );
  }

  return (
    <div className="space-y-8">
      <div>
        <label htmlFor="connector-search" className="sr-only">
          Search connectors
        </label>
        <input
          id="connector-search"
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search connectors"
          className="w-full rounded-md border border-line bg-card px-3 py-2 text-sm text-ink"
        />
      </div>
      {nothingMatches && (
        <p className="rounded-lg border border-line bg-card p-4 text-sm text-muted" role="status">
          No connector matches that search.
        </p>
      )}

      {(connected.length > 0 || visibleCustom.length > 0) && (
        <section>
          <h2 className="font-display text-xl text-ink">Connected</h2>
          <p className="mt-1 text-sm text-muted">
            What this organization already reaches, and whether each connection is healthy.
          </p>
          <ul className="m-0 mt-3 list-none space-y-3 p-0">
            {connected.map(connectorRow)}
            {visibleCustom.map((connector) => (
              <li key={connector.connectionId} className="rounded-lg border border-line bg-card p-5">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <p className="text-sm font-medium text-ink">{connector.displayName}</p>
                    <p className="mt-0.5 text-xs text-muted">
                      {connector.tools.length > 0 ? connector.tools.join(" · ") : "No actions declared"}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    {stateChip(connector.state)}
                    <HealthCheck connectionId={connector.connectionId} checkHealth={checkHealth} />
                  </div>
                </div>
                {connector.state === "needs_reauth" && (
                  <CredentialForm
                    label="Bearer token"
                    placeholder="Paste the new token"
                    multiline={false}
                    pendingLabel="Reconnect"
                    onSubmit={(value) => reattach(connector.connectionId, value)}
                  />
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {available.length > 0 && (
        <section>
          <h2 className="font-display text-xl text-ink">Available</h2>
          <p className="mt-1 text-sm text-muted">
            Services the platform can reach as soon as this organization connects them.
          </p>
          <ul className="m-0 mt-3 list-none space-y-3 p-0">{available.map(connectorRow)}</ul>
        </section>
      )}

      <section>
        <div className="flex items-center justify-between">
          <h2 className="font-display text-xl text-ink">Custom connectors</h2>
          <Button variant="secondary" onClick={() => setShowCustomForm((current) => !current)}>
            Add custom connector
          </Button>
        </div>
        <p className="mt-1 text-sm text-muted">
          A tool server your team runs, spoken to over the open tool protocol. Registering checks
          the server answers and records the actions it offers.
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
        {visibleCustom.length === 0 && !showCustomForm && customConnectors.length === 0 && (
          <p className="mt-3 rounded-lg border border-line bg-card p-4 text-sm text-muted">
            No custom connectors yet.
          </p>
        )}
      </section>
    </div>
  );
}
