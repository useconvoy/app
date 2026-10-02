"use client";

import Link from "next/link";
import { useState } from "react";
import { Badge, Missing } from "@/components/configurations/Badges";
import { DataTable, type Column } from "@/components/configurations/DataTable";
import { Notice } from "@/components/configurations/Notice";
import { useSession } from "@/components/configurations/Session";
import { EmptyState, LoadingState } from "@/components/configurations/States";
import { Card, Facts, Tile, Tiles } from "@/components/configurations/Tiles";
import { EpisodeReplay } from "@/components/console/EpisodeReplay";
import { Portal } from "@/components/portal/Portal";
import { useWorkspace } from "@/lib/configurations/client";
import { fmtCount, fmtSeconds, fmtWhen } from "@/lib/configurations/format";
import { routes } from "@/lib/configurations/routes";
import { notifySessionExpired } from "@/lib/configurations/session-events";
import type { Episode, Mission, Robot } from "@/lib/platform/client";
import { useProjectResource, type Fleet, type RobotProfile } from "@/lib/projects/client";
import { ProjectFleetDirectory } from "./ProjectFleetDirectory";
import { engineLabel, projectHref, robotHref } from "./ProjectNavigation";
import { currentRobotConfiguration, robotConfigurationState, type ProjectRobotConfigurationCatalogue } from "./ProjectRobotConfigurations";
import { readiness } from "./SimulatorReadiness";
import { TimingResult } from "./TimingResult";

export { robotHref };

/** Overview: four counts, the next setup step while one remains, and the fleets with their robots. */
export function ProjectOverview({ projectId, robots, profiles, fleets, catalogue, saved }: { projectId: string; robots?: Robot[]; profiles?: RobotProfile[]; fleets?: Fleet[]; catalogue: ProjectRobotConfigurationCatalogue; saved: number }) {
  const simulated = robots?.filter(robot => robot.simulated).length ?? 0;
  const assigned = robots?.filter(robot => fleets?.some(fleet => fleet.id === robot.fleet_id)).length ?? 0;
  const next = !robots || !profiles || catalogue.loading ? null
    : !profiles.length ? { section: "Profiles", text: "Import a robot profile to register your first robot." }
      : !robots.length ? { section: "Robots", text: "Register a robot to the profile you imported." }
        : !catalogue.applications.length ? { section: "Configurations", text: "Create a configuration to deploy to your robots." } : null;
  return <>
    <Tiles label="Summary">
      <Tile label="Robots" value={robots ? fmtCount(robots.length) : null} sub={robots ? `${simulated} simulated` : undefined} />
      <Tile label="Fleets" value={fleets ? fmtCount(fleets.length) : null} sub={robots && fleets ? `${assigned} of ${robots.length} robots assigned` : undefined} />
      <Tile label="Configurations" value={catalogue.loading ? null : fmtCount(catalogue.applications.length)} sub={`${saved} saved`} />
      <Tile label="Profiles" value={profiles ? fmtCount(profiles.length) : null} sub={profiles ? `${profiles.filter(profile => profile.simulation.engines.length).length} with simulation` : undefined} />
    </Tiles>
    {next && <div className="cv-strip cv-strip--static"><span>{next.text}</span><Link className="cv-btn cv-btn--secondary cv-btn--small" href={projectHref(projectId, next.section)}>Continue setup</Link></div>}
    {catalogue.error && <Notice tone="error">Configuration status could not be loaded. Refresh to try again.</Notice>}
    <section className="cv-row" aria-labelledby="po-fleets">
      <div className="cv-row__head"><h2 id="po-fleets">Fleets and robots</h2></div>
      <ProjectFleetDirectory projectId={projectId} fleets={fleets} robots={robots} catalogue={catalogue} />
    </section>
  </>;
}

/** Simulations: one equal card per simulated robot, opening its deployment and task controls. */
export function ProjectSimulations({ projectId, robots, profiles, catalogue }: { projectId: string; robots?: Robot[]; profiles?: RobotProfile[]; catalogue: ProjectRobotConfigurationCatalogue }) {
  const simulators = robots?.filter(robot => robot.simulated);
  if (!simulators) return <LoadingState />;
  if (!simulators.length) return <EmptyState title="No simulated robots yet." action={<Link className="cv-btn cv-btn--secondary" href={projectHref(projectId, "Robots")}>Open robots</Link>} />;
  return <section className="cv-grid" aria-label="Simulations">{simulators.map(robot => {
    const state = readiness(robot);
    const profile = profiles?.find(item => item.id === robot.profile_id);
    const { deployment, application } = currentRobotConfiguration(robot, catalogue);
    return <Link className="cv-config" key={robot.id} href={robotHref(projectId, robot.id)}>
      <div className="cv-config__head"><h2>{robot.name}</h2><Badge tone={state.tone}>{state.label}</Badge></div>
      <dl className="cv-config__facts cv-config__facts--wide">
        <div><dt>Engine</dt><dd>{engineLabel(robot.simulation_engine) ?? <Missing />}</dd></div>
        <div><dt>Profile</dt><dd>{profile ? `${profile.name} · revision ${profile.revision}` : <span className="cv-mono">{robot.profile}</span>}</dd></div>
        <div><dt>Configuration</dt><dd>{catalogue.loading ? <Missing label="Loading" /> : application?.name ?? <Missing label="Not configured" />}</dd></div>
        <div><dt>Deployment</dt><dd>{catalogue.loading ? <Missing label="Loading" /> : robotConfigurationState(deployment)}</dd></div>
      </dl>
    </Link>;
  })}</section>;
}

