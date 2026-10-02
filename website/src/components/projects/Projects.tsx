"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useId, useRef, useState, useSyncExternalStore, type FormEvent, type ReactNode } from "react";
import { AppShell, PageHeader, type Crumb } from "@/components/configurations/AppShell";
import { Missing } from "@/components/configurations/Badges";
import { Icon } from "@/components/configurations/Icons";
import { Notice } from "@/components/configurations/Notice";
import { Modal } from "@/components/configurations/Overlay";
import { useSession } from "@/components/configurations/Session";
import { EmptyState, LoadingState } from "@/components/configurations/States";
import { Card, Facts } from "@/components/configurations/Tiles";
import { useWorkspace } from "@/lib/configurations/client";
import { entryState, serverEntryState, setEntryState, subscribeEntry } from "@/lib/configurations/entry";
import { fmtCount } from "@/lib/configurations/format";
import { routes } from "@/lib/configurations/routes";
import { notifySessionExpired } from "@/lib/configurations/session-events";
import { ApiError, errorText, MutationAttempts, type Application, type Device, type Project, type Robot } from "@/lib/platform/client";
import { useProjectResource, type Fleet, type RobotProfile } from "@/lib/projects/client";
import { ComputerDetails, ConnectionSetup } from "./ConnectionSetup";
import { ProjectFleetDirectory } from "./ProjectFleetDirectory";
import { engineLabel, projectCrumbs, projectHref, ProjectTabs, robotHref, sections, type Section } from "./ProjectNavigation";
import { RobotConfigurationSummary, useProjectRobotConfigurations, type ProjectRobotConfigurationCatalogue } from "./ProjectRobotConfigurations";
import { ProjectConnectionTools, ProjectOverview, ProjectRuns, ProjectSimulations } from "./ProjectWorkspace";
import { ProjectConfigurationsSection } from "./SavedProjectConfigurations";
import { SimulationAssets } from "./SimulationAssets";
import { SimulatorReadiness } from "./SimulatorReadiness";

/** A Projects page: the shared top bar with its trail. No sidebar: sections are tabs under the title. */
export function ProjectShell({ crumbs, children }: { crumbs: readonly Crumb[]; children: ReactNode }) {
  return <AppShell crumbs={crumbs} area="projects">{children}</AppShell>;
}

export function useMutation(refresh: () => void) {
  const attempts = useRef(new MutationAttempts());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const locked = useRef(false);
  async function submit<T>(path: string, body: unknown): Promise<T | undefined> {
    if (locked.current) return;
    locked.current = true; setBusy(true); setError(undefined);
    try {
      const result = await attempts.current.submit<T>(path, body);
      refresh(); return result;
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 401) notifySessionExpired();
      setError(errorText(cause));
    } finally { locked.current = false; setBusy(false); }
  }
  return { submit, busy, error };
}

/**
 * Projects (`/app/projects`): one equal card per project; New project opens a dialog.
 * Reached through `/app`, it goes on to Configurations when the account has saved ones.
 */
export function ProjectsIndex() {
  const router = useRouter();
  const { operator } = useSession();
  const ws = useWorkspace();
  const entry = useSyncExternalStore(subscribeEntry, entryState, serverEntryState);
  const ready = ws.status === "ready";
  const saved = ready && ws.source === "document" && ws.workspace.configurations.length > 0;
  useEffect(() => {
    if (entry !== "pending" || !ready) return;
    if (saved) { setEntryState("leaving"); router.replace(routes.index()); } else setEntryState("settled");
  }, [entry, ready, saved, router]);
  // Once this page is left, a later visit to Projects in this page load stays here.
  useEffect(() => () => { if (entryState() === "leaving") setEntryState("settled"); }, []);
  const [revision, setRevision] = useState(0);
  const projects = useProjectResource<Project[]>("projects", revision);
  const [creating, setCreating] = useState(false);
  const crumbs: Crumb[] = [{ label: "Projects" }];
  if (entry === "leaving" || (entry === "pending" && (!ready || saved))) return <ProjectShell crumbs={crumbs}><LoadingState /></ProjectShell>;
  const create = operator ? <button className="cv-btn cv-btn--primary" type="button" onClick={() => setCreating(true)}>New project</button> : undefined;
  return <ProjectShell crumbs={crumbs}>
    <PageHeader title="Projects" actions={create} />
    {projects.error && <Notice tone="error" action={<button className="cv-link" type="button" onClick={() => setRevision(n => n + 1)}>Retry</button>}>{projects.error}</Notice>}
    {!projects.data && !projects.error && <LoadingState />}
    {projects.data?.length === 0 && <EmptyState title="No projects yet." />}
    {!!projects.data?.length && <section className="cv-grid" aria-label="Your projects">{projects.data.map(project => <ProjectCard key={project.id} project={project} />)}</section>}
    {creating && <NameDialog<Project> title="New project" label="Project name" action="Create project" busyAction="Creating…" path="projects" body={name => ({ name })}
      onClose={() => setCreating(false)} onSaved={project => router.push(projectHref(project.id))} />}
  </ProjectShell>;
}

