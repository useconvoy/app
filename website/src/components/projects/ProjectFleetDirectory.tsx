"use client";

import Link from "next/link";
import type { Robot } from "@/lib/platform/client";
import type { Fleet } from "@/lib/projects/client";
import { RobotConfigurationSummary, type ProjectRobotConfigurationCatalogue } from "./ProjectRobotConfigurations";

export function ProjectFleetDirectory({ projectId, fleets, robots, catalogue, onRemove, busy = false }: {
  projectId: string;
  fleets?: Fleet[];
  robots?: Robot[];
  catalogue: ProjectRobotConfigurationCatalogue;
  onRemove?: (fleetId: string, robotId: string) => void;
  busy?: boolean;
}) {
  const unassigned = robots?.filter(robot => !robot.fleet_id || !fleets?.some(fleet => fleet.id === robot.fleet_id));
  function row(robot: Robot, fleet?: Fleet) {
    return <article className="project-robot-row" key={robot.id}>
      <div className="project-robot-row__identity">
        <Link href={`/app/projects/${projectId}/robots/${robot.id}`}>{robot.name}</Link>
        <span className="project-row-meta">{robot.simulated === false ? "Physical robot" : "Simulated robot"}{robot.simulation_engine ? ` · ${robot.simulation_engine === "isaac" ? "Isaac Sim" : "MuJoCo"}` : ""}</span>
      </div>
      <RobotConfigurationSummary projectId={projectId} robot={robot} catalogue={catalogue} compact={false} />
      {fleet && onRemove && <button className="cv-link project-robot-row__remove" disabled={busy} onClick={() => onRemove(fleet.id, robot.id)}>Remove from {fleet.name}</button>}
    </article>;
  }
  return <section className="project-fleet-directory" aria-label="Project fleets">
    {!fleets || !robots ? <p role="status">Loading fleets and robots…</p> : <>
      {fleets.map(fleet => <details className="project-fleet" key={fleet.id}>
        <summary><div><h2>{fleet.name}</h2><span className="cv-muted">{fleet.robot_ids.length} {fleet.robot_ids.length === 1 ? "robot" : "robots"}</span></div><span className="project-fleet__chevron" aria-hidden="true">⌄</span></summary>
        <div className="project-fleet__members">
          {fleet.robot_ids.map(id => { const robot = robots.find(item => item.id === id); return robot ? row(robot, fleet) : <p className="cv-muted" key={id}>Robot details unavailable</p>; })}
          {fleet.robot_ids.length === 0 && <p className="project-fleet__empty">No robots in this fleet. Select robots from the Robots page to assign them.</p>}
        </div>
      </details>)}
      {!!unassigned?.length && <section className="project-unassigned" aria-label="Unassigned robots">
        <div className="project-section-heading"><h2>Unassigned robots</h2><span className="cv-muted">{unassigned.length} {unassigned.length === 1 ? "robot" : "robots"}</span></div>
        <p className="project-unassigned__note">These robots are in your project and can be added to a fleet.</p>
        {unassigned.map(robot => row(robot))}
      </section>}
      {fleets.length === 0 && robots.length === 0 && <div className="cv-empty"><h2>Your robots will appear here</h2><p>Register a robot, then organize it into a fleet.</p></div>}
    </>}
  </section>;
}
