"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent, type ReactNode } from "react";
import { PageHeader } from "@/components/configurations/AppShell";
import { Notice } from "@/components/configurations/Notice";
import { useSession } from "@/components/configurations/Session";
import { EmptyState, LoadingState } from "@/components/configurations/States";
import type { Application, Project, Release } from "@/lib/platform/client";
import type { RegisteredManifest } from "@/lib/platform/manifest";
import { useProjectResource, type RobotProfile } from "@/lib/projects/client";
import { configurationHref } from "./ProjectConfigurations";
import { projectCrumbs, projectHref, REFERENCE_RUNTIME } from "./ProjectNavigation";
import { ProjectShell, useMutation } from "./Projects";

export { REFERENCE_RUNTIME };
function supported(profile: RobotProfile) {
  const joints = profile.spec.joints.filter(j => j.kind !== "fixed");
  return profile.spec.command_interface === "joint-position" && [undefined, null, "registered-joint-policy-v1"].includes(profile.spec.execution_profile)
    && profile.spec.simulations?.some(m => m.engine === "mujoco" && m.controller === "position")
    && joints.length > 0 && joints.every(j => Number.isFinite(j.lower) && Number.isFinite(j.upper));
}

/** New runnable configuration (`/app/configurations/new?project_id=…`): one form, then its first release. */
export function NewProjectConfiguration({ projectId, profileId }: { projectId: string; profileId?: string }) {
  const projects = useProjectResource<Project[]>("projects");
  const project = projects.data?.find(item => item.id === projectId);
  return <ProjectShell crumbs={projectCrumbs({ id: projectId, name: project?.name }, { label: "New configuration" })}>
    <PageHeader title="Create runnable configuration" />
    <ConfigurationEditor projectId={projectId} preferredProfileId={profileId} />
  </ProjectShell>;
}

/** The release form for a compatible profile (bounded joint positions, a MuJoCo position controller). */
export function ConfigurationEditor({ projectId, preferredProfileId, application, initial, onSaved }: {
  projectId: string; preferredProfileId?: string; application?: Application; initial?: RegisteredManifest; onSaved?: (release: Release) => void;
}) {
  const { operator } = useSession();
  const profiles = useProjectResource<RobotProfile[]>(`robot-profiles?project_id=${projectId}`);
  const [chosen, setChosen] = useState(preferredProfileId ?? "");
  const available = profiles.data?.filter(supported) ?? [];
  const profileId = chosen || available.find(p => p.digest === initial?.environment.robot_profile_sha256)?.id || available[0]?.id;
  const profile = available.find(p => p.id === profileId);
  if (!operator) return <Notice>An operator can create configuration releases.</Notice>;
  if (profiles.error) return <Notice tone="error">{profiles.error}</Notice>;
  if (!profiles.data) return <LoadingState label="Loading profiles…" />;
  if (!available.length) return <EmptyState title="No compatible profiles yet." text="A runnable configuration needs a profile with bounded joint positions and a MuJoCo position controller."
    action={<Link className="cv-btn cv-btn--secondary" href={projectHref(projectId, "Profiles")}>Open profiles</Link>} />;
  const select = <label className="cv-field">Robot profile<select className="cv-input" value={profileId ?? ""} onChange={e => setChosen(e.target.value)}>
    <option value="">Choose profile</option>{available.map(p => <option key={p.id} value={p.id}>{p.name} · revision {p.revision}</option>)}
  </select></label>;
  return <>
    {chosen && !profile && <Notice tone="error">The selected profile is unavailable or does not support this execution interface.</Notice>}
    {profile ? <ReleaseForm key={profile.id} profile={profile} profileField={select} application={application} initial={initial?.environment.robot_profile_sha256 === profile.digest ? initial : undefined} onSaved={onSaved} />
      : <div className="cv-form">{select}</div>}
  </>;
}

