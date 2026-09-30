"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, errorText, MutationAttempts, terminal, timestamp } from "@/lib/platform/client";
import type { Account, Application, Deployment, Device, Episode, EvaluationRun, Mission, Project, Qualification, Release, Robot } from "@/lib/platform/client";
import { Portal } from "@/components/portal/Portal";
import { Evaluations } from "./Evaluations";
import { ReleaseDetails } from "./ReleaseDetails";
import { EpisodeSummary } from "./EpisodeSummary";
import { PAIRED_PROFILE, releaseComponents } from "@/lib/platform/manifest";

const PROFILES = [
  { id: "metaworld-sawyer-pick-place-v1", label: "Sawyer · state observations" },
  { id: "metaworld-smolvla-pick-place-rgb-v1", label: "Sawyer · camera observations (SmolVLA)" },
  { id: PAIRED_PROFILE, label: "Sawyer · text planner + camera action policy" },
];
const shellQuote = (value: string) => `'${value.replaceAll("'", "'\\''")}'`;

function Brand() { return <Link className="console-brand" href="/app">Robot applications</Link>; }
function Status({ state }: { state: string }) {
  return <span className={`console-status console-status-${["ready", "completed"].includes(state) ? "success" : ["failed", "unknown", "blocked"].includes(state) ? "warning" : "neutral"}`}>{state.replaceAll("_", " ")}</span>;
}
function Alert({ children }: { children: React.ReactNode }) { return <p className="console-alert" role="alert">{children}</p>; }

export function Console() {
  const [account, setAccount] = useState<Account | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try { setAccount(await api<Account>("auth/me")); setError(null); }
    catch (cause) { setAccount(null); if (!(cause instanceof ApiError && cause.status === 401)) setError(errorText(cause)); }
  }, []);
  useEffect(() => { const timer = window.setTimeout(() => void load(), 0); return () => window.clearTimeout(timer); }, [load]);
  const signOut = useCallback(() => setAccount(null), []);
  return <div className="console-shell">
    {account === undefined ? <main className="console-auth"><Brand /><p role="status">Checking your session…</p></main>
      : account === null ? <Login initialError={error} onLogin={load} />
      : <Workspace account={account} onSessionEnd={signOut} />}
  </div>;
}

function Login({ initialError, onLogin }: { initialError: string | null; onLogin: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(initialError);
  const lock = useRef(false);
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (lock.current) return;
    lock.current = true; setBusy(true); setError(null);
    const form = event.currentTarget;
    const data = new FormData(form);
    try { await api("auth/login", { email: data.get("email"), password: data.get("password") }); form.reset(); await onLogin(); }
    catch (cause) { setError(errorText(cause)); }
    finally { lock.current = false; setBusy(false); }
  }
  return <main className="console-auth"><Brand /><section className="console-card">
    <p className="console-eyebrow">Your Convoy workspace</p><h1>One workspace for your robots.</h1>
    <p>Sign in to connect a device, test its model, and manage robot applications.</p>
    <form onSubmit={event => void submit(event)}>
      <label>Email<input name="email" type="email" autoComplete="username" required /></label>
      <label>Password<input name="password" type="password" autoComplete="current-password" required /></label>
      {error && <Alert>{error}</Alert>}
      <button className="btn btn-primary" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
    </form><p className="console-note">Use your Convoy account for devices, applications, and simulation results.</p>
  </section></main>;
}

