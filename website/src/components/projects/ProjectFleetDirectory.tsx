"use client";

import Link from "next/link";
import { Icon } from "@/components/configurations/Icons";
import { EmptyState, LoadingState } from "@/components/configurations/States";
import type { Robot } from "@/lib/platform/client";
import type { Fleet } from "@/lib/projects/client";
import { engineLabel, robotHref } from "./ProjectNavigation";
import { RobotConfigurationSummary, type ProjectRobotConfigurationCatalogue } from "./ProjectRobotConfigurations";

/** Each fleet as a disclosure of its robots (with their configuration), then the robots in no fleet. */
export function ProjectFleetDirectory({ projectId, fleets, robots, catalogue, onRemove, busy = false }: {
  projectId: string;
  fleets?: Fleet[];
  robots?: Robot[];
  catalogue: ProjectRobotConfigurationCatalogue;
  onRemove?: (fleetId: string, robotId: string) => void;
  busy?: boolean;
}) {
  if (!fleets || !robots) return <section className="project-fleet-directory" aria-label="Project fleets"><LoadingState label="Loading fleets…" /></section>;
  const unassigned = robots.filter(robot => !robot.fleet_id || !fleets.some(fleet => fleet.id === robot.fleet_id));
  function row(robot: Robot, fleet?: Fleet) {
    return <article className="project-robot-row" key={robot.id}>
      <div className="project-robot-row__identity">
        <Link className="cv-cell-link" href={robotHref(projectId, robot.id)}>{robot.name}</Link>
        <span className="project-row-meta">{robot.simulated === false ? "Physical robot" : "Simulated robot"}{robot.simulation_engine ? ` · ${engineLabel(robot.simulation_engine)}` : ""}</span>
      </div>
      <RobotConfigurationSummary projectId={projectId} robot={robot} catalogue={catalogue} compact={false} />
      {fleet && onRemove && <button className="cv-link project-robot-row__remove" type="button" disabled={busy} onClick={() => onRemove(fleet.id, robot.id)}>Remove from {fleet.name}</button>}
    </article>;
  }
  const count = (n: number) => `${n} ${n === 1 ? "robot" : "robots"}`;
  return <section className="project-fleet-directory" aria-label="Project fleets">
    {fleets.map(fleet => <details className="project-fleet" key={fleet.id}>
      <summary><h2>{fleet.name}</h2><span className="cv-muted">{count(fleet.robot_ids.length)}</span><Icon name="chevron-right" className="project-fleet__chevron" /></summary>
      <div className="project-fleet__members">
        {fleet.robot_ids.map(id => { const robot = robots.find(item => item.id === id); return robot ? row(robot, fleet) : <p className="project-fleet__empty" key={id}>Robot details unavailable</p>; })}
        {fleet.robot_ids.length === 0 && <p className="project-fleet__empty">No robots in this fleet. Select robots on the Robots tab to assign them.</p>}
      </div>
    </details>)}
    {unassigned.length > 0 && <section className="project-unassigned" aria-label="Unassigned robots">
      <div className="project-fleet__summary"><h2>{fleets.length ? "Not in a fleet" : "Robots"}</h2><span className="cv-muted">{count(unassigned.length)}</span></div>
      {unassigned.map(robot => row(robot))}
    </section>}
    {fleets.length === 0 && robots.length === 0 && <EmptyState title="No robots yet." />}
  </section>;
}
