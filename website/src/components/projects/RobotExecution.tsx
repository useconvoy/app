"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useState } from "react";
import { PageHeader } from "@/components/configurations/AppShell";
import { Badge } from "@/components/configurations/Badges";
import { Notice } from "@/components/configurations/Notice";
import { useSession } from "@/components/configurations/Session";
import { EmptyState, LoadingState } from "@/components/configurations/States";
import { Card, Facts } from "@/components/configurations/Tiles";
import { fmtWhen } from "@/lib/configurations/format";
import { notifySessionExpired } from "@/lib/configurations/session-events";
import type { Episode, Mission, Project, Qualification, Robot } from "@/lib/platform/client";
import { useProjectResource, type RobotProfile } from "@/lib/projects/client";
import { ComputerDetails } from "./ConnectionSetup";
import { PolicyTools } from "./policy-tools/PolicyTools";
import { configurationHref, newConfigurationHref } from "./ProjectConfigurations";
import { engineLabel, projectCrumbs } from "./ProjectNavigation";
import { currentRobotConfiguration, robotReleaseCompatible, useProjectRobotConfigurations, RobotConfigurationSummary } from "./ProjectRobotConfigurations";
import { ProjectShell, useMutation } from "./Projects";
import { readiness, SimulatorReadiness } from "./SimulatorReadiness";
import { TimingResult } from "./TimingResult";

const finished = (state: string) => ["completed", "failed", "cancelled"].includes(state);