/** A project's counts, read for its card. */
function ProjectCard({ project }: { project: Project }) {
  const scope = `project_id=${encodeURIComponent(project.id)}`;
  const robots = useProjectResource<Robot[]>(`robots?${scope}`);
  const fleets = useProjectResource<Fleet[]>(`fleets?${scope}`);
  const applications = useProjectResource<Application[]>(`applications?${scope}`);
  const profiles = useProjectResource<RobotProfile[]>(`robot-profiles?${scope}`);
  const count = (items?: unknown[]) => items ? fmtCount(items.length) : <Missing />;
  const simulated = robots.data?.filter(robot => robot.simulated).length ?? 0;
  return <Link className="cv-config" href={projectHref(project.id)}>
    <div className="cv-config__head"><h2>{project.name}</h2></div>
    <dl className="cv-config__facts cv-config__facts--wide">
      <div><dt>Robots</dt><dd>{robots.data ? `${fmtCount(robots.data.length)}${simulated ? ` · ${simulated} simulated` : ""}` : <Missing />}</dd></div>
      <div><dt>Fleets</dt><dd>{count(fleets.data)}</dd></div>
      <div><dt>Configurations</dt><dd>{count(applications.data)}</dd></div>
      <div><dt>Profiles</dt><dd>{count(profiles.data)}</dd></div>
    </dl>
  </Link>;
}

/** One name, then create (a project, a fleet). */
function NameDialog<T>({ title, label, action, busyAction, path, body, onClose, onSaved }: {
  title: string; label: string; action: string; busyAction: string; path: string; body: (name: string) => unknown; onClose: () => void; onSaved: (result: T) => void;
}) {
  const id = useId();
  const [name, setName] = useState("");
  const mutation = useMutation(() => undefined);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) return;
    const result = await mutation.submit<T>(path, body(name.trim()));
    if (result) onSaved(result);
  }
  return <Modal open onClose={onClose} title={title} footer={<>
    <button className="cv-btn cv-btn--secondary" type="button" onClick={onClose}>Cancel</button>
    <button className="cv-btn cv-btn--primary" type="submit" form={`${id}-form`} disabled={mutation.busy || !name.trim()}>{mutation.busy ? busyAction : action}</button>
  </>}>
    <form id={`${id}-form`} className="cv-form cv-form--dialog" aria-label={action} onSubmit={event => void submit(event)}>
      <label className="cv-field">{label}<input className="cv-input" required maxLength={120} value={name} onChange={event => setName(event.target.value)} data-autofocus="" /></label>
      {mutation.error && <p className="cv-form-error" role="alert">{mutation.error}</p>}
    </form>
  </Modal>;
}

