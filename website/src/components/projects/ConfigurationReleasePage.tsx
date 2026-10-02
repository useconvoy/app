"use client";

import Link from "next/link";
import { useState } from "react";
import { PageHeader } from "@/components/configurations/AppShell";
import { useSession } from "@/components/configurations/Session";
import type { Application, Deployment, Release, Robot } from "@/lib/platform/client";
import { useProjectResource } from "@/lib/projects/client";
import { Shell } from "./Projects";
import { ConfigurationEditor, REFERENCE_RUNTIME } from "./ConfigurationEditor";
import { LinkedWorkspaceSpecifications } from "./WorkspaceConfigurationLink";

function download(name: string, value: string) {
  const url = URL.createObjectURL(new Blob([value + "\n"], { type: "application/json" }));
  const link = document.createElement("a"); link.href = url; link.download = name; link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function ConfigurationReleasePage({ configurationId }: { configurationId: string }) {
  const { operator } = useSession();
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState("");
  const [editing, setEditing] = useState(false);
  const [setupOpen, setSetupOpen] = useState(false);
  const application = useProjectResource<Application>(`applications/${configurationId}`);
  const projectId = application.data?.project_id;
  const releases = useProjectResource<Release[]>(projectId ? `applications/${configurationId}/releases` : null, revision);
  const robots = useProjectResource<Robot[]>(projectId ? `robots?project_id=${projectId}` : null, revision, true);
  const deployments = useProjectResource<Deployment[]>(projectId ? `deployments?project_id=${projectId}` : null, revision, true);
  const release = releases.data?.find(r => r.id === (selected || releases.data?.[0]?.id));
  const manifest = release?.manifest;
  const registered = manifest?.schema_version === 3 ? manifest : undefined;
  const matching = robots.data?.filter(robot => robot.simulated && robot.profile_id && registered && robot.qualification?.state === "passed" && robot.qualification.profile_digest === registered.environment.robot_profile_sha256);
  const setup = useProjectResource<{ release_id: string; manifest_json: string; reference_policy_json: string | null }>(setupOpen && release ? `applications/${configurationId}/releases/${release.id}/setup` : null);
  const reads = [application, releases, robots, deployments, setup];
  return <Shell project={application.data?.name ?? "Configuration"}>
    {projectId && <Link className="cv-link" href={`/app/projects/${projectId}`}>← Back to project</Link>}
    <PageHeader title={application.data?.name ?? "Configuration"} actions={operator && projectId ? <button className="cv-btn cv-btn--secondary" onClick={() => setEditing(value => !value)}>{editing ? "Close editor" : "Create new release"}</button> : undefined} />
    {reads.map((read, i) => read.error && <p role="alert" key={i}>{read.error}</p>)}
    {!application.data && !application.error && <p role="status">Loading configuration…</p>}
    {releases.data?.length === 0 && <p>No releases yet. Create one to configure a registered simulator.</p>}
    {!!releases.data?.length && <label className="cv-field">Configuration release<select className="cv-input" value={release?.id ?? ""} onChange={e => { setSelected(e.target.value); setEditing(false); }}>{releases.data.map(r => <option key={r.id} value={r.id}>{r.digest.slice(0, 12)}</option>)}</select></label>}
    {registered && <section aria-label="Release details">
      <h2>{registered.task.instruction}</h2>
      <p>{registered.policy.runtime === REFERENCE_RUNTIME ? "Joint-position reference controller · no learned model inference" : `Installed policy: ${registered.policy.runtime}`}</p>
      <p>MuJoCo {registered.environment.version} · {registered.interface.control_rate_hz} control steps/second · {registered.execution.timing ? "independent real-time simulation" : "functional simulation"}</p>
      {registered.execution.timing && <p>Maximum observation age: {registered.execution.timing.max_observation_age_ms} ms · Maximum physics lag: {registered.execution.timing.max_physics_lag_ms} ms · Fallback: simulated position hold</p>}
      <p>Policy response timeout: {registered.execution.decision_timeout_ms} ms · Task timeout: {registered.execution.mission_timeout_s} seconds · Maximum {registered.execution.max_steps} steps</p>
      <div className="cv-table-wrap"><table className="cv-table"><thead><tr><th scope="col">Joint</th><th scope="col">Target (SI units)</th><th scope="col">Limits</th></tr></thead><tbody>{registered.interface.joint_names.map((name, i) => <tr key={name}><th scope="row">{name}</th><td>{registered.task.target_joint_positions[i]}</td><td>{registered.interface.action_bounds[i].join(" to ")}</td></tr>)}</tbody></table></div>
      <p>Saving does not activate this release. On deployment, a configured managed runner prepares its reference policy automatically. Upload robot model files in the project’s Profiles section. Other policy workers still require operator setup.</p>
      <details onToggle={event => setSetupOpen(event.currentTarget.open)}><summary>Operator setup files</summary><p>For a reference policy, start the enrolled simulator with the managed-worker option to prepare it automatically on deployment. These files are available for manual worker setup and diagnosis.</p>
        <button className="cv-btn cv-btn--secondary" disabled={!setup.data || !!setup.error} onClick={() => download("release-manifest.json", setup.data!.manifest_json)}>Download release manifest</button>
        {setup.data?.reference_policy_json && <button className="cv-btn cv-btn--secondary" disabled={!!setup.error} onClick={() => download("joint-reference.json", setup.data!.reference_policy_json!)}>Download reference policy</button>}
      </details>
      <h2>Matching verified simulators</h2>
      {matching?.length === 0 && <p>No verified simulator matches this profile yet. Register and verify its simulated instance from the project.</p>}
      {matching?.map(robot => {
        const deployment = deployments.data?.find(d => d.robot_id === robot.id && d.generation === robot.generation);
        return <article className="cv-card" key={robot.id}><div><h3>{robot.name}</h3>
          <p>{deployment ? `Deployment ${deployment.generation} · ${deployment.state}${deployment.release_id === release?.id ? " · selected release" : " · another release"}` : "No deployment"}</p>
          <Link className="cv-link" href={`/app/projects/${projectId}/robots/${robot.id}?application_id=${configurationId}&release_id=${release!.id}`}>Open deployment and task controls →</Link>
        </div></article>;
      })}
    </section>}
    {manifest && !registered && <p>This release uses the existing {manifest.profile} execution interface. <Link className="cv-link" href="/app/applications">Open its existing application controls</Link>.</p>}
    {application.data && <LinkedWorkspaceSpecifications applicationId={application.data.id} />}
    {editing && application.data && <section aria-label="New configuration release"><h2>New release</h2><p>Existing robots keep their deployed version until you explicitly deploy this release.</p>
      <ConfigurationEditor key={release?.id ?? "new"} projectId={application.data.project_id} application={application.data} initial={registered} onSaved={saved => { setSelected(saved.id); setRevision(n => n + 1); setEditing(false); }} />
    </section>}
  </Shell>;
}