function Workspace({ account, onSessionEnd }: { account: Account; onSessionEnd: () => void }) {
  const [section, setSection] = useState<"applications" | "device">("applications");
  const [deviceVisited, setDeviceVisited] = useState(false);
  useEffect(() => {
    const sync = () => {
      const selected = new URLSearchParams(window.location.search).get("section") === "device" ? "device" : "applications";
      setSection(selected);
      if (selected === "device") setDeviceVisited(true);
    };
    const initial = window.setTimeout(sync, 0);
    window.addEventListener("popstate", sync);
    return () => { window.clearTimeout(initial); window.removeEventListener("popstate", sync); };
  }, []);
  function navigateSection(next: "applications" | "device") {
    setSection(next);
    if (next === "device") setDeviceVisited(true);
    const params = new URLSearchParams(window.location.search);
    params.set("section", next);
    window.history.pushState(null, "", `/app/applications?${params}`);
  }
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const attempts = useRef(new MutationAttempts());
  const reload = useCallback(async () => {
    try { setProjects(await api<Project[]>("projects")); }
    catch (cause) { if (cause instanceof ApiError && cause.status === 401) onSessionEnd(); else setError(errorText(cause)); }
  }, [onSessionEnd]);
  useEffect(() => { const timer = window.setTimeout(() => void reload(), 0); return () => window.clearTimeout(timer); }, [reload]);
  async function create(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (busy) return; setBusy(true); setError(null);
    const form = event.currentTarget;
    try {
      const project = await attempts.current.submit<Project>("projects", { name: new FormData(form).get("name") });
      await reload(); setProjectId(project.id); form.reset();
    } catch (cause) { setError(errorText(cause)); } finally { setBusy(false); }
  }
  async function logout() {
    try { await api("auth/logout", {}); onSessionEnd(); } catch (cause) { setError(errorText(cause)); }
  }
  const project = projects.find(item => item.id === projectId) ?? projects[0];
  const writable = account.user.role !== "viewer";
  const paused = !!(account.installation.dispatch_paused_at || account.installation.quarantined_at);
  return <>
    <a className="console-skip" href="#console-main">Skip to workspace</a>
    <header className="console-header"><Brand /><div><span>{account.user.email}</span><button onClick={() => void logout()}>Sign out</button></div></header>
    <main id="console-main" className="console-main" tabIndex={-1}>
      <div className="console-heading"><div><p className="console-eyebrow">Robot applications</p><h1>Build. Deploy. Observe.</h1><p>Connect your device, test its model, and bring your application together.</p></div></div>
      <nav className="application-nav" aria-label="Robot applications">{(["applications", "device"] as const).map(item => <a key={item} href={`/app/applications?section=${item}`} aria-current={section === item ? "page" : undefined} onClick={event => { if (!event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey && event.button === 0) { event.preventDefault(); navigateSection(item); } }}>{item === "device" ? "Device connection" : "Applications"}</a>)}</nav>
      {error && <Alert>{error}</Alert>}
      <div hidden={section !== "device"}>{deviceVisited && <Portal active={section === "device"} onSessionEnd={onSessionEnd} canChat={writable} />}</div>
      <div hidden={section !== "applications"}>
      <p className="console-scope">Sawyer pick-and-place simulation. Policy and environment identity are pinned in each release.</p>
      {paused && <Alert>Dispatch is paused or the installation is in recovery. Existing observations remain available.</Alert>}
      <section className="console-projects" aria-label="Project selection">
        <label>Project<select value={project?.id ?? ""} onChange={event => setProjectId(event.target.value)}><option value="" disabled>{projects.length ? "Choose a project" : "No projects yet"}</option>{projects.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        {writable && <form onSubmit={event => void create(event)}><label>New project name<input name="name" maxLength={120} required /></label><button className="btn btn-secondary" disabled={busy}>Create project</button></form>}
      </section>
      {project ? <ProjectWorkspace key={project.id} project={project} writable={writable} canDispatch={!paused && account.installation.simulator} executionProfiles={account.installation.execution_profiles ?? []} onSessionEnd={onSessionEnd} /> : <p>Create a project to connect a simulator and register an application release.</p>}
      </div>
    </main>
  </>;
}

function ProjectWorkspace({ project, writable, canDispatch, executionProfiles, onSessionEnd }: { project: Project; writable: boolean; canDispatch: boolean; executionProfiles: string[]; onSessionEnd: () => void }) {
  const [robots, setRobots] = useState<Robot[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [applications, setApplications] = useState<Application[]>([]);
  const [releases, setReleases] = useState<Release[]>([]);
  const [deployments, setDeployments] = useState<Deployment[]>([]);
  const [missions, setMissions] = useState<Mission[]>([]);
  const [evaluations, setEvaluations] = useState<EvaluationRun[]>([]);
  const [qualification, setQualification] = useState<Qualification | null>(null);
  const [robotId, setRobotId] = useState("");
  const [applicationId, setApplicationId] = useState("");
  const [releaseId, setReleaseId] = useState("");
  const [manifest, setManifest] = useState("");
  const [enrollment, setEnrollment] = useState("");
  const [episode, setEpisode] = useState<Episode | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [updated, setUpdated] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const actionLock = useRef(false);
  const refreshLock = useRef<Promise<void> | null>(null);
  const attempts = useRef(new MutationAttempts());
  const alive = useRef(true);
  const projectQuery = `project_id=${encodeURIComponent(project.id)}`;
  const robot = robots.find(item => item.id === robotId) ?? robots[0];
  const application = applications.find(item => item.id === applicationId) ?? applications[0];
  const selectedApplicationId = application?.id;
  const release = releases.find(item => item.id === releaseId) ?? releases[0];
  const deployment = robot ? deployments.find(item => item.robot_id === robot.id && item.generation === robot.generation) : undefined;
  const robotMissions = robot ? missions.filter(item => item.robot_id === robot.id) : [];
  const active = robotMissions.find(item => !terminal(item.state));
  const reserved = !!robot?.evaluation_id;
  const profileMatches = !!robot && !!release && robot.profile === releaseComponents(release.manifest).profile;
  const pairedRegistration = executionProfiles.includes(PAIRED_PROFILE);
  const qualified = !!release && qualification?.release_id === release.id && qualification.deployment_allowed;

  const refresh = useCallback(async (afterMutation = false) => {
    if (refreshLock.current) {
      await refreshLock.current;
      if (!afterMutation) return;
    }
    // A read begun before a mutation cannot acknowledge its new reservation.
    // Keep the action locked until a fresh post-mutation snapshot completes.
    const pending = (async () => {
      setRefreshing(true);
      try {
        const [r, d, a, m, targets, runs] = await Promise.all([
          api<Robot[]>(`robots?${projectQuery}`), api<Device[]>("devices"), api<Application[]>(`applications?${projectQuery}`),
          api<Mission[]>(`missions?${projectQuery}`), api<Deployment[]>(`deployments?${projectQuery}`),
          api<EvaluationRun[]>(`evaluations?${projectQuery}`),
        ]);
        if (alive.current) { setRobots(r); setDevices(d); setApplications(a); setMissions(m); setDeployments(targets); setEvaluations(runs); setUpdated(new Date().toISOString()); setRefreshError(null); }
      } catch (cause) {
        if (alive.current) { if (cause instanceof ApiError && cause.status === 401) onSessionEnd(); else setRefreshError(errorText(cause)); }
      } finally { if (alive.current) setRefreshing(false); }
    })();
    refreshLock.current = pending;
    try { await pending; } finally { if (refreshLock.current === pending) refreshLock.current = null; }
  }, [projectQuery, onSessionEnd]);
  useEffect(() => {
    alive.current = true;
    const initial = window.setTimeout(() => void refresh(), 0);
    const interval = window.setInterval(() => { if (document.visibilityState === "visible") void refresh(); }, 3000);
    return () => { alive.current = false; window.clearTimeout(initial); window.clearInterval(interval); };
  }, [refresh]);
  const loadReleases = useCallback(async () => {
    if (!selectedApplicationId) return;
    const result = await api<Release[]>(`applications/${selectedApplicationId}/releases`);
    if (alive.current) setReleases(result);
  }, [selectedApplicationId]);
  useEffect(() => {
    let current = true;
    if (!selectedApplicationId) return;
    void api<Release[]>(`applications/${selectedApplicationId}/releases`).then(result => { if (current) setReleases(result); }).catch(cause => { if (current) setError(errorText(cause)); });
    return () => { current = false; };
  }, [selectedApplicationId]);
  async function run(action: () => Promise<void>) {
    if (actionLock.current) return;
    actionLock.current = true; setBusy(true); setError(null); setNotice(null);
    try { await action(); await refresh(true); }
    catch (cause) { if (cause instanceof ApiError && cause.status === 401) onSessionEnd(); else setError(errorText(cause)); }
    finally { actionLock.current = false; if (alive.current) setBusy(false); }
  }
  function submit(event: React.FormEvent<HTMLFormElement>, action: (form: FormData) => Promise<void>) {
    event.preventDefault(); const data = new FormData(event.currentTarget); void run(() => action(data));
  }
  async function readManifest(file?: File) {
    if (!file) return;
    if (file.size > 128 * 1024) { setError("The manifest must be smaller than 128 KiB."); return; }
    try { const text = await file.text(); JSON.parse(text); setManifest(text); setError(null); }
    catch { setError("Choose a valid JSON release manifest."); }
  }
  const disabled = busy || !writable;
  return <>
    <div className="console-refresh"><p>Management snapshot: {updated ? timestamp(updated) : "Loading…"}. Refreshes every 3 seconds.</p><button disabled={refreshing} onClick={() => void refresh()}>{refreshing ? "Refreshing…" : "Refresh"}</button></div>
    {refreshError && <Alert>{refreshError} Showing the last successful snapshot. Deployment and start are disabled until refreshed.</Alert>}
    {error && <Alert>{error}</Alert>}{notice && <p className="console-notice" role="status">{notice}</p>}
    <div className="console-grid">
      <section className="console-card" aria-labelledby="robots-heading"><p className="console-eyebrow">01 / Connect</p><h2 id="robots-heading">Robots</h2>
        <label>Selected robot<select value={robot?.id ?? ""} onChange={event => { setRobotId(event.target.value); setEpisode(null); }}><option value="" disabled>No robot selected</option>{robots.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        {robot && <p className="console-note">{robot.profile}<br /><code>{robot.id}</code> · Generation {robot.generation}</p>}
        <details><summary>Connect a simulator</summary><p>Create an enrollment token, run the command on the simulator host, then bind the enrolled device below.</p>
          <form onSubmit={event => submit(event, async data => {
            const label = String(data.get("name"));
            const token = await api<{ token: string; server_url: string }>("enrollments", { label, simulated: true });
            setEnrollment(`convoy-agent enroll --server ${shellQuote(token.server_url)} --token ${shellQuote(token.token)} --name ${shellQuote(label)} --simulate`);
          })}><label>Simulator name<input name="name" defaultValue="Sawyer simulator" required maxLength={120} /></label><button className="btn btn-secondary" disabled={disabled || !canDispatch}>Create enrollment command</button></form>
          {enrollment && <><p className="console-note">This command contains a one-use credential. It is shown only in this session.</p><pre className="console-code">{enrollment}</pre><button onClick={() => void navigator.clipboard.writeText(enrollment).then(() => setNotice("Enrollment command copied.")).catch(() => setError("Copy the enrollment command manually."))}>Copy command</button></>}
          <form onSubmit={event => submit(event, async data => {
            if (data.get("profile") === PAIRED_PROFILE && !pairedRegistration) throw new Error("This server has not enabled the paired simulator profile.");
            const created = await attempts.current.submit<Robot>("robots", { project_id: project.id, device_id: data.get("device"), name: data.get("name"), profile: data.get("profile") });
            setRobotId(created.id); setNotice("Simulator registered. Configure a release before deployment.");
          })}><label>Enrolled device<select name="device" required defaultValue=""><option value="" disabled>Choose an enrolled simulator</option>{devices.filter(item => item.simulated && !robots.some(bound => bound.device_id === item.id)).map(item => <option key={item.id} value={item.id}>{item.name} · {item.id}</option>)}</select></label><label>Robot name<input name="name" required maxLength={120} /></label><label>Simulator profile<select name="profile" defaultValue={PROFILES[0].id}>{PROFILES.map(profile => <option key={profile.id} value={profile.id} disabled={profile.id === PAIRED_PROFILE && !pairedRegistration}>{profile.label}{profile.id === PAIRED_PROFILE && !pairedRegistration ? " · server support required" : ""}</option>)}</select></label><p className="console-note">Choose the profile supported by your simulator and release. The paired option requires explicit server support. Registration does not install models, acknowledge component readiness, or qualify physical hardware.</p><button className="btn btn-secondary" disabled={disabled || !canDispatch}>Register robot</button></form>
          <p className="console-note">The coordinator must also be configured on that host with this robot ID and a verified inference worker.</p>
        </details>
      </section>
      <section className="console-card" aria-labelledby="application-heading"><p className="console-eyebrow">02 / Package</p><h2 id="application-heading">Applications & releases</h2>
        <label>Application<select value={application?.id ?? ""} onChange={event => { setApplicationId(event.target.value); setReleases([]); setReleaseId(""); }}><option value="" disabled>No application selected</option>{applications.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <details><summary>Create an application</summary><form onSubmit={event => submit(event, async data => {
          const created = await attempts.current.submit<Application>("applications", { project_id: project.id, name: data.get("name") });
          setApplicationId(created.id); setReleases([]); setReleaseId(""); setNotice("Application created. Register a pinned release manifest.");
        })}><label>Application name<input name="name" maxLength={120} required /></label><button className="btn btn-secondary" disabled={disabled}>Create application</button></form></details>
        <label>Release<select value={release?.id ?? ""} onChange={event => setReleaseId(event.target.value)}><option value="" disabled>No release selected</option>{releases.map(item => <option key={item.id} value={item.id}>{item.id} · {item.digest.slice(0, 12)}</option>)}</select></label>
        {release && <ReleaseDetails release={release} />}
        <details><summary>Register a release manifest</summary><p>Use the manifest produced by your policy build. Registration records its identity; deployment readiness is acknowledged separately by the coordinator.</p>
          <label>Upload manifest JSON<input type="file" accept="application/json,.json" onChange={event => void readManifest(event.target.files?.[0])} /></label>
          <form onSubmit={event => submit(event, async () => {
            if (!application) throw new Error("Choose an application first.");
            let parsed: unknown; try { parsed = JSON.parse(manifest); } catch { throw new Error("The release manifest is not valid JSON."); }
            const created = await attempts.current.submit<Release>(`applications/${application.id}/releases`, { manifest: parsed });
            await loadReleases(); setReleaseId(created.id); setNotice("Immutable release registered. It has not been deployed or started.");
          })}><label>Manifest JSON<textarea rows={9} required value={manifest} onChange={event => setManifest(event.target.value)} spellCheck={false} /></label><button className="btn btn-secondary" disabled={disabled || !application}>Register release</button></form>
        </details>
      </section>
      {application && release && <Evaluations key={`${application.id}:${release.id}`} application={application} release={release} robot={robot} runs={evaluations}
        qualification={qualification?.release_id === release.id ? qualification : null} onQualification={setQualification} snapshot={updated}
        dispatchAvailable={canDispatch && !refreshError && !!updated} hasActiveMission={!!active} writable={writable} busy={busy} run={run}
        onEpisode={async id => { setEpisode(await api<Episode>(`episodes/${id}`)); document.getElementById("episodes-heading")?.scrollIntoView({ behavior: "smooth" }); }} />}
      <section className="console-card" aria-labelledby="deployment-heading"><p className="console-eyebrow">04 / Deploy</p><h2 id="deployment-heading">Deployment</h2>
        {deployment ? <><div className="console-state"><Status state={deployment.state} /><span>Generation {deployment.generation}</span></div><p>Desired release <code>{deployment.release_id}</code></p><p className="console-note">Last acknowledgement: {timestamp(deployment.observed_at)}</p>{deployment.detail && <p>{deployment.detail}</p>}</> : <p>No deployment requested for the selected robot.</p>}
        <button className="btn btn-primary" disabled={disabled || !canDispatch || !robot || !release || !!active || reserved || !qualified || !profileMatches || !!refreshError || !updated} onClick={() => void run(async () => {
          if (!robot || !release) return;
          await attempts.current.submit("deployments", { robot_id: robot.id, release_id: release.id, expected_generation: robot.generation });
          setNotice("Deployment requested. Wait for the coordinator to acknowledge readiness; this does not start a mission.");
        })}>Request deployment</button>
        {reserved && <p className="console-note">Reserved by evaluation <code>{robot?.evaluation_id}</code>. Inspect or request cancellation in Release qualification.</p>}
        {!profileMatches && <p className="console-note">Choose a robot and release with the same observation profile.</p>}
        {!qualified && release && <p className="console-note">The selected release’s qualification must be available and satisfy the current application gate.</p>}
        <p className="console-note">Uses the selected robot and release. Active or unresolved missions block deployment. An acknowledgement is not a live-health guarantee.</p>
      </section>
      <section className="console-card" aria-labelledby="mission-heading"><p className="console-eyebrow">05 / Run</p><h2 id="mission-heading">Missions</h2>
        {active && <div className="console-state"><Status state={active.state} /><code>{active.id}</code></div>}
        {active?.state === "unknown" && <Alert>Execution state is unresolved. Reconcile it on the coordinator before new work can start.</Alert>}
        <form onSubmit={event => submit(event, async data => {
          if (!robot || !deployment) throw new Error("Choose a ready deployment first.");
          await attempts.current.submit<Mission>(`robots/${robot.id}/missions`, { deployment_id: deployment.id, expected_generation: robot.generation, seed: Number(data.get("seed")), ttl_s: Number(data.get("ttl")) });
          setNotice("Mission requested. Running is shown only after the coordinator acknowledges execution.");
        })}><div className="console-pair"><label>Scenario seed<input name="seed" type="number" min={0} max={4294967295} step={1} defaultValue={0} required /></label><label>Authorization, seconds<input name="ttl" type="number" min={1} max={300} step={1} defaultValue={60} required /></label></div><button className="btn btn-primary" disabled={disabled || !canDispatch || deployment?.state !== "ready" || deployment?.release_id !== release?.id || !qualified || !profileMatches || !!active || reserved || !!refreshError || !updated}>Start mission</button></form>
        {reserved && <p className="console-note">This robot is reserved by an evaluation. Use its evaluation cancellation control to stop the suite.</p>}
        {deployment && deployment.release_id !== release?.id && <p className="console-note">Start uses the currently deployed release. Select <code>{deployment.release_id}</code> above before starting it.</p>}
        <p className="console-note">Authorization covers both queueing and execution, bounded by the release policy. Simulation steps are not a physical-robot timing qualification.</p>
        {active && !reserved && <button className="btn btn-secondary" disabled={disabled || active.state === "cancel_requested"} onClick={() => void run(async () => {
          await attempts.current.submit(`missions/${active.id}/cancel`, { reason: "Requested from application console" });
          setNotice("Cancellation requested. Wait for acknowledgement; this button is not an emergency stop.");
        })}>{active.state === "cancel_requested" ? "Cancellation awaiting acknowledgement" : "Request cancellation"}</button>}
      </section>
    </div>
    <section className="console-card console-history" aria-labelledby="episodes-heading"><div className="console-section-heading"><div><p className="console-eyebrow">06 / Inspect</p><h2 id="episodes-heading">Mission history & episodes</h2></div><span>{robot?.name ?? "Select a robot"}</span></div>
      {robotMissions.length ? <div className="console-table-wrap"><table><thead><tr><th>Mission</th><th>State</th><th>Seed</th><th>Last report</th><th>Evidence</th></tr></thead><tbody>{robotMissions.map(item => <tr key={item.id}><td><code>{item.id}</code>{item.detail && <small>{item.detail}</small>}</td><td><Status state={item.state} /></td><td>{item.seed}</td><td>{timestamp(item.updated_at)}</td><td>{item.episode_id ? <button disabled={busy} onClick={() => void run(async () => { setEpisode(await api<Episode>(`episodes/${item.episode_id}`)); })}>View episode</button> : "No terminal episode"}</td></tr>)}</tbody></table></div> : <p>No missions recorded for this robot.</p>}
      {episode && <div className="console-episode"><h3>Episode <code>{episode.id}</code></h3><p><Status state={episode.state} /> {episode.detail}</p><EpisodeSummary episode={episode} /></div>}
    </section>
  </>;
}