/** A project (`/app/projects/[id]`): its title and one primary action for the section, then the sections as tabs. */
export function ProjectPage({ projectId }: { projectId: string }) {
  const session = useSession();
  const ws = useWorkspace();
  const router = useRouter();
  const query = useSearchParams();
  const [revision, setRevision] = useState(0);
  const refresh = () => setRevision(n => n + 1);
  const projects = useProjectResource<Project[]>("projects", revision);
  const robots = useProjectResource<Robot[]>(`robots?project_id=${projectId}`, revision, true);
  const profiles = useProjectResource<RobotProfile[]>(`robot-profiles?project_id=${projectId}`, revision);
  const fleets = useProjectResource<Fleet[]>(`fleets?project_id=${projectId}`, revision);
  const devices = useProjectResource<Device[]>("robot-connections", revision);
  const catalogue = useProjectRobotConfigurations(projectId, revision);
  const project = projects.data?.find(p => p.id === projectId);
  const tab: Section = sections.find(s => s.toLowerCase() === query.get("section")) ?? "Overview";
  const [dialog, setDialog] = useState<"profile" | "fleet" | null>(null);
  const [registering, setRegistering] = useState<{ source?: Robot } | null>(null);
  const saved = ws.source === "document" ? ws.workspace?.configurations.filter(config => config.projectId === projectId) ?? [] : [];
  const errors = [...new Set([projects.error, robots.error, profiles.error, fleets.error, devices.error].filter((error): error is string => !!error))];
  function register(source?: Robot) {
    setRegistering({ source });
    if (tab !== "Robots") router.push(projectHref(projectId, "Robots"));
  }
  const button = (label: string, onClick: () => void) => <button className="cv-btn cv-btn--primary" type="button" onClick={onClick}>{label}</button>;
  // One primary action per section; Configurations and Runs put theirs (if any) beside their groups.
  const primary = !session.operator || !project || tab === "Configurations" || tab === "Runs" ? undefined
    : tab === "Profiles" ? button("Import profile", () => setDialog("profile"))
      : tab === "Fleets" ? button("New fleet", () => setDialog("fleet")) : button("Add robot", () => register());
  const counts = {
    Robots: robots.data?.length, Profiles: profiles.data?.length, Fleets: fleets.data?.length,
    Configurations: catalogue.loading ? undefined : catalogue.applications.length + saved.length, Simulations: robots.data?.filter(robot => robot.simulated).length,
  };
  return <ProjectShell crumbs={projectCrumbs(null, { label: project?.name ?? "Project" })}>
    <PageHeader title={project?.name ?? "Project"} actions={<><button className="cv-btn cv-btn--secondary" type="button" onClick={refresh}>Refresh</button>{primary}</>} />
    {projects.data && !project && <Notice tone="error">This project is unavailable.</Notice>}
    {errors.map(error => <Notice key={error} tone="error">{error}</Notice>)}
    {project && <>
      <ProjectTabs projectId={projectId} selected={tab} counts={counts} />
      {tab === "Overview" && <ProjectOverview projectId={projectId} robots={robots.data} profiles={profiles.data} fleets={fleets.data} catalogue={catalogue} saved={saved.length} />}
      {tab === "Robots" && <ProjectRobots projectId={projectId} robots={robots.data} profiles={profiles.data} fleets={fleets.data} devices={devices.data} catalogue={catalogue}
        registering={registering} onRegister={register} onRegistered={() => { setRegistering(null); refresh(); }} onCancel={() => setRegistering(null)} refresh={refresh} />}
      {tab === "Profiles" && <ProjectProfiles profiles={profiles.data} />}
      {tab === "Fleets" && <ProjectFleets projectId={projectId} robots={robots.data} fleets={fleets.data} catalogue={catalogue} refresh={refresh} />}
      {tab === "Configurations" && <ProjectConfigurationsSection projectId={projectId} catalogue={catalogue} />}
      {tab === "Simulations" && <ProjectSimulations projectId={projectId} robots={robots.data} profiles={profiles.data} catalogue={catalogue} />}
      {tab === "Runs" && <ProjectRuns projectId={projectId} robots={robots.data} />}
    </>}
    {dialog === "profile" && <ProfileDialog projectId={projectId} profiles={profiles.data ?? []} onClose={() => setDialog(null)} onSaved={() => { setDialog(null); refresh(); }} />}
    {dialog === "fleet" && <NameDialog<Fleet> title="New fleet" label="Fleet name" action="Create fleet" busyAction="Creating…" path="fleets" body={name => ({ project_id: projectId, name })}
      onClose={() => setDialog(null)} onSaved={() => { setDialog(null); refresh(); }} />}
  </ProjectShell>;
}

