"use client";
import Link from "next/link";
import { useState } from "react";
import { useWorkspace } from "@/lib/configurations/client";
import { routes } from "@/lib/configurations/routes";
import type { Robot, Application, Deployment, Mission, Episode } from "@/lib/platform/client";
import { useProjectResource, type RobotProfile, type Fleet } from "@/lib/projects/client";
import { projectHref } from "./ProjectNavigation";
import { EpisodeReplay } from "@/components/console/EpisodeReplay";
import { Portal } from "@/components/portal/Portal";
import { useSession } from "@/components/configurations/Session";
import { notifySessionExpired } from "@/lib/configurations/session-events";
import { TimingResult } from "./TimingResult";

export const robotHref = (projectId: string, robotId: string) => `/app/projects/${projectId}/robots/${robotId}`;

export function ProjectOverview({ projectId, robots, profiles, fleets }: { projectId: string; robots?: Robot[]; profiles?: RobotProfile[]; fleets?: Fleet[] }) {
  const workspace = useWorkspace();
  const setups = workspace.source === "document" ? workspace.workspace?.configurations.filter(c => c.projectId === projectId).length ?? 0 : 0;
  const configurations = useProjectResource<Application[]>(`applications?project_id=${projectId}`);
  const deployments = useProjectResource<Deployment[]>(`deployments?project_id=${projectId}`, 0, true);
  const next = !profiles?.length ? ["Profiles", "Import a robot profile", "Describe its joints and controller, then upload a matching simulation model."] : !robots?.length ? ["Robots", "Register your first robot", "Connect a computer and associate it with the robot’s physical profile."] : !configurations.data?.length ? ["Configurations", "Configure a task", "Choose a policy and timing limits for a registered robot."] : ["Simulations", "Test your configuration", "Verify a simulator, deploy a release and inspect the resulting run."];
  return <section className="project-section" aria-label="Project overview"><p>Register robots, configure their intelligence, and verify how tasks perform before deployment.</p>
    <div className="cv-grid">{[["Robots", robots?.length], ["Fleets", fleets?.length], ["Configurations", configurations.data ? `${configurations.data.length} runnable · ${setups} model setups` : undefined]].map(([label, count]) => <Link className="cv-config" key={label} href={projectHref(projectId, String(label))}><h2>{label}</h2><p>{count ?? "Loading…"}</p></Link>)}</div>
    <article className="cv-card"><div><h2>{next[1]}</h2><p>{next[2]}</p><Link className="cv-btn cv-btn--primary" href={projectHref(projectId, next[0])}>Continue setup</Link></div></article>
    <h2>Deployments</h2>{[configurations.error, deployments.error].filter(Boolean).map(error => <p role="alert" key={error}>{error}</p>)}
    {deployments.data?.length === 0 && <p>No deployments yet. Saving a configuration does not activate it on a robot.</p>}
    {deployments.data?.filter(d => robots?.some(r => r.id === d.robot_id && r.generation === d.generation)).map(d => <p key={d.id}><Link className="cv-link" href={robotHref(projectId, d.robot_id)}>{robots?.find(r => r.id === d.robot_id)?.name}</Link> · {d.state} · revision {d.generation}</p>)}
    <p><Link className="cv-link" href={projectHref(projectId, "Runs")}>View task results and evaluations →</Link></p>
  </section>;
}

export function ProjectSimulations({ projectId, robots }: { projectId: string; robots?: Robot[] }) {
  const simulators = robots?.filter(r => r.simulated);
  return <section className="project-section" aria-label="Simulations"><h2>Simulations</h2><p>Choose a simulated robot to verify its model, deploy a configuration and start a task. Registered execution currently supports MuJoCo joint-position tasks.</p><p><Link className="cv-btn cv-btn--primary" href={projectHref(projectId, "Robots")}>Register or select a simulator</Link> <Link className="cv-link" href={projectHref(projectId, "Profiles")}>Manage robot models</Link></p>
    {simulators?.length === 0 && <p>No simulated robots yet. A physical robot’s simulated counterpart shares its profile revision and uses a separate runner.</p>}
    <div className="cv-grid">{simulators?.map(robot => <Link className="cv-config" key={robot.id} href={robotHref(projectId, robot.id)}><h3>{robot.name}</h3><p>{robot.simulation_engine ?? "Existing simulator"}</p><p>{robot.qualification?.state === "passed" ? "Simulator verified" : robot.profile_id ? "Verification required" : "Existing runtime"}</p><p>Open task controls →</p></Link>)}</div>
    <p className="cv-notice">Natural-language scene creation and Isaac execution are not available yet. Simulator verification checks the supplied model and interfaces; physical fidelity requires separate calibration.</p><SavedResults projectId={projectId} />
  </section>;
}

