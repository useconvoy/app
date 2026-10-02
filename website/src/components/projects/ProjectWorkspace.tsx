"use client";
import Link from "next/link";
import { useState } from "react";
import { useWorkspace } from "@/lib/configurations/client";
import { routes } from "@/lib/configurations/routes";
import type { Robot, Mission, Episode } from "@/lib/platform/client";
import { useProjectResource, type RobotProfile, type Fleet } from "@/lib/projects/client";
import { projectHref } from "./ProjectNavigation";
import { EpisodeReplay } from "@/components/console/EpisodeReplay";
import { Portal } from "@/components/portal/Portal";
import { useSession } from "@/components/configurations/Session";
import { notifySessionExpired } from "@/lib/configurations/session-events";
import { TimingResult } from "./TimingResult";
import { ProjectFleetDirectory } from "./ProjectFleetDirectory";
import type { ProjectRobotConfigurationCatalogue } from "./ProjectRobotConfigurations";

export const robotHref = (projectId: string, robotId: string) => `/app/projects/${projectId}/robots/${robotId}`;

export function ProjectOverview({ projectId, robots, profiles, fleets, catalogue }: { projectId: string; robots?: Robot[]; profiles?: RobotProfile[]; fleets?: Fleet[]; catalogue: ProjectRobotConfigurationCatalogue }) {
  const workspace = useWorkspace();
  const setups = workspace.source === "document" ? workspace.workspace?.configurations.filter(c => c.projectId === projectId).length ?? 0 : 0;
  const next = !robots?.length ? !profiles?.length ? ["Profiles", "Import a robot profile", "Add the physical model and controller interfaces for your first robot."] : ["Robots", "Register your first robot", "Connect a computer to the robot profile you have imported."] : catalogue.applications.length === 0 ? ["Configurations", "Set up a configuration", "Choose the models, policy, and timing for your robots."] : ["Runs", "Review task results", "Inspect completed tasks, timing measurements, and recordings."];
  return <section className="project-section project-overview" aria-label="Project overview">
    <p className="project-intro">Your fleet, each robot’s configuration, and its current deployment status in one place.</p>
    <div className="project-metrics">{[["Robots", robots?.length, "Registered in this project"], ["Fleets", fleets?.length, "Groups of robots"], ["Configurations", catalogue.loading ? undefined : catalogue.applications.length + setups, `${catalogue.applications.length} runnable · ${setups} model setups`]].map(([label, count, detail]) => <Link className="project-metric" key={label} href={projectHref(projectId, String(label))}><span>{label}</span><strong>{count ?? "—"}</strong><small>{detail}</small></Link>)}</div>
    {catalogue.error && <p className="cv-notice cv-notice--error" role="alert">Configuration status could not be loaded. Refresh to try again.</p>}
    <div className="project-section-heading project-overview__heading"><div><h2>Fleets and robots</h2><p>Expand a fleet or open a robot to manage its configuration.</p></div><Link className="cv-link" href={projectHref(projectId, "Fleets")}>Manage fleets →</Link></div>
    <ProjectFleetDirectory projectId={projectId} fleets={fleets} robots={robots} catalogue={catalogue} />
    {!catalogue.loading && robots && profiles && <aside className="project-next-step"><div><h2>{next[1]}</h2><p>{next[2]}</p></div><Link className="cv-btn cv-btn--secondary" href={projectHref(projectId, next[0])}>{next[0] === "Runs" ? "View results" : "Continue setup"}</Link></aside>}
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
