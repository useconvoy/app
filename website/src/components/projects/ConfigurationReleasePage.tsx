"use client";

import Link from "next/link";
import { useState } from "react";
import { PageHeader } from "@/components/configurations/AppShell";
import { Badge } from "@/components/configurations/Badges";
import { DataTable } from "@/components/configurations/DataTable";
import { Notice } from "@/components/configurations/Notice";
import { useSession } from "@/components/configurations/Session";
import { EmptyState, LoadingState } from "@/components/configurations/States";
import { Card, Facts } from "@/components/configurations/Tiles";
import type { Application, Deployment, Project, Release, Robot } from "@/lib/platform/client";
import { useProjectResource } from "@/lib/projects/client";
import { ConfigurationEditor, REFERENCE_RUNTIME } from "./ConfigurationEditor";
import { projectCrumbs, robotHref } from "./ProjectNavigation";
import { ProjectShell } from "./Projects";
import { LinkedWorkspaceSpecifications } from "./WorkspaceConfigurationLink";

function download(name: string, value: string) {
  const url = URL.createObjectURL(new Blob([value + "\n"], { type: "application/json" }));
  const link = document.createElement("a"); link.href = url; link.download = name; link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** A runnable project configuration (`/app/configurations/[id]?source=project`): its releases, what each pins, and the verified robots it can deploy to. */
export function ConfigurationReleasePage({ configurationId }: { configurationId: string }) {
  const { operator } = useSession();
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState("");
  const [editing, setEditing] = useState(false);
  const [setupOpen, setSetupOpen] = useState(false);
  const application = useProjectResource<Application>(`applications/${configurationId}`);
  const projectId = application.data?.project_id;
  const projects = useProjectResource<Project[]>(projectId ? "projects" : null);
  const releases = useProjectResource<Release[]>(projectId ? `applications/${configurationId}/releases` : null, revision);
  const robots = useProjectResource<Robot[]>(projectId ? `robots?project_id=${projectId}` : null, revision, true);
  const deployments = useProjectResource<Deployment[]>(projectId ? `deployments?project_id=${projectId}` : null, revision, true);
  const release = releases.data?.find(r => r.id === (selected || releases.data?.[0]?.id));
  const manifest = release?.manifest;
  const registered = manifest?.schema_version === 3 ? manifest : undefined;
  const matching = robots.data?.filter(robot => robot.simulated && robot.profile_id && registered && robot.qualification?.state === "passed" && robot.qualification.profile_digest === registered.environment.robot_profile_sha256);
  const setup = useProjectResource<{ release_id: string; manifest_json: string; reference_policy_json: string | null }>(setupOpen && release ? `applications/${configurationId}/releases/${release.id}/setup` : null);
  const errors = [...new Set([application, releases, robots, deployments, setup].map(read => read.error).filter((error): error is string => !!error))];
  const name = application.data?.name ?? "Configuration";
  const timing = registered?.execution.timing;
  return <ProjectShell crumbs={projectCrumbs(projectId ? { id: projectId, name: projects.data?.find(item => item.id === projectId)?.name } : null, { label: name })}>
    <PageHeader title={name} badges={registered && <Badge>{timing ? "Measured real time" : "Functional"}</Badge>}
      actions={operator && projectId ? <button className="cv-btn cv-btn--primary" type="button" onClick={() => setEditing(value => !value)}>{editing ? "Close editor" : "Create new release"}</button> : undefined} />
    {errors.map(error => <Notice key={error} tone="error">{error}</Notice>)}
    {!application.data && !application.error && <LoadingState />}
    {editing && application.data && <section className="cv-row cv-row--first" aria-label="New configuration release">
      <div className="cv-row__head"><h2>New release</h2></div>
      <ConfigurationEditor key={release?.id ?? "new"} projectId={application.data.project_id} application={application.data} initial={registered} onSaved={saved => { setSelected(saved.id); setRevision(n => n + 1); setEditing(false); }} />
    </section>}
    {releases.data?.length === 0 && <EmptyState title="No releases yet." />}
    {!!releases.data?.length && <div className="cv-toolbar"><label className="cv-field cv-field--inline">Configuration release<select className="cv-input" value={release?.id ?? ""} onChange={e => { setSelected(e.target.value); setEditing(false); }}>{releases.data.map(r => <option key={r.id} value={r.id}>{r.digest.slice(0, 12)}</option>)}</select></label></div>}
    {registered && <>
      <Card label="Release details">
        <Facts items={[
          { label: "Task", value: registered.task.instruction },
          { label: "Policy", value: registered.policy.runtime === REFERENCE_RUNTIME ? "Reference controller · no learned model" : `Installed: ${registered.policy.runtime}` },
          { label: "Simulator", value: `MuJoCo ${registered.environment.version}` },
          { label: "Control rate", value: `${registered.interface.control_rate_hz} steps/s` },
          ...(timing ? [{ label: "Observation age limit", value: `${timing.max_observation_age_ms} ms` }, { label: "Physics lag limit", value: `${timing.max_physics_lag_ms} ms` }, { label: "Fallback", value: "Simulated position hold" }] : []),
          { label: "Policy timeout", value: `${registered.execution.decision_timeout_ms} ms` },
          { label: "Task timeout", value: `${registered.execution.mission_timeout_s} s` },
          { label: "Step limit", value: String(registered.execution.max_steps) },
        ]} />
      </Card>
      <section className="cv-row" aria-labelledby="cr-joints">
        <div className="cv-row__head"><h2 id="cr-joints">Joint targets</h2></div>
        <Card flush><div className="cv-table-wrap"><table className="cv-table" aria-labelledby="cr-joints"><thead><tr><th scope="col">Joint</th><th scope="col" className="cv-num">Target (SI units)</th><th scope="col" className="cv-num">Limits</th></tr></thead><tbody>
          {registered.interface.joint_names.map((joint, i) => <tr key={joint}><th scope="row">{joint}</th><td className="cv-num">{registered.task.target_joint_positions[i]}</td><td className="cv-num">{registered.interface.action_bounds[i].join(" to ")}</td></tr>)}
        </tbody></table></div></Card>
      </section>
      <section className="cv-row" aria-labelledby="cr-robots">
        <div className="cv-row__head"><h2 id="cr-robots">Verified simulators</h2></div>
        {matching && <Card flush><DataTable label="Matching verified simulators" rows={matching} rowKey={robot => robot.id}
          rowHref={robot => `${robotHref(projectId!, robot.id)}?application_id=${configurationId}&release_id=${release!.id}`}
          empty="No verified simulator matches this profile yet."
          columns={[
            { key: "robot", header: "Robot", cell: robot => robot.name },
            { key: "deployment", header: "Deployment", cell: robot => {
              const deployment = deployments.data?.find(item => item.robot_id === robot.id && item.generation === robot.generation);
              return deployment ? `Deployment ${deployment.generation} · ${deployment.state}${deployment.release_id === release?.id ? " · this release" : " · another release"}` : "Not deployed";
            } },
          ]} /></Card>}
      </section>
      <details className="cv-card cv-disclosure cv-disclosure--card" onToggle={event => setSetupOpen(event.currentTarget.open)}><summary>Operator setup files</summary>
        <div className="cv-actions">
          <button className="cv-btn cv-btn--secondary cv-btn--small" type="button" disabled={!setup.data || !!setup.error} onClick={() => download("release-manifest.json", setup.data!.manifest_json)}>Download release manifest</button>
          {setup.data?.reference_policy_json && <button className="cv-btn cv-btn--secondary cv-btn--small" type="button" disabled={!!setup.error} onClick={() => download("joint-reference.json", setup.data!.reference_policy_json!)}>Download reference policy</button>}
        </div>
      </details>
    </>}
    {manifest && !registered && <Notice>This release uses the existing {manifest.profile} interface. <Link className="cv-link" href="/app/applications">Open its application controls</Link></Notice>}
    {application.data && <LinkedWorkspaceSpecifications applicationId={application.data.id} />}
  </ProjectShell>;
}
