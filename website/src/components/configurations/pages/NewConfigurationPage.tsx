"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useId, useRef, useState } from "react";
import type { FormEvent } from "react";
import { useWorkspace } from "@/lib/configurations/client";
import { buildRevision, EDGE_MODELS, EMPTY_INPUT, HARDWARE, inputErrors, ROLE_LABEL, ROUTE_LABEL, routeMode } from "@/lib/configurations/create";
import type { NewConfigurationInput } from "@/lib/configurations/create";
import { createConfiguration } from "@/lib/configurations/mutations";
import { routes } from "@/lib/configurations/routes";
import { AppShell, PageHeader, type Crumb } from "../AppShell";
import { Notice, WorkspaceNotice } from "../Notice";
import { LoadingState } from "../States";

const CRUMBS: Crumb[] = [{ label: "Configurations", href: routes.index() }, { label: "New configuration" }];
const ROLES = ["planner", "policy"] as const;

/** New configuration (`/app/configurations/new`): one short form; Create saves revision r1 and opens its dashboard. */
export function NewConfigurationPage() {
  const ws = useWorkspace();
  if (ws.status === "loading") return <AppShell crumbs={CRUMBS}><LoadingState /></AppShell>;
  return <AppShell crumbs={CRUMBS}><NewConfigurationForm /></AppShell>;
}

function NewConfigurationForm() {
  const ws = useWorkspace();
  const router = useRouter();
  const id = useId();
  const [input, setInput] = useState<NewConfigurationInput>(EMPTY_INPUT);
  const [checked, setChecked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const form = useRef<HTMLFormElement>(null);
  // The sample is never saved: a new configuration starts the account's own workspace.
  const taken = ws.source === "document" ? ws.workspace?.configurations.map(config => config.name) ?? [] : [];
  const errors = inputErrors(input, taken);
  const shown = checked ? errors : {};
  const set = <K extends keyof NewConfigurationInput>(key: K, value: NewConfigurationInput[K]) => setInput(current => ({ ...current, [key]: value }));
  function edgeModel(name: string) {
    const known = EDGE_MODELS.find(model => model.name.toLowerCase() === name.trim().toLowerCase());
    setInput(current => ({ ...current, edgeModel: name, edgeRole: known && (known.role === "planner" || known.role === "policy") ? known.role : current.edgeRole }));
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setChecked(true);
    if (Object.keys(errors).length) {
      const first = errors.name ? "name" : errors.robot ? "robot" : "models";
      form.current?.querySelector<HTMLElement>(`#${CSS.escape(`${id}-${first}`)}`)?.focus();
      return;
    }
    setBusy(true);
    setError(null);
    const name = input.name.trim();
    const result = await ws.save(current => createConfiguration(current, { name, revision: buildRevision(input, Date.now()) }, Date.now()).workspace);
    if (!result.ok) { setBusy(false); setError(result.error); return; }
    const created = result.workspace.configurations.filter(config => config.name === name).at(-1);
    router.push(created ? routes.configuration(created.id) : routes.index());
  }

  const field = (key: "name" | "robot" | "models") => ({ "aria-invalid": shown[key] ? true as const : undefined, "aria-describedby": shown[key] ? `${id}-${key}-error` : undefined });
  const problem = (key: "name" | "robot" | "models") => shown[key] && <span className="cv-field__error" id={`${id}-${key}-error`}>{shown[key]}</span>;
  const route = input.edgeModel.trim() || input.cloudModel.trim() ? ROUTE_LABEL[routeMode(!!input.edgeModel.trim(), !!input.cloudModel.trim())] : null;

  return <>
    <PageHeader title="New configuration" />
    <WorkspaceNotice />
    <p>This form saves a workspace configuration document. To create an executable release for a registered robot, open <Link className="cv-link" href="/app/projects">your project’s Configurations tab</Link>.</p>
    <form ref={form} className="cv-form" onSubmit={event => void submit(event)} noValidate>
      <div className="cv-field">
        <label htmlFor={`${id}-name`}>Name</label>
        <input id={`${id}-name`} className="cv-input" type="text" value={input.name} maxLength={80} autoComplete="off" disabled={busy} onChange={event => set("name", event.target.value)} {...field("name")} />
        {problem("name")}
      </div>
      <div className="cv-field">
        <label htmlFor={`${id}-robot`}>Robot</label>
        <input id={`${id}-robot`} className="cv-input" type="text" value={input.robot} maxLength={80} autoComplete="off" disabled={busy} onChange={event => set("robot", event.target.value)} {...field("robot")} />
        {problem("robot")}
      </div>
      <label className="cv-field">Edge hardware
        <select className="cv-input" value={input.hardwareId} disabled={busy} onChange={event => set("hardwareId", event.target.value)}>
          {HARDWARE.map(item => <option key={item.id} value={item.id}>{item.hardware.name}</option>)}
        </select>
      </label>
      <div className="cv-form__pair">
        <label className="cv-field">Edge model
          <input id={`${id}-models`} className="cv-input" type="text" value={input.edgeModel} maxLength={120} list={`${id}-edge`} autoComplete="off" disabled={busy} onChange={event => edgeModel(event.target.value)} {...field("models")} />
          <datalist id={`${id}-edge`}>{EDGE_MODELS.map(model => <option key={model.name} value={model.name} />)}</datalist>
        </label>
        <label className="cv-field">Role
          <select className="cv-input" value={input.edgeRole} disabled={busy} onChange={event => set("edgeRole", event.target.value as NewConfigurationInput["edgeRole"])}>
            {ROLES.map(role => <option key={role} value={role}>{ROLE_LABEL[role]}</option>)}
          </select>
        </label>
      </div>
      <div className="cv-form__pair">
        <label className="cv-field">Cloud model
          <input className="cv-input" type="text" value={input.cloudModel} maxLength={120} autoComplete="off" disabled={busy} onChange={event => set("cloudModel", event.target.value)} {...field("models")} />
        </label>
        <label className="cv-field">Role
          <select className="cv-input" value={input.cloudRole} disabled={busy} onChange={event => set("cloudRole", event.target.value as NewConfigurationInput["cloudRole"])}>
            {ROLES.map(role => <option key={role} value={role}>{ROLE_LABEL[role]}</option>)}
          </select>
        </label>
      </div>
      {problem("models")}
      {error && <Notice tone="error">{error}</Notice>}
      <div className="cv-form__foot">
        <span className="cv-muted">{route && `Route: ${route}`}</span>
        <Link className="cv-btn cv-btn--secondary" href={routes.index()}>Cancel</Link>
        <button className="cv-btn cv-btn--primary" type="submit" disabled={busy || ws.canSave === false}>{busy ? "Creating…" : "Create"}</button>
      </div>
    </form>
  </>;
}