/** Robots: the registration panel when adding one, the table, assigning a selection to a fleet, and the workspace device's tools. */
function ProjectRobots({ projectId, robots, profiles, fleets, devices, catalogue, registering, onRegister, onRegistered, onCancel, refresh }: {
  projectId: string; robots?: Robot[]; profiles?: RobotProfile[]; fleets?: Fleet[]; devices?: Device[]; catalogue: ProjectRobotConfigurationCatalogue;
  registering: { source?: Robot } | null; onRegister: (source?: Robot) => void; onRegistered: () => void; onCancel: () => void; refresh: () => void;
}) {
  const { operator } = useSession();
  const [selected, setSelected] = useState<string[]>([]);
  const [fleetId, setFleetId] = useState("");
  const mutation = useMutation(refresh);
  async function assign() {
    if (!robots) return;
    const result = await mutation.submit(`fleets/${fleetId}/members`, { assignments: selected.map(id => ({ robot_id: id, expected_fleet_id: robots.find(robot => robot.id === id)?.fleet_id ?? null })) });
    if (result) setSelected([]);
  }
  const source = registering?.source;
  return <section aria-label="Robots">
    {operator && registering && profiles && devices && <RegistrationForm key={source?.id ?? "new"} projectId={projectId} profiles={profiles} fleets={fleets ?? []}
      devices={devices.filter(device => !robots?.some(robot => robot.device_id === device.id))} source={source} onCancel={onCancel} onSaved={onRegistered} />}
    {!robots ? <LoadingState />
      : !robots.length ? !registering && <EmptyState title="No robots yet." action={operator && profiles?.length === 0 && <Link className="cv-btn cv-btn--secondary" href={projectHref(projectId, "Profiles")}>Import a profile first</Link>} />
        : <Card flush><div className="cv-table-wrap"><table className="cv-table cv-table--rich" aria-label="Robots">
          <thead><tr>{operator && <th scope="col" className="cv-table__select"><span className="cv-sr">Select</span></th>}<th scope="col">Robot</th><th scope="col">Configuration</th><th scope="col">Fleet</th><th scope="col">Profile</th></tr></thead>
          <tbody>{robots.map(robot => {
            const profile = profiles?.find(item => item.id === robot.profile_id);
            return <tr key={robot.id}>
              {operator && <td className="cv-table__select"><input type="checkbox" aria-label={`Select ${robot.name}`} checked={selected.includes(robot.id)} onChange={event => setSelected(items => event.target.checked ? [...items, robot.id] : items.filter(id => id !== robot.id))} /></td>}
              <th scope="row"><Link className="cv-cell-link" href={robotHref(projectId, robot.id)}>{robot.name}</Link><span className="project-row-meta">{robot.simulated === false ? "Physical" : "Simulated"}</span></th>
              <td data-label="Configuration"><RobotConfigurationSummary projectId={projectId} robot={robot} catalogue={catalogue} compact /></td>
              <td data-label="Fleet">{fleets?.find(fleet => fleet.id === robot.fleet_id)?.name ?? <span className="cv-muted">Unassigned</span>}</td>
              <td data-label="Profile">{profile ? <span>{profile.name} · revision {profile.revision}</span> : <span className="cv-mono">{robot.profile}</span>}
                <span className="project-row-meta">{robot.source_robot_id ? `From ${robots.find(item => item.id === robot.source_robot_id)?.name ?? "physical robot"}` : robot.simulated ? engineLabel(robot.simulation_engine) ?? "Existing runtime" : "Physical robot"}</span>
                {robot.simulated === false && profile && operator && <button className="cv-link" type="button" disabled={!profile.simulation.engines.length} onClick={() => onRegister(robot)}>Create simulated instance</button>}
                {robot.simulated && robot.profile_id && <SimulatorReadiness robot={robot} busy={mutation.busy} canWrite={operator} verify={() => void mutation.submit(`robots/${robot.id}/qualification`, {})} />}
              </td>
            </tr>;
          })}</tbody>
        </table></div></Card>}
    {operator && selected.length > 0 && <div className="cv-toolbar">
      <label className="cv-field cv-field--inline">Assign selected robots to fleet<select aria-label="Assign selected robots to fleet" className="cv-input" value={fleetId} onChange={event => setFleetId(event.target.value)}><option value="">Choose fleet</option>{fleets?.map(fleet => <option key={fleet.id} value={fleet.id}>{fleet.name}</option>)}</select></label>
      <button className="cv-btn cv-btn--secondary" type="button" disabled={mutation.busy || !fleetId} onClick={() => void assign()}>Assign {selected.length} robots</button>
    </div>}
    {mutation.error && <Notice tone="error">{mutation.error}</Notice>}
    <ProjectConnectionTools />
  </section>;
}

