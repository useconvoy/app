"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState, type FormEvent, type ReactNode } from "react";
import { ConfigurationsRoot, useSession } from "@/components/configurations/Session";
import { AppShell, PageHeader } from "@/components/configurations/AppShell";
import { useSearchParams } from "next/navigation";
import { sections, projectHref } from "./ProjectNavigation";
import { ProjectOverview, ProjectRuns, ProjectSimulations, ProjectConnectionTools } from "./ProjectWorkspace";
import { SavedProjectConfigurations } from "./SavedProjectConfigurations";
import { ProjectConfigurations } from "./ProjectConfigurations";
import { SimulatorReadiness } from "./SimulatorReadiness";
import { SimulationAssets } from "./SimulationAssets";
import { ComputerDetails, ConnectionSetup } from "./ConnectionSetup";
import { ApiError, errorText, MutationAttempts, type Project, type Robot, type Device } from "@/lib/platform/client";
import { notifySessionExpired } from "@/lib/configurations/session-events";
import { useProjectResource, type Fleet, type RobotProfile } from "@/lib/projects/client";

export function ProjectsRoot({ children }: { children: ReactNode }) {
  return <ConfigurationsRoot>{children}</ConfigurationsRoot>;
}

export function Shell({ project, projectId, section, navigation = true, children }: { project?: string; projectId?: string; section?: string; navigation?: boolean; children: ReactNode }) {
  return <AppShell crumbs={[{ label: project ?? "Projects" }]} projectId={projectId} section={section} navigation={navigation}>{children}</AppShell>;
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

export function ProjectsIndex() {
  const router = useRouter();
  const session = useSession();
  const [revision, setRevision] = useState(0);
  const projects = useProjectResource<Project[]>("projects", revision);
  const mutation = useMutation(() => setRevision(n => n + 1));
  const [name, setName] = useState("");
  async function create(event: FormEvent) {
    event.preventDefault();
    const project = await mutation.submit<Project>("projects", { name: name.trim() });
    if (project) router.push(`/app/projects/${project.id}`);
  }
  return <Shell>
    <PageHeader title="Projects" actions={<Link className="cv-btn cv-btn--secondary" href="/app/configurations">Organize saved configurations</Link>} />
    <p>Organize your robots, their physical profiles, and simulation instances in one project.</p>
    {projects.error && <p role="alert">{projects.error}</p>}
    {!projects.data && !projects.error && <p role="status">Loading projects…</p>}
    {projects.data?.length === 0 && <div className="cv-empty"><h2>Create your first project</h2><p>Then register a robot or its simulated counterpart.</p></div>}
    <div className="cv-grid">{projects.data?.map(project => <Link className="cv-card" key={project.id} href={`/app/projects/${project.id}`}><div><h2>{project.name}</h2><p>Open project →</p></div></Link>)}</div>
    {session.operator && <form className="cv-form project-create" onSubmit={create}>
      <label className="cv-field">Project name<input className="cv-input" required maxLength={120} value={name} onChange={e => setName(e.target.value)} /></label>
      {mutation.error && <p role="alert">{mutation.error}</p>}
      <button className="cv-btn cv-btn--primary" disabled={mutation.busy || !name.trim()}>{mutation.busy ? "Creating…" : "Create project"}</button>
    </form>}
  </Shell>;
}

export function ProjectPage({ projectId }: { projectId: string }) {
  const session = useSession();
  const [revision, setRevision] = useState(0);
  const refresh = () => setRevision(n => n + 1);
  const projects = useProjectResource<Project[]>("projects", revision);
  const robots = useProjectResource<Robot[]>(`robots?project_id=${projectId}`, revision, true);
  const profiles = useProjectResource<RobotProfile[]>(`robot-profiles?project_id=${projectId}`, revision);
  const fleets = useProjectResource<Fleet[]>(`fleets?project_id=${projectId}`, revision);
  const devices = useProjectResource<Device[]>("robot-connections", revision);
  const project = projects.data?.find(p => p.id === projectId);
  const query = useSearchParams();
  const router = useRouter();
  const tab = sections.find(s => s.toLowerCase() === query.get("section")) ?? "Overview";
  const setTab = (value: string) => router.push(projectHref(projectId, value));
  const [source, setSource] = useState<Robot>();
  const [adding, setAdding] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [fleetId, setFleetId] = useState("");
  const mutation = useMutation(refresh);
  const errors = [projects.error, robots.error, profiles.error, fleets.error, devices.error].filter(Boolean);
  async function assign() {
    if (!robots.data) return;
    const result = await mutation.submit(`fleets/${fleetId}/members`, { assignments: selected.map(id => ({ robot_id: id, expected_fleet_id: robots.data!.find(r => r.id === id)?.fleet_id ?? null })) });
    if (result) setSelected([]);
  }
  return <Shell project={project?.name ?? "Project"} projectId={projectId} section={tab}>
    <PageHeader title={project?.name ?? "Project"} actions={<><button type="button" className="cv-btn cv-btn--secondary" onClick={refresh}>Refresh</button>{session.operator && <button type="button" className="cv-btn cv-btn--primary" onClick={() => { setTab("Robots"); setSource(undefined); setAdding(true); }}>Add robot</button>}</>} />
    {projects.data && !project && <p role="alert">This project is unavailable.</p>}
    {errors.map((error, i) => <p role="alert" key={i}>{error}</p>)}
    {project && <>
      {tab === "Overview" && <ProjectOverview projectId={projectId} robots={robots.data} profiles={profiles.data} fleets={fleets.data} />}
      {tab === "Simulations" && <ProjectSimulations projectId={projectId} robots={robots.data} />}
      {tab === "Runs" && <ProjectRuns projectId={projectId} robots={robots.data} />}
      {tab === "Robots" && <section aria-label="Robots">
        <p>Physical robots and simulated instances keep separate connections and share a versioned physical profile.</p>
        {robots.data?.length === 0 && !adding && <div className="cv-empty"><h2>No robots yet</h2><p>Import a robot profile, then connect an enrolled device to it.</p><button className="cv-btn cv-btn--primary" onClick={() => setTab("Profiles")}>Add a profile</button></div>}
        <div className="cv-table-wrap"><table className="cv-table"><thead><tr><th scope="col">Select</th><th scope="col">Robot</th><th scope="col">Type</th><th scope="col">Profile</th><th scope="col">Fleet</th><th scope="col">Simulation</th></tr></thead><tbody>
          {robots.data?.map(robot => {
            const profile = profiles.data?.find(p => p.id === robot.profile_id);
            return <tr key={robot.id}>
              <td><input type="checkbox" aria-label={`Select ${robot.name}`} checked={selected.includes(robot.id)} disabled={!session.operator} onChange={e => setSelected(s => e.target.checked ? [...s, robot.id] : s.filter(id => id !== robot.id))} /></td>
              <th scope="row"><Link href={`/app/projects/${projectId}/robots/${robot.id}`}>{robot.name}</Link></th><td>{robot.simulated === false ? "Physical" : "Simulated"}</td>
              <td>{profile ? `${profile.name} · revision ${profile.revision}` : "Legacy runner profile"}</td>
              <td>{fleets.data?.find(f => f.id === robot.fleet_id)?.name ?? "Unassigned"}</td>
              <td>{robot.simulated === false && profile ? <button className="cv-link" disabled={!session.operator || !profile.simulation.engines.length} onClick={() => { setSource(robot); setAdding(true); }}>Create simulated instance</button> : <>
                {robot.source_robot_id ? `From ${robots.data?.find(r => r.id === robot.source_robot_id)?.name ?? "physical robot"}` : robot.simulation_engine ?? "Existing runner"}
                {robot.simulated && robot.profile_id && <SimulatorReadiness robot={robot} busy={mutation.busy} canWrite={session.operator} verify={() => void mutation.submit(`robots/${robot.id}/qualification`, {})} />}
              </>}</td>
            </tr>;
          })}
        </tbody></table></div>
        {session.operator && selected.length > 0 && <div className="project-actions">
          <label className="cv-field">Assign selected robots to fleet<select aria-label="Assign selected robots to fleet" className="cv-input" value={fleetId} onChange={e => setFleetId(e.target.value)}><option value="">Choose fleet</option>{fleets.data?.map(f => <option key={f.id} value={f.id}>{f.name}</option>)}</select></label>
          <button className="cv-btn cv-btn--secondary" disabled={mutation.busy || !fleetId} onClick={() => void assign()}>Assign {selected.length} robots</button>
        </div>}
        {mutation.error && <p role="alert">{mutation.error}</p>}
        {session.operator && adding && profiles.data && devices.data && <RegistrationForm key={source?.id ?? "new"} projectId={projectId} profiles={profiles.data} fleets={fleets.data ?? []} devices={devices.data.filter(d => !robots.data?.some(r => r.device_id === d.id))} source={source} onCancel={() => { setSource(undefined); setAdding(false); }} onSaved={() => { setSource(undefined); setAdding(false); refresh(); }} />}
      </section>}
      {tab === "Robots" && <ProjectConnectionTools />}
      {tab === "Configurations" && <><ProjectConfigurations projectId={projectId} /><SavedProjectConfigurations projectId={projectId} /></>}
      {tab === "Profiles" && <section aria-label="Robot profiles">
        <p>Each revision pins the robot interfaces and simulation assets. Asset declarations still require runner verification and calibration.</p>
        {profiles.data?.map(profile => <article className="cv-card" key={profile.id}><div><h2>{profile.name} · revision {profile.revision}</h2><p>{profile.spec.embodiment} · {profile.spec.joints.length} joints · {profile.spec.sensors.length} sensors</p><p>{profile.simulation.engines.length ? `Simulation assets: ${profile.simulation.engines.join(", ")}` : "Simulation assets missing"} · Dynamics: {profile.simulation.dynamics_source}</p><p>{profile.simulation.detail}</p><SimulationAssets profile={profile} /><details><summary>Profile specification</summary><pre className="project-spec">{JSON.stringify(profile.spec, null, 2)}</pre></details></div></article>)}
        {session.operator && <ProfileForm projectId={projectId} profiles={profiles.data ?? []} onSaved={refresh} />}
      </section>}
      {tab === "Fleets" && <section aria-label="Fleets">
        <p>Group registered robots here. Membership does not start tasks or deploy software.</p>
        {fleets.data?.map(fleet => <article className="cv-card" key={fleet.id}><div><h2>{fleet.name}</h2><p>{fleet.robot_ids.length} robots</p>{fleet.robot_ids.map(id => <p key={id}>{robots.data?.find(r => r.id === id)?.name ?? id} {session.operator && <button className="cv-link" disabled={mutation.busy} onClick={() => void mutation.submit(`fleets/${fleet.id}/members/${id}/remove`, {})}>Remove from {fleet.name}</button>}</p>)}</div></article>)}
        {mutation.error && <p role="alert">{mutation.error}</p>}
        {session.operator && <FleetForm projectId={projectId} onSaved={refresh} />}
      </section>}
    </>}
  </Shell>;
}

function ProfileForm({ projectId, profiles, onSaved }: { projectId: string; profiles: RobotProfile[]; onSaved: () => void }) {
  const [name, setName] = useState("");
  const [spec, setSpec] = useState<unknown>();
  const [fileError, setFileError] = useState<string>();
  const mutation = useMutation(onSaved);
  async function load(file?: File) {
    setSpec(undefined); setFileError(undefined);
    if (!file) return;
    try {
      if (file.size > 128 * 1024) throw new Error("Use a profile JSON file smaller than 128 KiB. Model assets are referenced separately.");
      setSpec(JSON.parse(await file.text()));
    } catch (cause) { setFileError(errorText(cause)); }
  }
  const latest = Math.max(0, ...profiles.filter(p => p.name === name.trim()).map(p => p.revision));
  async function submit(event: FormEvent) {
    event.preventDefault();
    await mutation.submit("robot-profiles", { project_id: projectId, name: name.trim(), expected_revision: latest, spec });
  }
  return <form className="cv-form project-create" onSubmit={submit}><h2>Import robot profile</h2>
    <p>Import a Convoy profile describing joints, sensors, controller and pinned simulation assets. <a className="cv-link" href="/robot-profile-template.json" download>Download a starting template</a>.</p>
    <label className="cv-field">Profile name<input className="cv-input" required maxLength={120} value={name} onChange={e => setName(e.target.value)} /></label>
    <label className="cv-field">Profile JSON<input type="file" accept=".json,application/json" onChange={e => void load(e.target.files?.[0])} /></label>
    {latest > 0 && <p>This creates revision {latest + 1}. Existing robots keep their pinned revision.</p>}
    {(fileError || mutation.error) && <p role="alert">{fileError ?? mutation.error}</p>}
    <button className="cv-btn cv-btn--primary" disabled={mutation.busy || !name.trim() || !spec}>Save profile revision</button>
  </form>;
}

function RegistrationForm({ projectId, profiles, devices, fleets, source, onSaved, onCancel }: { projectId: string; profiles: RobotProfile[]; devices: Device[]; fleets: Fleet[]; source?: Robot; onSaved: () => void; onCancel: () => void }) {
  const [name, setName] = useState(source ? `${source.name} · simulation` : "");
  const [kind, setKind] = useState(source ? "simulated" : "physical");
  const [profileId, setProfileId] = useState(source?.profile_id ?? "");
  const [deviceId, setDeviceId] = useState("");
  const [connected, setConnected] = useState<Device>();
  const [engine, setEngine] = useState("");
  const [fleetId, setFleetId] = useState("");
  const profile = profiles.find(p => p.id === profileId);
  const available = [...devices, ...(connected && !devices.some(d => d.id === connected.id) ? [connected] : [])].filter(d => d.simulated === (kind === "simulated"));
  const mutation = useMutation(onSaved);
  async function submit(event: FormEvent) {
    event.preventDefault();
    await mutation.submit("robot-registrations", { project_id: projectId, name: name.trim(), kind, device_id: deviceId, profile_id: profileId, simulation_engine: kind === "simulated" ? engine : null, source_robot_id: source?.id ?? null, fleet_id: fleetId || null });
  }
  return <form className="cv-form project-create" onSubmit={submit}>
    <h2>{source ? `Simulate ${source.name}` : "Register a robot"}</h2>
    {source && <p>The simulated instance uses the physical robot’s exact profile revision. It gets its own runner connection.</p>}
    <label className="cv-field">Robot name<input className="cv-input" required maxLength={120} value={name} onChange={e => setName(e.target.value)} /></label>
    <label className="cv-field">Robot type<select aria-label="Robot type" className="cv-input" disabled={!!source} value={kind} onChange={e => { setKind(e.target.value); setDeviceId(""); setEngine(""); }}><option value="physical">Physical robot</option><option value="simulated">Simulated robot</option></select></label>
    <label className="cv-field">Robot profile<select aria-label="Robot profile" className="cv-input" required disabled={!!source} value={profileId} onChange={e => { setProfileId(e.target.value); setEngine(""); }}><option value="">Choose a profile revision</option>{profiles.map(p => <option key={p.id} value={p.id}>{p.name} · revision {p.revision}</option>)}</select></label>
    {kind === "simulated" && <label className="cv-field">Simulation engine<select aria-label="Simulation engine" className="cv-input" required value={engine} onChange={e => setEngine(e.target.value)}><option value="">Choose engine</option>{profile?.simulation.engines.map(e => <option key={e} value={e}>{e === "isaac" ? "NVIDIA Isaac Sim" : "MuJoCo"}</option>)}</select></label>}
    <label className="cv-field">{kind === "simulated" ? "Enrolled simulator runner" : "Enrolled robot computer"}<select aria-label={kind === "simulated" ? "Enrolled simulator runner" : "Enrolled robot computer"} className="cv-input" required value={deviceId} onChange={e => setDeviceId(e.target.value)}><option value="">Choose a connection</option>{available.map(d => <option key={d.id} value={d.id}>{d.name} · {d.status === "never_seen" ? "awaiting heartbeat" : d.status}</option>)}</select></label>
    {available.length === 0 && <p>No unassigned connections yet. Connect a computer below.</p>}
    <ConnectionSetup key={kind} projectId={projectId} name={name.trim()} simulated={kind === "simulated"} onConnected={device => { setConnected(device); setDeviceId(device.id); }} />
    <ComputerDetails deviceId={deviceId} />
    <label className="cv-field">Fleet (optional)<select aria-label="Fleet (optional)" className="cv-input" value={fleetId} onChange={e => setFleetId(e.target.value)}><option value="">Unassigned</option>{fleets.map(f => <option key={f.id} value={f.id}>{f.name}</option>)}</select></label>
    <p>Registration saves identity and profile links. It does not start motion or verify the simulator assets.</p>
    {mutation.error && <p role="alert">{mutation.error}</p>}
    <button className="cv-btn cv-btn--primary" disabled={mutation.busy || !name.trim() || !profileId || !deviceId || (kind === "simulated" && !engine)}>Register robot</button>
    <button type="button" className="cv-link" onClick={onCancel}>Cancel registration</button>
  </form>;
}

function FleetForm({ projectId, onSaved }: { projectId: string; onSaved: () => void }) {
  const [name, setName] = useState("");
  const mutation = useMutation(onSaved);
  async function submit(event: FormEvent) { event.preventDefault(); if (await mutation.submit("fleets", { project_id: projectId, name: name.trim() })) setName(""); }
  return <form className="cv-form project-create" onSubmit={submit}><label className="cv-field">Fleet name<input className="cv-input" required maxLength={120} value={name} onChange={e => setName(e.target.value)} /></label>{mutation.error && <p role="alert">{mutation.error}</p>}<button className="cv-btn cv-btn--primary" disabled={mutation.busy || !name.trim()}>Create fleet</button></form>;
}
