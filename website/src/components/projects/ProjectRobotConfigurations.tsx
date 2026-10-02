"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, ApiError, errorText, type Application, type Deployment, type Release, type Robot } from "@/lib/platform/client";
import { notifySessionExpired } from "@/lib/configurations/session-events";
import type { RobotProfile } from "@/lib/projects/client";
import { configurationHref } from "./ProjectConfigurations";

export interface ProjectRobotConfigurationCatalogue {
  applications: Application[];
  releases: Release[];
  deployments: Deployment[];
  loading: boolean;
  fresh: boolean;
  error?: string;
}

/** Read one shared catalogue for the project, rather than one release list per robot row. */
export function useProjectRobotConfigurations(projectId: string, revision = 0, robotId?: string): ProjectRobotConfigurationCatalogue {
  const key = `${projectId}:${revision}:${robotId ?? ""}`;
  const [snapshot, setSnapshot] = useState<{ key: string; applications: Application[]; releases: Release[]; deployments: Deployment[]; error?: string }>();
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function read() {
      try {
        const [applications, deployments] = await Promise.all([
          api<Application[]>(`applications?project_id=${encodeURIComponent(projectId)}`),
          api<Deployment[]>(`deployments?project_id=${encodeURIComponent(projectId)}&current_only=true${robotId ? `&robot_id=${encodeURIComponent(robotId)}` : ""}`),
        ]);
        const releases = (await Promise.all(applications.map(application => api<Release[]>(`applications/${application.id}/releases`)))).flat();
        if (active) setSnapshot({ key, applications, releases, deployments });
      } catch (cause) {
        if (!active) return;
        if (cause instanceof ApiError && cause.status === 401) { notifySessionExpired(); return; }
        setSnapshot(previous => ({ key, applications: previous?.key === key ? previous.applications : [], releases: previous?.key === key ? previous.releases : [], deployments: previous?.key === key ? previous.deployments : [], error: errorText(cause) }));
      }
      if (active) timer = setTimeout(tick, 5000);
    }
    function tick() {
      if (document.visibilityState === "visible") void read();
      else timer = setTimeout(tick, 5000);
    }
    void read();
    return () => { active = false; clearTimeout(timer); };
  }, [key, projectId, robotId]);
  const current = snapshot?.key === key ? snapshot : undefined;
  return { applications: current?.applications ?? [], releases: current?.releases ?? [], deployments: current?.deployments ?? [], loading: !current, fresh: !!current && !current.error, error: current?.error };
}

export function currentRobotConfiguration(robot: Robot, catalogue: ProjectRobotConfigurationCatalogue) {
  const deployment = catalogue.deployments.find(item => item.robot_id === robot.id && item.generation === robot.generation);
  const release = catalogue.releases.find(item => item.id === deployment?.release_id);
  const application = catalogue.applications.find(item => item.id === release?.application_id);
  return { deployment, release, application };
}

export const robotConfigurationState = (deployment?: Deployment) => !deployment ? "Not configured"
  : deployment.state === "ready" ? "Ready"
    : deployment.state === "requested" ? "Awaiting runner"
      : deployment.state.replaceAll("_", " ");

export function RobotConfigurationSummary({ projectId, robot, catalogue, compact = true }: {
  projectId: string; robot: Robot; catalogue: ProjectRobotConfigurationCatalogue; compact?: boolean;
}) {
  const { deployment, release, application } = currentRobotConfiguration(robot, catalogue);
  if (catalogue.loading) return <span>Loading configuration…</span>;
  if (catalogue.error) return <span>Configuration status unavailable</span>;
  return <div className="robot-configuration-summary">
    {application ? <Link className="cv-link" href={configurationHref(application.id)}>{application.name}</Link>
      : deployment ? <span>Configuration details unavailable</span>
        : <span>{robot.generation ? "Current configuration not reported" : "No configuration assigned"}</span>}
    <small>{deployment ? `${robotConfigurationState(deployment)}${release ? ` · release ${release.digest.slice(0, 12)}` : ""}` : robot.simulated === false ? "Execution interface unavailable" : "Select a configuration to deploy"}</small>
    {!compact && <Link className="cv-link" href={`/app/projects/${encodeURIComponent(projectId)}/robots/${encodeURIComponent(robot.id)}#robot-configuration`}>Manage configuration →</Link>}
  </div>;
}

/** Mirror the server's exact registered asset and joint contract before offering deployment. */
export function robotReleaseCompatible(robot: Robot, release: Release, profile?: RobotProfile) {
  const manifest = release.manifest;
  if (!robot.profile_id) return manifest.schema_version !== 3 && manifest.profile === robot.profile;
  if (manifest.schema_version !== 3 || !profile || robot.simulated !== true) return false;
  const spec = profile.spec;
  if (!spec || !Array.isArray(spec.simulations) || !Array.isArray(spec.joints)) return false;
  const model = spec.simulations.find(item => item.engine === robot.simulation_engine);
  const joints = spec.joints.filter(joint => joint.kind !== "fixed");
  if (!model?.asset?.sha256 || !model.engine_version || !manifest.environment || !manifest.interface || !joints.length) return false;
  return manifest.environment.engine === robot.simulation_engine
    && manifest.environment.version === model.engine_version
    && manifest.environment.robot_profile_sha256 === profile.digest
    && manifest.environment.asset_sha256 === model.asset.sha256
    && model.controller === "position"
    && manifest.interface.command_interface === spec.command_interface
    && manifest.interface.control_rate_hz === spec.control_rate_hz
    && JSON.stringify(manifest.interface.joint_names) === JSON.stringify(joints.map(joint => joint.name))
    && JSON.stringify(manifest.interface.action_bounds) === JSON.stringify(joints.map(joint => [joint.lower, joint.upper]));
}