/** Profiles: one card per revision, its simulation files and its specification. */
function ProjectProfiles({ profiles }: { profiles?: RobotProfile[] }) {
  if (!profiles) return <LoadingState />;
  if (!profiles.length) return <EmptyState title="No profiles yet." text="A profile describes a robot’s joints, sensors and simulation model." />;
  return <div className="cv-stack">{profiles.map(profile => <Card key={profile.id} title={`${profile.name} · revision ${profile.revision}`}>
    <Facts items={[
      { label: "Embodiment", value: profile.spec.embodiment },
      { label: "Control rate", value: `${profile.spec.control_rate_hz} Hz` },
      { label: "Joints", value: fmtCount(profile.spec.joints.length) },
      { label: "Sensors", value: fmtCount(profile.spec.sensors.length) },
      { label: "Simulation", value: profile.simulation.engines.map(engine => engineLabel(engine)).join(", ") || "Not declared" },
      { label: "Dynamics", value: profile.simulation.dynamics_source },
    ]} />
    <SimulationAssets profile={profile} />
    <details className="cv-disclosure"><summary>Profile specification</summary><pre className="cv-code">{JSON.stringify(profile.spec, null, 2)}</pre></details>
  </Card>)}</div>;
}

/** Fleets: each fleet and its robots, and the robots in none. */
function ProjectFleets({ projectId, robots, fleets, catalogue, refresh }: { projectId: string; robots?: Robot[]; fleets?: Fleet[]; catalogue: ProjectRobotConfigurationCatalogue; refresh: () => void }) {
  const { operator } = useSession();
  const mutation = useMutation(refresh);
  return <>
    {mutation.error && <Notice tone="error">{mutation.error}</Notice>}
    <ProjectFleetDirectory projectId={projectId} fleets={fleets} robots={robots} catalogue={catalogue} busy={mutation.busy}
      onRemove={operator ? (fleetId, robotId) => void mutation.submit(`fleets/${fleetId}/members/${robotId}/remove`, {}) : undefined} />
  </>;
}

function ProfileDialog({ projectId, profiles, onClose, onSaved }: { projectId: string; profiles: RobotProfile[]; onClose: () => void; onSaved: () => void }) {
  const id = useId();
  const [name, setName] = useState("");
  const [spec, setSpec] = useState<unknown>();
  const [fileError, setFileError] = useState<string>();
  const mutation = useMutation(() => undefined);
  async function load(file?: File) {
    setSpec(undefined); setFileError(undefined);
    if (!file) return;
    try {
      if (file.size > 128 * 1024) throw new Error("Use a profile JSON file smaller than 128 KiB. Model files are uploaded separately.");
      setSpec(JSON.parse(await file.text()));
    } catch (cause) { setFileError(errorText(cause)); }
  }
  const latest = Math.max(0, ...profiles.filter(profile => profile.name === name.trim()).map(profile => profile.revision));
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (await mutation.submit("robot-profiles", { project_id: projectId, name: name.trim(), expected_revision: latest, spec })) onSaved();
  }
  return <Modal open onClose={onClose} title="Import profile" footer={<>
    <button className="cv-btn cv-btn--secondary" type="button" onClick={onClose}>Cancel</button>
    <button className="cv-btn cv-btn--primary" type="submit" form={`${id}-form`} disabled={mutation.busy || !name.trim() || !spec}>{mutation.busy ? "Saving…" : "Save profile revision"}</button>
  </>}>
    <form id={`${id}-form`} className="cv-form cv-form--dialog" aria-label="Import robot profile" onSubmit={event => void submit(event)}>
      <label className="cv-field">Profile name<input className="cv-input" required maxLength={120} value={name} onChange={event => setName(event.target.value)} data-autofocus="" /></label>
      <label className="cv-field">Profile JSON<input className="cv-file-input" type="file" accept=".json,application/json" onChange={event => void load(event.target.files?.[0])} /></label>
      <p className="cv-muted">{latest > 0 ? `Saves revision ${latest + 1}. Robots keep their pinned revision.` : <a className="cv-link" href="/robot-profile-template.json" download>Download a template</a>}</p>
      {(fileError || mutation.error) && <p className="cv-form-error" role="alert">{fileError ?? mutation.error}</p>}
    </form>
  </Modal>;
}