function ReleaseForm({ profile, profileField, application, initial, onSaved }: {
  profile: RobotProfile; profileField: ReactNode; application?: Application; initial?: RegisteredManifest; onSaved?: (release: Release) => void;
}) {
  const router = useRouter();
  const mutation = useMutation(() => {});
  const joints = profile.spec.joints.filter(j => j.kind !== "fixed");
  const [name, setName] = useState(application?.name ?? "");
  const [instruction, setInstruction] = useState(initial?.task.instruction ?? "");
  const [targets, setTargets] = useState<Record<string, string>>(() => Object.fromEntries(joints.map(j => [j.name, initial ? String(initial.task.target_joint_positions[initial.interface.joint_names.indexOf(j.name)]) : ""])));
  const [policy, setPolicy] = useState(initial && initial.policy.runtime !== REFERENCE_RUNTIME ? "installed" : "reference");
  const [runtime, setRuntime] = useState(initial?.policy.runtime === REFERENCE_RUNTIME ? "" : initial?.policy.runtime ?? "");
  const [digest, setDigest] = useState(initial?.policy.artifact_sha256 ?? "");
  const [realtime, setRealtime] = useState(!!initial?.execution.timing);
  const [maxAge, setMaxAge] = useState(initial?.execution.timing?.max_observation_age_ms ?? 200);
  const [maxLag, setMaxLag] = useState(initial?.execution.timing?.max_physics_lag_ms ?? 20);
  const [steps, setSteps] = useState(initial?.execution.max_steps ?? 200);
  const [deadline, setDeadline] = useState(initial?.execution.decision_timeout_ms ?? 1000);
  const [timeout, setTimeoutSeconds] = useState(initial?.execution.mission_timeout_s ?? 60);
  const [positionTolerance, setPositionTolerance] = useState(initial?.task.position_tolerance ?? 0.01);
  const [velocityTolerance, setVelocityTolerance] = useState(initial?.task.velocity_tolerance ?? 0.02);
  async function submit(event: FormEvent) {
    event.preventDefault();
    const configuration = { profile_id: profile.id, instruction, targets: Object.fromEntries(joints.map(j => [j.name, Number(targets[j.name])])),
      policy: policy === "reference" ? { kind: "reference" } : { kind: "installed", runtime, artifact_sha256: digest },
      position_tolerance: positionTolerance, velocity_tolerance: velocityTolerance,
      execution: { max_steps: steps, decision_timeout_ms: deadline, mission_timeout_s: timeout,
        ...(realtime ? { timing: { mode: "realtime", max_observation_age_ms: maxAge, max_physics_lag_ms: maxLag, fallback: "hold-position" } } : {}) } };
    const result = await mutation.submit<{ application: Application; release: Release }>(application ? `applications/${application.id}/configuration-releases` : "configurations",
      application ? configuration : { name, project_id: profile.project_id, configuration });
    if (result) { if (onSaved) onSaved(result.release); else router.push(configurationHref(result.application.id)); }
  }
  return <form className="cv-form" onSubmit={submit}>
    {!application && <label className="cv-field">Configuration name<input className="cv-input" required maxLength={120} value={name} onChange={e => setName(e.target.value)} /></label>}
    {profileField}
    <p className="cv-muted cv-form__hint">{profile.spec.control_rate_hz} control steps/second. Joint order and limits come from this profile.</p>
    <label className="cv-field">Task instruction<input className="cv-input" required maxLength={1000} value={instruction} onChange={e => setInstruction(e.target.value)} /></label>
    <fieldset className="cv-field"><legend>Target joint positions</legend><div className="cv-form__grid">{joints.map(j => <label className="cv-field" key={j.name}>{j.name} ({j.kind === "prismatic" ? "metres" : "radians"}, {j.lower} to {j.upper})<input className="cv-input" type="number" required step="any" min={j.lower!} max={j.upper!} value={targets[j.name]} onChange={e => setTargets(t => ({ ...t, [j.name]: e.target.value }))} /></label>)}</div></fieldset>
    <label className="cv-field">Policy<select className="cv-input" value={policy} onChange={e => setPolicy(e.target.value)}><option value="reference">Joint-position reference controller</option><option value="installed">Installed joint-position policy</option></select></label>
    {policy === "installed" && <>
      <p className="cv-muted cv-form__hint">Install a compatible policy worker first: its runtime and artifact are checked before activation, and entering them installs nothing.</p>
      <div className="cv-form__grid">
        <label className="cv-field">Installed runtime name<input className="cv-input" required maxLength={120} value={runtime} onChange={e => setRuntime(e.target.value)} /></label>
        <label className="cv-field">Policy artifact SHA-256<input className="cv-input" required pattern="[a-f0-9]{64}" value={digest} onChange={e => setDigest(e.target.value)} /></label>
      </div>
    </>}
    <label className="cv-field">Simulation timing<select className="cv-input" value={realtime ? "realtime" : "functional"} onChange={e => setRealtime(e.target.value === "realtime")}>
      <option value="functional">Functional · physics waits for policy responses</option><option value="realtime">Measured real time · physics advances independently</option>
    </select></label>
    {realtime && <>
      <p className="cv-muted cv-form__hint">Stale actions are rejected; an expired command holds position in simulation. This is not an emergency stop.</p>
      <div className="cv-form__grid">
        <label className="cv-field">Maximum observation age (milliseconds)<input className="cv-input" type="number" required min={1} max={30000} value={maxAge} onChange={e => setMaxAge(Number(e.target.value))} /></label>
        <label className="cv-field">Maximum physics lag (milliseconds)<input className="cv-input" type="number" required min={1} max={1000} value={maxLag} onChange={e => setMaxLag(Number(e.target.value))} /></label>
      </div>
      {profile.spec.control_rate_hz < 10 && <p className="cv-form-error" role="alert">Measured real-time execution requires a profile with at least 10 control steps per second.</p>}
    </>}
    <details className="cv-disclosure"><summary>Execution limits and success criteria</summary>
      <div className="cv-form__grid cv-disclosure__body">
        <label className="cv-field">Maximum control steps<input className="cv-input" type="number" required min={1} max={500} value={steps} onChange={e => setSteps(Number(e.target.value))} /></label>
        <label className="cv-field">Policy response timeout (milliseconds)<input className="cv-input" type="number" required min={1} max={30000} value={deadline} onChange={e => setDeadline(Number(e.target.value))} /></label>
        <label className="cv-field">Task timeout (seconds)<input className="cv-input" type="number" required min={1} max={3600} value={timeout} onChange={e => setTimeoutSeconds(Number(e.target.value))} /></label>
        <label className="cv-field">Position tolerance (joint SI units)<input className="cv-input" type="number" required min={0.000001} max={1} step="any" value={positionTolerance} onChange={e => setPositionTolerance(Number(e.target.value))} /></label>
        <label className="cv-field">Settled velocity tolerance (joint SI units/second)<input className="cv-input" type="number" required min={0.000001} max={1} step="any" value={velocityTolerance} onChange={e => setVelocityTolerance(Number(e.target.value))} /></label>
      </div>
    </details>
    {mutation.error && <p className="cv-form-error" role="alert">{mutation.error}</p>}
    <div className="cv-form__foot">
      <span className="cv-muted">Saving does not deploy.</span>
      <button className="cv-btn cv-btn--primary" disabled={mutation.busy || !instruction.trim() || !name.trim() || (realtime && profile.spec.control_rate_hz < 10)}>{mutation.busy ? "Saving…" : application ? "Save new release" : "Create configuration"}</button>
    </div>
  </form>;
}