export function ProjectRuns({ projectId, robots }: { projectId: string; robots?: Robot[] }) {
  const tasks = useProjectResource<Mission[]>(`missions?project_id=${projectId}`, 0, true);
  const [selected, setSelected] = useState<Mission>();
  const episode = useProjectResource<Episode>(selected?.episode_id ? `episodes/${selected.episode_id}` : null);
  return <section className="project-section" aria-label="Runs"><h2>Runs</h2><p>Task outcome and timing evidence are reported separately. Select a completed task to inspect its measurements.</p>
    {tasks.error && <p role="alert">{tasks.error}</p>}{!tasks.data && !tasks.error && <p role="status">Loading runs…</p>}{tasks.data?.length === 0 && <p>No tasks yet. Open a robot to deploy a configuration and start one.</p>}
    {!!tasks.data?.length && <div className="cv-table-wrap"><table className="cv-table"><thead><tr><th>Robot</th><th>Status</th><th>Updated</th><th>Result</th></tr></thead><tbody>{tasks.data.map(task => <tr key={task.id}><td><Link href={robotHref(projectId, task.robot_id)}>{robots?.find(r => r.id === task.robot_id)?.name ?? task.robot_id}</Link></td><td>{task.state}</td><td>{new Date(task.updated_at).toLocaleString()}</td><td>{task.episode_id ? <button className="cv-link" onClick={() => setSelected(task)}>View result</button> : task.detail || "Awaiting result"}</td></tr>)}</tbody></table></div>}
    {selected && <article className="cv-card"><div><h3>Task result</h3><p>{selected.detail}</p>{episode.error && <p role="alert">{episode.error}</p>}{episode.data && <><p>Task success: {typeof episode.data.summary.final_success === "boolean" ? episode.data.summary.final_success ? "Yes" : "No" : "Not reported"}</p><p>Release: <code>{episode.data.release_digest}</code></p><TimingResult value={episode.data.summary.timing} /><EpisodeReplay key={episode.data.id} episodeId={episode.data.id} /><p>Simulation time: {String(episode.data.summary.simulated_duration_s ?? "Not reported")} s · Wall time: {String(episode.data.summary.wall_duration_s ?? "Not reported")} s</p>{!episode.data.summary.timing && <p>Independent real-time measurements were not reported for this task.</p>}<Link className="cv-link" href={robotHref(projectId, selected.robot_id)}>Open robot controls and full result</Link></>}</div></article>}<SavedResults projectId={projectId} />
  </section>;
}

function SavedResults({ projectId }: { projectId: string }) {
  const ws = useWorkspace();
  const configurations = ws.source === "document" ? ws.workspace?.configurations.filter(c => c.projectId === projectId) ?? [] : [];
  return <section className="project-section" aria-label="Saved evaluations"><h2>Evaluations and recordings</h2><p>Model setups retain their existing evaluation history, traces and available replay recordings.</p>{configurations.map(config => <article key={config.id} className="cv-card"><div><h3>{config.name}</h3>{ws.workspace?.robots.filter(r => r.configId === config.id).map(robot => <p key={robot.id}><Link className="cv-link" href={routes.robot(config.id, robot.id)}>{robot.name} · evaluations and traces →</Link></p>)}<Link className="cv-link" href={routes.configuration(config.id)}>Open configuration results</Link></div></article>)}{configurations.length === 0 && <p><Link className="cv-link" href={projectHref(projectId, "Configurations")}>Assign an existing model setup</Link> to include its evaluations here.</p>}</section>;
}

export function ProjectConnectionTools() {
  const [open, setOpen] = useState(false);
  const { operator } = useSession();
  return <section className="project-create" aria-label="Connection diagnostics"><h2>Connection diagnostics</h2><p>Inspect and test the workspace’s configured device connection. The device shown here is named explicitly; registered robots keep their own computer connections.</p><button className="cv-btn cv-btn--secondary" onClick={() => setOpen(value => !value)}>{open ? "Close device tools" : "Open device tools"}</button>{open && <Portal active onSessionEnd={notifySessionExpired} canChat={operator} />}</section>;
}