/** Register a robot (or a simulated instance of a physical one) to a profile and an enrolled computer. */
function RegistrationForm({ projectId, profiles, devices, fleets, source, onSaved, onCancel }: { projectId: string; profiles: RobotProfile[]; devices: Device[]; fleets: Fleet[]; source?: Robot; onSaved: () => void; onCancel: () => void }) {
  const [name, setName] = useState(source ? `${source.name} · simulation` : "");
  const [kind, setKind] = useState(source ? "simulated" : "physical");
  const [profileId, setProfileId] = useState(source?.profile_id ?? "");
  const [deviceId, setDeviceId] = useState("");
  const [connected, setConnected] = useState<Device>();
  const [engine, setEngine] = useState("");
  const [fleetId, setFleetId] = useState("");
  const profile = profiles.find(item => item.id === profileId);
  const available = [...devices, ...(connected && !devices.some(device => device.id === connected.id) ? [connected] : [])].filter(device => device.simulated === (kind === "simulated"));
  const mutation = useMutation(onSaved);
  const title = source ? `Simulate ${source.name}` : "Register a robot";
  const computer = kind === "simulated" ? "Enrolled simulator runner" : "Enrolled robot computer";
  async function submit(event: FormEvent) {
    event.preventDefault();
    await mutation.submit("robot-registrations", { project_id: projectId, name: name.trim(), kind, device_id: deviceId, profile_id: profileId, simulation_engine: kind === "simulated" ? engine : null, source_robot_id: source?.id ?? null, fleet_id: fleetId || null });
  }
  return <section className="cv-card cv-panel" aria-label={title}>
    <div className="cv-card__head"><h2>{title}</h2><button className="cv-icon-btn" type="button" aria-label="Cancel registration" onClick={onCancel}><Icon name="close" /></button></div>
    <form className="cv-form cv-form--panel" onSubmit={event => void submit(event)}>
      <div className="cv-form__grid">
        <label className="cv-field">Robot name<input className="cv-input" required maxLength={120} value={name} onChange={event => setName(event.target.value)} /></label>
        <label className="cv-field">Robot type<select aria-label="Robot type" className="cv-input" disabled={!!source} value={kind} onChange={event => { setKind(event.target.value); setDeviceId(""); setEngine(""); }}><option value="physical">Physical robot</option><option value="simulated">Simulated robot</option></select></label>
        <label className="cv-field">Robot profile<select aria-label="Robot profile" className="cv-input" required disabled={!!source} value={profileId} onChange={event => { setProfileId(event.target.value); setEngine(""); }}><option value="">Choose a profile revision</option>{profiles.map(item => <option key={item.id} value={item.id}>{item.name} · revision {item.revision}</option>)}</select></label>
        {kind === "simulated" && <label className="cv-field">Simulation engine<select aria-label="Simulation engine" className="cv-input" required value={engine} onChange={event => setEngine(event.target.value)}><option value="">Choose engine</option>{profile?.simulation.engines.map(item => <option key={item} value={item}>{engineLabel(item)}</option>)}</select></label>}
        <label className="cv-field">{computer}<select aria-label={computer} className="cv-input" required value={deviceId} onChange={event => setDeviceId(event.target.value)}><option value="">Choose a connection</option>{available.map(device => <option key={device.id} value={device.id}>{device.name} · {device.status === "never_seen" ? "awaiting heartbeat" : device.status}</option>)}</select></label>
        <label className="cv-field">Fleet (optional)<select aria-label="Fleet (optional)" className="cv-input" value={fleetId} onChange={event => setFleetId(event.target.value)}><option value="">Unassigned</option>{fleets.map(fleet => <option key={fleet.id} value={fleet.id}>{fleet.name}</option>)}</select></label>
      </div>
      {available.length === 0 && <p className="cv-muted">No unassigned connections yet. Connect a computer below.</p>}
      <ConnectionSetup key={kind} projectId={projectId} name={name.trim()} simulated={kind === "simulated"} onConnected={device => { setConnected(device); setDeviceId(device.id); }} />
      <ComputerDetails deviceId={deviceId} />
      {mutation.error && <p className="cv-form-error" role="alert">{mutation.error}</p>}
      <div className="cv-form__foot">
        <span className="cv-muted">Registering does not start motion.</span>
        <button className="cv-btn cv-btn--secondary" type="button" onClick={onCancel}>Cancel</button>
        <button className="cv-btn cv-btn--primary" disabled={mutation.busy || !name.trim() || !profileId || !deviceId || (kind === "simulated" && !engine)}>Register robot</button>
      </div>
    </form>
  </section>;
}