/** A registered robot (`/app/projects/[id]/robots/[robotId]`): its computer, readiness, configuration and deployment, and tasks. */
export function RobotExecution({ projectId, robotId }: { projectId: string; robotId: string }) {
  const { operator, account } = useSession();
  const [revision, setRevision] = useState(0);
  const query = useSearchParams();
  const [applicationId, setApplicationId] = useState(query.get("application_id") ?? "");
  const [releaseId, setReleaseId] = useState(query.get("release_id") ?? "");
  const [episodeId, setEpisodeId] = useState<string | null>(null);
  const [advancedTools, setAdvancedTools] = useState(false);
  const refresh = () => setRevision(n => n + 1);
  const mutation = useMutation(refresh);
  const projects = useProjectResource<Project[]>("projects");
  const project = projects.data?.find(item => item.id === projectId);
  const robotRead = useProjectResource<Robot>(`robots/${robotId}`, revision, true);
  const robot = robotRead.data?.project_id === projectId ? robotRead.data : undefined;
  const profile = useProjectResource<RobotProfile>(robot?.profile_id ? `robot-profiles/${robot.profile_id}` : null, revision);
  const catalogue = useProjectRobotConfigurations(projectId, revision, robotId);
  const current = robot ? currentRobotConfiguration(robot, catalogue) : undefined;
  const deployment = current?.deployment;
  const compatibleApplications = robot ? catalogue.applications.filter(app => catalogue.releases.some(release => release.application_id === app.id && robotReleaseCompatible(robot, release, profile.data))) : [];
  const selectedApplication = applicationId || current?.application?.id || compatibleApplications[0]?.id;
  const releases = catalogue.releases.filter(release => release.application_id === selectedApplication);
  const missions = useProjectResource<Mission[]>(`missions?project_id=${projectId}&robot_id=${robotId}`, revision, true);
  const episode = useProjectResource<Episode>(episodeId ? `episodes/${episodeId}` : null, revision);
  const tasks = missions.data?.filter(m => m.robot_id === robotId) ?? [];
  const active = tasks.find(m => !finished(m.state));
  const selectedReleaseId = releaseId || (releases.some(r => r.id === deployment?.release_id) ? deployment!.release_id
    : releases.find(release => robot && robotReleaseCompatible(robot, release, profile.data))?.id ?? "");
  const selectedRelease = releases.find(r => r.id === selectedReleaseId);
  const registered = !!robot?.profile_id;
  const supported = !!robot && robot.simulated !== false && (registered
    ? robot.simulated === true && robot.simulation_engine === "mujoco" && profile.data?.spec?.command_interface === "joint-position" && (robot.profile === "registered-joint-policy-v1" || robot.profile === "custom-unqualified" && !profile.data?.spec?.execution_profile)
    : (account.installation.execution_profiles ?? []).includes(robot.profile));
  const qualified = !registered || robot?.qualification?.state === "passed";
  const compatible = !!robot && !!selectedRelease && robotReleaseCompatible(robot, selectedRelease, profile.data);
  const qualification = useProjectResource<Qualification>(selectedApplication && selectedReleaseId ? `applications/${selectedApplication}/qualification?release_id=${selectedReleaseId}` : null, revision, true);
  const releaseQualified = qualification.data?.release_id === selectedReleaseId && qualification.data.deployment_allowed;
  const reads = [robotRead, profile, missions, episode, qualification];
  // A stale poll can still display the previous state, but it must not enable a new command.
  const fresh = reads.every(read => !read.error) && catalogue.fresh && !!robot && !!missions.data;
  const canDispatch = account.installation.simulator && !account.installation.dispatch_paused_at && !account.installation.quarantined_at;
  const canStart = operator && fresh && canDispatch && supported && qualified && compatible && releaseQualified && !active && !robot.evaluation_id && deployment?.state === "ready" && selectedReleaseId === deployment.release_id;
  const errors = [...new Set([...reads.map(read => read.error), catalogue.error, mutation.error].filter((error): error is string => !!error))];
  const state = robot?.simulated ? readiness(robot) : null;
  return <ProjectShell crumbs={projectCrumbs({ id: projectId, name: project?.name }, { label: robot?.name ?? "Robot" })}>
    <PageHeader title={robot?.name ?? "Robot"} badges={robot && <><Badge>{robot.simulated ? "Simulated" : "Physical"}</Badge>{state && <Badge tone={state.tone}>{state.label}</Badge>}</>}
      actions={<button className="cv-btn cv-btn--secondary" type="button" onClick={refresh}>Refresh</button>} />
    {errors.map(error => <Notice key={error} tone="error">{error}</Notice>)}
    {robotRead.data && !robot && <Notice tone="error">This robot does not belong to this project.</Notice>}
    {!robotRead.data && !robotRead.error && <LoadingState />}
    {robot && <>
      <Card title="Robot">
        <Facts items={[
          { label: "Type", value: robot.simulated ? "Simulated robot" : "Physical robot" },
          { label: "Profile", value: profile.data ? `${profile.data.name} · revision ${profile.data.revision}` : registered ? null : "Existing runner" },
          { label: "Engine", value: robot.simulated ? engineLabel(robot.simulation_engine) ?? "Existing runtime" : null },
          { label: "Generation", value: String(robot.generation) },
        ]} />
        <ComputerDetails deviceId={robot.device_id} />
      </Card>
      {registered && robot.simulated && <Card title="Simulator readiness"><div className="cv-card__body"><SimulatorReadiness robot={robot} busy={mutation.busy} canWrite={operator} verify={() => void mutation.submit(`robots/${robot.id}/qualification`, {})} /></div></Card>}
      <section id="robot-configuration" className="cv-card" aria-label="Robot configuration">
        <div className="cv-card__head"><h2>Configuration</h2><span className="cv-actions">
          {selectedApplication && <Link className="cv-link" href={configurationHref(selectedApplication)}>View releases</Link>}
          {operator && <Link className="cv-link" href={newConfigurationHref(projectId, robot.profile_id)}>Create runnable configuration</Link>}
        </span></div>
        <div className="cv-card__body">
          <div className="cv-current"><RobotConfigurationSummary projectId={projectId} robot={robot} catalogue={catalogue} />
            {deployment && <p>Deployment {deployment.generation} · {deployment.state}</p>}
            {deployment?.observed_at && <p className="cv-muted">Last reported {fmtWhen(deployment.observed_at)} UTC</p>}
            {deployment?.detail && <p className="cv-muted">{deployment.detail}</p>}
          </div>
          {catalogue.loading ? <LoadingState label="Loading runnable configurations…" /> : catalogue.applications.length === 0 ? <p className="cv-muted">No runnable configurations in this project yet.</p> : <>
            <div className="cv-form__grid">
              <label className="cv-field">Configuration<select className="cv-input" value={selectedApplication ?? ""} onChange={e => { setApplicationId(e.target.value); setReleaseId(""); }}>
                <option value="">Choose a configuration</option>{catalogue.applications.map(app => <option key={app.id} value={app.id} disabled={!compatibleApplications.some(compatibleApp => compatibleApp.id === app.id)}>{app.name}{compatibleApplications.some(compatibleApp => compatibleApp.id === app.id) ? "" : " · no compatible release"}</option>)}
              </select></label>
              <label className="cv-field">Release<select className="cv-input" value={selectedReleaseId} onChange={e => setReleaseId(e.target.value)}>
                <option value="">Choose a release</option>{releases.map(release => <option key={release.id} value={release.id} disabled={!robotReleaseCompatible(robot, release, profile.data)}>{release.digest.slice(0, 12)}{release.id === deployment?.release_id ? " · current" : ""}{robotReleaseCompatible(robot, release, profile.data) ? "" : " · incompatible"}</option>)}
              </select></label>
            </div>
            <div className="cv-notes">
              {selectedRelease && <p>{compatible ? "Matches this robot profile and execution interface." : "This release does not match this robot profile and execution interface."}</p>}
              {selectedRelease?.manifest.schema_version === 3 && <p>Task: {selectedRelease.manifest.task.instruction}</p>}
              {selectedRelease?.manifest.schema_version === 2 && <p>Task: {selectedRelease.manifest.task.instruction}</p>}
              {selectedRelease?.manifest.schema_version === 3 && selectedRelease.manifest.policy.runtime === "convoy-joint-target-reference-v1" && <p>Joint-position reference controller · no learned model inference.</p>}
              {deployment?.state === "ready" && selectedReleaseId !== deployment.release_id && <p>Choose the deployed release to run it, or deploy your new selection first.</p>}
              {selectedRelease && !qualification.data && !qualification.error && <p role="status">Checking release qualification…</p>}
              {qualification.data && !releaseQualified && <p>This release must pass its configured evaluation gate before deployment.</p>}
              {compatibleApplications.length === 0 && <p>No release matches this robot’s current profile and controller.</p>}
            </div>
          </>}
          <div className="cv-notes">
            {!supported && <p>Deployment and task execution are unavailable for this robot interface.</p>}
            {supported && !qualified && <p>Verify this simulator before deploying a configuration.</p>}
            {!canDispatch && <p>Task dispatch is unavailable or paused on this installation.</p>}
            {robot.evaluation_id && <p>This robot is reserved by an evaluation.</p>}
          </div>
          {operator && supported && <div className="cv-actions cv-actions--end">
            <button className="cv-btn cv-btn--secondary" type="button" disabled={mutation.busy || !fresh || !canDispatch || !qualified || !compatible || !releaseQualified || !!active || !!robot.evaluation_id || deployment?.release_id === selectedReleaseId && !["failed", "blocked"].includes(deployment.state)}
              onClick={() => void mutation.submit("deployments", { robot_id: robot.id, release_id: selectedReleaseId, expected_generation: robot.generation })}>Deploy selected release</button>
            <button className="cv-btn cv-btn--primary" type="button" disabled={mutation.busy || !canStart}
              onClick={() => void mutation.submit(`robots/${robot.id}/missions`, { deployment_id: deployment!.id, expected_generation: robot.generation, seed: 0, ttl_s: 60 })}>Start task</button>
          </div>}
        </div>
      </section>
      {!registered && robot.simulated !== false && <details className="cv-card cv-disclosure cv-disclosure--card" onToggle={event => setAdvancedTools(event.currentTarget.open)}><summary>Advanced policy and evaluation tools</summary>{advancedTools && <section className="console-shell cv-embedded" aria-label="Existing policy runtime"><PolicyTools project={{ id: projectId, name: robot.name }} initialRobotId={robot.id} writable={operator} canDispatch={canDispatch} executionProfiles={account.installation.execution_profiles ?? []} onSessionEnd={notifySessionExpired} /></section>}</details>}
      <Card title="Tasks"><div className="cv-card__body">
        {tasks.length === 0 && <EmptyState title="No tasks yet." />}
        {tasks.map(task => <article className="robot-task" key={task.id}>
          <div className="robot-task__head"><strong>{task.state === "cancel_requested" ? "Stop requested — waiting for robot" : task.state}</strong><span className="cv-muted">{fmtWhen(task.updated_at)} UTC</span>
            <span className="cv-actions">
              {task.episode_id && <button className="cv-link" type="button" onClick={() => setEpisodeId(task.episode_id)}>View task result</button>}
              {operator && !finished(task.state) && <button className="cv-btn cv-btn--secondary cv-btn--small" type="button" disabled={mutation.busy || task.state === "cancel_requested" || task.state === "unknown"}
                onClick={() => void mutation.submit(`missions/${task.id}/cancel`, { reason: "Stopped from robot page" })}>Stop task</button>}
            </span></div>
          {task.detail && <p className="cv-muted">{task.detail}</p>}
          {task.state === "unknown" && <p>The execution outcome needs reconciliation before another task can start.</p>}
          {episodeId === task.episode_id && episode.data && <div className="robot-task__result">
            <Facts items={[
              { label: "Task success", value: typeof episode.data.summary.final_success === "boolean" ? episode.data.summary.final_success ? "Yes" : "No" : null },
              { label: "Steps", value: typeof episode.data.summary.steps === "number" ? `${episode.data.summary.steps} ${episode.data.summary.execution_mode === "independent_realtime_simulation" ? "applied policy actions" : "control steps"}` : null },
              { label: "Execution", value: episode.data.summary.execution_mode === "lockstep_offline" ? "Functional simulation · physics waits for each policy response" : episode.data.summary.execution_mode === "independent_realtime_simulation" ? "Measured real-time simulation · physics advances independently" : episode.data.summary.execution_mode === "not_started" ? "Task stopped before execution began" : "Execution timing mode not reported" },
              { label: "Simulated time", value: typeof episode.data.summary.simulated_duration_s === "number" ? `${episode.data.summary.simulated_duration_s} s` : null },
              { label: "Elapsed time", value: typeof episode.data.summary.wall_duration_s === "number" ? `${episode.data.summary.wall_duration_s} s` : null },
            ]} />
            <TimingResult value={episode.data.summary.timing} />
          </div>}
        </article>)}
      </div></Card>
    </>}
  </ProjectShell>;
}
