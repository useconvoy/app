"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { newConfigurationHref, configurationHref } from "./ProjectConfigurations";
import { useState } from "react";
import { useSession } from "@/components/configurations/Session";
import { PageHeader } from "@/components/configurations/AppShell";
import { Card } from "@/components/configurations/Tiles";
import type { Application, Deployment, Episode, Mission, Release, Robot } from "@/lib/platform/client";
import { useProjectResource, type RobotProfile } from "@/lib/projects/client";
import { Shell, useMutation } from "./Projects";
import { TimingResult } from "./TimingResult";
import { SimulatorReadiness } from "./SimulatorReadiness";

const finished = (state: string) => ["completed", "failed", "cancelled"].includes(state);

export function RobotExecution({ projectId, robotId }: { projectId: string; robotId: string }) {
  const { operator } = useSession();
  const [revision, setRevision] = useState(0);
  const query = useSearchParams();
  const [applicationId, setApplicationId] = useState(query.get("application_id") ?? "");
  const [releaseId, setReleaseId] = useState(query.get("release_id") ?? "");
  const [episodeId, setEpisodeId] = useState<string | null>(null);
  const refresh = () => setRevision(n => n + 1);
  const mutation = useMutation(refresh);
  const robotRead = useProjectResource<Robot>(`robots/${robotId}`, revision, true);
  const robot = robotRead.data?.project_id === projectId ? robotRead.data : undefined;
  const profile = useProjectResource<RobotProfile>(robot?.profile_id ? `robot-profiles/${robot.profile_id}` : null, revision);
  const applications = useProjectResource<Application[]>(`applications?project_id=${projectId}`, revision);
  const selectedApplication = applicationId || applications.data?.[0]?.id;
  const releases = useProjectResource<Release[]>(selectedApplication ? `applications/${selectedApplication}/releases` : null, revision);
  const deployments = useProjectResource<Deployment[]>(`deployments?project_id=${projectId}&robot_id=${robotId}`, revision, true);
  const missions = useProjectResource<Mission[]>(`missions?project_id=${projectId}&robot_id=${robotId}`, revision, true);
  const episode = useProjectResource<Episode>(episodeId ? `episodes/${episodeId}` : null, revision);
  const tasks = missions.data?.filter(m => m.robot_id === robotId) ?? [];
  const active = tasks.find(m => !finished(m.state));
  const deployment = deployments.data?.find(d => d.generation === robot?.generation);
  const selectedReleaseId = releaseId || (releases.data?.some(r => r.id === deployment?.release_id) ? deployment!.release_id : "");
  const selectedRelease = releases.data?.find(r => r.id === selectedReleaseId);
  const supported = robot?.simulated && robot.profile === "registered-joint-policy-v1";
  const qualified = robot?.qualification?.state === "passed";
  const compatible = selectedRelease?.manifest.schema_version === 3 && selectedRelease.manifest.environment.robot_profile_sha256 === profile.data?.digest;
  const reads = [robotRead, profile, applications, releases, deployments, missions, episode];
  // A stale poll can still display the previous state, but it must not enable a new command.
  const fresh = reads.every(read => !read.error) && !!robot && !!deployments.data && !!missions.data;
  const canStart = operator && fresh && supported && qualified && !active && !robot.evaluation_id && deployment?.state === "ready" && selectedReleaseId === deployment.release_id;
  return <Shell project={robot?.name ?? "Robot"}>
    <Link className="cv-link" href={`/app/projects/${projectId}`}>← Back to project</Link>
    <PageHeader title={robot?.name ?? "Robot"} actions={<button className="cv-btn cv-btn--secondary" onClick={refresh}>Refresh</button>} />
    {reads.map((read, index) => read.error ? <p key={index} role="alert">{read.error}</p> : null)}
    {mutation.error && <p role="alert">{mutation.error}</p>}
    {robotRead.data && !robot && <p role="alert">This robot does not belong to this project.</p>}
    {robot && <>
      <p>{robot.simulated ? "Simulated robot" : "Physical robot"} · {profile.data ? `${profile.data.name} · revision ${profile.data.revision}` : "Profile unavailable"}</p>
      {!supported && <p>Task execution is not available for this robot interface yet.</p>}
      {supported && <>
        <Card title="Simulator readiness"><div className="robot-execution-content"><SimulatorReadiness robot={robot} busy={mutation.busy} canWrite={operator} verify={() => void mutation.submit(`robots/${robot.id}/qualification`, {})} /></div></Card>
        <Card title="Deployment"><div className="robot-execution-content">
          <p>{deployment ? `Deployment ${deployment.generation} · ${deployment.state}` : "No configuration deployed"}</p>
          {deployment?.observed_at && <p>Last reported: {new Date(deployment.observed_at).toLocaleString()}</p>}
          {deployment?.detail && <p>{deployment.detail}</p>}
          <p>Runs the pinned robot model in the selected release’s timing mode. Physical accuracy requires separate calibration.</p>
          {operator && <p><Link className="cv-link" href={newConfigurationHref(projectId, robot.profile_id)}>Create runnable configuration</Link></p>}
          {selectedApplication && <p><Link className="cv-link" href={configurationHref(selectedApplication)}>View configuration releases</Link></p>}
          {applications.data?.length === 0 ? <p>No runnable configurations in this project yet.</p> : <>
            <label className="cv-field">Configuration<select className="cv-input" value={selectedApplication ?? ""} onChange={e => { setApplicationId(e.target.value); setReleaseId(""); }}>
              {applications.data?.map(app => <option key={app.id} value={app.id}>{app.name}</option>)}
            </select></label>
            <label className="cv-field">Release<select className="cv-input" value={selectedReleaseId} onChange={e => setReleaseId(e.target.value)}>
              <option value="">Choose a release</option>{releases.data?.map(release => <option key={release.id} value={release.id}>{release.digest.slice(0, 12)}</option>)}
            </select></label>
            {selectedRelease && <p>{compatible ? "Matches this robot profile." : "This release does not match this robot profile."}</p>}
            {selectedRelease?.manifest.schema_version === 3 && <p>Task: {selectedRelease.manifest.task.instruction}</p>}
            {selectedRelease?.manifest.schema_version === 3 && selectedRelease.manifest.policy.runtime === "convoy-joint-target-reference-v1" && <p>Joint-position reference controller · no learned model inference.</p>}
            {deployment?.state === "ready" && selectedReleaseId !== deployment.release_id && <p>Choose the deployed release to run it, or deploy your new selection first.</p>}
          </>}
          {robot.evaluation_id && <p>This robot is reserved by an evaluation.</p>}
          {operator && <div className="project-actions">
            <button className="cv-btn cv-btn--secondary" disabled={mutation.busy || !fresh || !qualified || !compatible || !!active || !!robot.evaluation_id}
              onClick={() => void mutation.submit("deployments", { robot_id: robot.id, release_id: selectedReleaseId, expected_generation: robot.generation })}>Deploy selected release</button>
            <button className="cv-btn cv-btn--primary" disabled={mutation.busy || !canStart}
              onClick={() => void mutation.submit(`robots/${robot.id}/missions`, { deployment_id: deployment!.id, expected_generation: robot.generation, seed: 0, ttl_s: 60 })}>Start task</button>
          </div>}
        </div></Card>
      </>}
      <Card title="Tasks"><div className="robot-execution-content">
        {tasks.length === 0 && <p>No tasks yet.</p>}
        {tasks.map(task => <article className="robot-task" key={task.id}>
          <p><strong>{task.state === "cancel_requested" ? "Stop requested — waiting for robot" : task.state}</strong> · {new Date(task.updated_at).toLocaleString()}</p>
          {task.detail && <p>{task.detail}</p>}
          {operator && !finished(task.state) && <button className="cv-btn cv-btn--secondary" disabled={mutation.busy || task.state === "cancel_requested" || task.state === "unknown"}
            onClick={() => void mutation.submit(`missions/${task.id}/cancel`, { reason: "Stopped from robot page" })}>Stop task</button>}
          {task.state === "unknown" && <p>The execution outcome needs reconciliation before another task can start.</p>}
          {task.episode_id && <button className="cv-link" onClick={() => setEpisodeId(task.episode_id)}>View task result</button>}
          {episodeId === task.episode_id && episode.data && <div>
            <p>Task success: {typeof episode.data.summary.final_success === "boolean" ? episode.data.summary.final_success ? "Yes" : "No" : "Not reported"}</p>
            <p>{typeof episode.data.summary.steps === "number" ? `${episode.data.summary.steps} ${episode.data.summary.execution_mode === "independent_realtime_simulation" ? "applied policy actions" : "control steps"}` : "Step count not reported"}</p>
            <p>{episode.data.summary.execution_mode === "lockstep_offline" ? "Functional simulation · physics waits for each policy response" : episode.data.summary.execution_mode === "independent_realtime_simulation" ? "Measured real-time simulation · physics advances independently" : episode.data.summary.execution_mode === "not_started" ? "Task stopped before execution began" : "Execution timing mode not reported"}</p>
            <TimingResult value={episode.data.summary.timing} />
            <p>Simulated seconds: {String(episode.data.summary.simulated_duration_s ?? "Not reported")} · Elapsed seconds: {String(episode.data.summary.wall_duration_s ?? "Not reported")}</p>
          </div>}
        </article>)}
      </div></Card>
    </>}
  </Shell>;
}