const TASK_TONE: Record<string, "success" | "warning" | "info" | "neutral"> = { completed: "success", failed: "warning", unknown: "warning", running: "info", requested: "info", cancel_requested: "info" };
/** "cancel_requested" → "Cancel requested". */
export const sentence = (state: string) => { const text = state.replaceAll("_", " "); return text.charAt(0).toUpperCase() + text.slice(1); };

/** Runs: the project's tasks, newest first; a finished task's result, timing and replay; the saved configurations' evals. */
export function ProjectRuns({ projectId, robots }: { projectId: string; robots?: Robot[] }) {
  const tasks = useProjectResource<Mission[]>(`missions?project_id=${projectId}`, 0, true);
  const [selected, setSelected] = useState<Mission>();
  const episode = useProjectResource<Episode>(selected?.episode_id ? `episodes/${selected.episode_id}` : null);
  const name = (task: Mission) => robots?.find(robot => robot.id === task.robot_id)?.name ?? task.robot_id;
  const columns: Array<Column<Mission>> = [
    { key: "robot", header: "Robot", cell: task => <Link className="cv-cell-link" href={robotHref(projectId, task.robot_id)}>{name(task)}</Link> },
    { key: "status", header: "Status", cell: task => <Badge tone={TASK_TONE[task.state] ?? "neutral"}>{sentence(task.state)}</Badge> },
    { key: "updated", header: "Updated (UTC)", wide: true, cell: task => fmtWhen(task.updated_at) },
    { key: "result", header: "Result", cell: task => task.episode_id ? <button className="cv-link" type="button" onClick={() => setSelected(task)}>View result</button> : <span className="cv-muted">{task.detail || "Awaiting result"}</span> },
  ];
  const summary = episode.data?.summary;
  return <>
    {tasks.error && <Notice tone="error">{tasks.error}</Notice>}
    <Card label="Runs" flush>{!tasks.data && !tasks.error ? <LoadingState label="Loading runs…" /> : <DataTable label="Runs" columns={columns} rows={tasks.data ?? []} rowKey={task => task.id} empty="No runs yet." />}</Card>
    {selected && <Card title="Task result" action={<Link className="cv-link" href={robotHref(projectId, selected.robot_id)}>Open robot</Link>}>
      {episode.error && <p className="cv-form-error" role="alert">{episode.error}</p>}
      {!episode.data && !episode.error && <LoadingState />}
      {episode.data && summary && <div className="cv-result">
        <Facts items={[
          { label: "Robot", value: name(selected) },
          { label: "Task success", value: typeof summary.final_success === "boolean" ? summary.final_success ? "Yes" : "No" : null },
          { label: "Simulated time", value: typeof summary.simulated_duration_s === "number" ? fmtSeconds(summary.simulated_duration_s) : null },
          { label: "Wall time", value: typeof summary.wall_duration_s === "number" ? fmtSeconds(summary.wall_duration_s) : null },
          { label: "Release", value: <span className="cv-mono">{episode.data.release_digest.slice(0, 12)}</span> },
        ]} />
        <TimingResult value={summary.timing} />
        <EpisodeReplay key={episode.data.id} episodeId={episode.data.id} />
      </div>}
    </Card>}
    <SavedResults projectId={projectId} />
  </>;
}

/** The evals of the saved configurations assigned to this project, one row per robot. */
function SavedResults({ projectId }: { projectId: string }) {
  const ws = useWorkspace();
  const configurations = ws.source === "document" ? ws.workspace?.configurations.filter(config => config.projectId === projectId) ?? [] : [];
  const rows = configurations.flatMap(config => (ws.workspace?.robots ?? []).filter(robot => robot.configId === config.id).map(robot => ({ config, robot })));
  if (!rows.length) return null;
  return <section className="cv-row" aria-labelledby="pr-saved">
    <div className="cv-row__head"><h2 id="pr-saved">Saved configurations</h2></div>
    <Card label="Saved evaluations" flush><DataTable label="Saved evaluations" rows={rows} rowKey={row => row.robot.id} rowHref={row => routes.robot(row.config.id, row.robot.id)} columns={[
      { key: "robot", header: "Robot", cell: row => row.robot.name },
      { key: "configuration", header: "Configuration", cell: row => row.config.name },
    ]} /></Card>
  </section>;
}

/** The workspace's configured device, inspected and tested on demand. */
export function ProjectConnectionTools() {
  const [open, setOpen] = useState(false);
  const { operator } = useSession();
  return <section className="cv-row" aria-label="Connection diagnostics">
    <div className="cv-strip cv-strip--static"><span>Workspace device</span><button className="cv-btn cv-btn--secondary cv-btn--small" type="button" aria-expanded={open} onClick={() => setOpen(value => !value)}>{open ? "Close device tools" : "Open device tools"}</button></div>
    {open && <div className="cv-embedded"><Portal active onSessionEnd={notifySessionExpired} canChat={operator} /></div>}
  </section>;
}
