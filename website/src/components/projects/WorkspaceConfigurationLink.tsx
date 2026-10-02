"use client";

import Link from "next/link";
import { useState } from "react";
import { Badge } from "@/components/configurations/Badges";
import { Modal } from "@/components/configurations/Overlay";
import { useSession } from "@/components/configurations/Session";
import { useWorkspace } from "@/lib/configurations/client";
import { notifySessionExpired } from "@/lib/configurations/session-events";
import { ApiError, MutationAttempts, errorText, type Application } from "@/lib/platform/client";
import { useProjectResource, useRunnableConfigurations, type RunnableConfiguration } from "@/lib/projects/client";
import { configurationHref } from "./ProjectConfigurations";

interface WorkspaceLink {
  id: string; configuration_id: string; source_name: string; source_revision: number;
  document_revision: number; source_state: "unchanged" | "changed" | "missing" | "unsupported";
  application: Application; project_name: string;
}

/** A link whose saved configuration no longer matches it, in a few words (releases and deployments are never changed by it). */
const SOURCE_STATE: Record<Exclude<WorkspaceLink["source_state"], "unchanged">, string> = {
  changed: "Changed since linking", missing: "Saved configuration removed", unsupported: "Unsupported format",
};

/**
 * The saved configuration's link to a runnable project configuration, as one row (Details
 * tab): the linked configuration with Open, Change and Unlink, or Link. Hidden for the
 * sample, and when there is no link and no runnable configuration to link to.
 */
export function WorkspaceConfigurationLink({ configurationId }: { configurationId: string }) {
  const ws = useWorkspace();
  const { operator } = useSession();
  const saved = ws.source === "document" && ws.documentRevision !== null;
  const [revision, setRevision] = useState(0);
  const [attempts] = useState(() => new MutationAttempts());
  const [dialog, setDialog] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const links = useProjectResource<WorkspaceLink[]>(saved ? `workspace-configuration-links?configuration_id=${encodeURIComponent(configurationId)}` : null, revision + (ws.documentRevision ?? 0));
  const runnable = useRunnableConfigurations(saved && operator, revision);
  const link = links.data?.[0];
  async function mutate(path: string, body: unknown): Promise<boolean> {
    setBusy(true); setError(undefined);
    try { await attempts.submit(path, body); return true; }
    catch (cause) { if (cause instanceof ApiError && cause.status === 401) notifySessionExpired(); else setError(errorText(cause)); return false; }
    finally { setBusy(false); setRevision(n => n + 1); }
  }
  async function save(applicationId: string) {
    if (await mutate("workspace-configuration-links", { configuration_id: configurationId, application_id: applicationId, document_revision: ws.documentRevision, expected_link_id: link?.id ?? null })) setDialog(false);
  }
  if (!saved || !links.data || links.error || (!link && !runnable.items?.length)) return null;
  const disabled = busy || ws.saving;
  return <section className="cv-card cv-linkrow" aria-label="Project configuration">
    <span className="cv-linkrow__label">Project configuration</span>
    <span className="cv-linkrow__value">
      {link ? <><span className="cv-linkrow__name">{link.application.name}<span className="cv-muted"> · {link.project_name}</span></span>{link.source_state !== "unchanged" && <Badge tone="warning">{SOURCE_STATE[link.source_state]}</Badge>}</>
        : <span className="cv-muted">Not linked</span>}
    </span>
    <span className="cv-linkrow__actions">
      {link && <Link className="cv-link" href={configurationHref(link.application.id)}>Open</Link>}
      {operator && runnable.items && <button className="cv-link" type="button" disabled={disabled} onClick={() => { setError(undefined); setDialog(true); }}>{link ? "Change" : "Link"}</button>}
      {operator && link && <button className="cv-link" type="button" disabled={disabled} onClick={() => void mutate(`workspace-configuration-links/${link.id}/remove`, {})}>Unlink</button>}
    </span>
    {error && !dialog && <p className="cv-linkrow__error" role="alert">{error}</p>}
    {dialog && runnable.items && <LinkDialog options={runnable.items} initial={link?.application.id} busy={disabled} error={error} onSave={id => void save(id)} onClose={() => setDialog(false)} />}
  </section>;
}

/** Choose the runnable configuration, grouped by project. One option is chosen for you. */
function LinkDialog({ options, initial, busy, error, onSave, onClose }: { options: RunnableConfiguration[]; initial?: string; busy: boolean; error?: string; onSave: (id: string) => void; onClose: () => void }) {
  const [choice, setChoice] = useState(initial ?? (options.length === 1 ? options[0].application.id : ""));
  const projects = [...new Map(options.map(item => [item.project.id, item.project])).values()];
  return <Modal open onClose={onClose} title="Link to a project configuration"
    footer={<>
      <button className="cv-btn cv-btn--secondary" type="button" onClick={onClose}>Cancel</button>
      <button className="cv-btn cv-btn--primary" type="button" disabled={busy || !choice} onClick={() => onSave(choice)}>{busy ? "Linking…" : "Link"}</button>
    </>}>
    <form className="cv-form cv-form--dialog" onSubmit={event => { event.preventDefault(); if (choice && !busy) onSave(choice); }}>
      <label className="cv-field">Project configuration
        <select className="cv-input" value={choice} disabled={busy} onChange={event => setChoice(event.target.value)} data-autofocus="">
          <option value="">Choose a configuration</option>
          {projects.map(project => <optgroup key={project.id} label={project.name}>
            {options.filter(item => item.project.id === project.id).map(item => <option key={item.application.id} value={item.application.id}>{item.application.name}</option>)}
          </optgroup>)}
        </select>
      </label>
      {error && <p className="cv-form-error" role="alert">{error}</p>}
    </form>
  </Modal>;
}

/** On a project configuration: the saved configurations linked to it, one row each. */
export function LinkedWorkspaceSpecifications({ applicationId }: { applicationId: string }) {
  const links = useProjectResource<WorkspaceLink[]>(`workspace-configuration-links?application_id=${encodeURIComponent(applicationId)}`);
  if (links.error || !links.data?.length) return null;
  return <section className="cv-row" aria-label="Linked saved configurations">
    <div className="cv-row__head"><h2>Saved configurations</h2></div>
    <div className="cv-card cv-card--flush"><ul className="cv-list">
      {links.data.map(link => <li key={link.id}>
        {link.source_state !== "missing" ? <Link className="cv-list__main" href={`/app/configurations/${encodeURIComponent(link.configuration_id)}`}>{link.source_name}</Link> : <span className="cv-list__main">{link.source_name}</span>}
        {link.source_state !== "unchanged" && <Badge tone="warning">{SOURCE_STATE[link.source_state]}</Badge>}
      </li>)}
    </ul></div>
  </section>;
}
