"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { PageHeader } from "@/components/configurations/AppShell";
import { useSession } from "@/components/configurations/Session";
import type { Application, Release } from "@/lib/platform/client";
import type { RegisteredManifest } from "@/lib/platform/manifest";
import { useProjectResource, type RobotProfile } from "@/lib/projects/client";
import { Shell, useMutation } from "./Projects";
import { configurationHref } from "./ProjectConfigurations";

export const REFERENCE_RUNTIME = "convoy-joint-target-reference-v1";
function supported(profile: RobotProfile) {
  const joints = profile.spec.joints.filter(j => j.kind !== "fixed");
  return profile.spec.command_interface === "joint-position" && [undefined, null, "registered-joint-policy-v1"].includes(profile.spec.execution_profile)
    && profile.spec.simulations?.some(m => m.engine === "mujoco" && m.controller === "position")
    && joints.length > 0 && joints.every(j => Number.isFinite(j.lower) && Number.isFinite(j.upper));
}

export function NewProjectConfiguration({ projectId, profileId }: { projectId: string; profileId?: string }) {
  return <Shell project="New configuration"><Link className="cv-link" href={`/app/projects/${projectId}`}>← Back to project</Link>
    <PageHeader title="Create runnable configuration" />
    <ConfigurationEditor projectId={projectId} preferredProfileId={profileId} />
  </Shell>;
}

export function ConfigurationEditor({ projectId, preferredProfileId, application, initial, onSaved }: {
  projectId: string; preferredProfileId?: string; application?: Application; initial?: RegisteredManifest; onSaved?: (release: Release) => void;
}) {
  const { operator } = useSession();
  const profiles = useProjectResource<RobotProfile[]>(`robot-profiles?project_id=${projectId}`);
  const [chosen, setChosen] = useState(preferredProfileId ?? "");
  const available = profiles.data?.filter(supported) ?? [];
  const profileId = chosen || available.find(p => p.digest === initial?.environment.robot_profile_sha256)?.id || available[0]?.id;
  const profile = available.find(p => p.id === profileId);
  if (!operator) return <p>An operator can create configuration releases.</p>;
  return <>
    <p>Choose a physical profile and define a joint-position task. This creates a functional MuJoCo release; it does not certify timing or physical accuracy.</p>
    {profiles.error && <p role="alert">{profiles.error}</p>}
    {!profiles.data && !profiles.error && <p role="status">Loading profiles…</p>}
    {profiles.data && available.length === 0 && <p>No compatible profiles yet. <Link className="cv-link" href={`/app/projects/${projectId}`}>Import a bounded joint-position profile with a MuJoCo position controller.</Link></p>}
    {available.length > 0 && <label className="cv-field">Robot profile<select className="cv-input" value={profileId ?? ""} onChange={e => setChosen(e.target.value)}>
      <option value="">Choose profile</option>{available.map(p => <option key={p.id} value={p.id}>{p.name} · revision {p.revision}</option>)}
    </select></label>}
    {profile && <ReleaseForm key={profile.id} profile={profile} application={application} initial={initial?.environment.robot_profile_sha256 === profile.digest ? initial : undefined} disabled={!!profiles.error} onSaved={onSaved} />}
    {chosen && !profile && profiles.data && <p role="alert">The selected profile is unavailable or does not support this execution interface.</p>}
  </>;
}

function ReleaseForm({ profile, application, initial, disabled, onSaved }: {
  profile: RobotProfile; application?: Application; initial?: RegisteredManifest; disabled: boolean; onSaved?: (release: Release) => void;
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
      execution: { max_steps: steps, decision_timeout_ms: deadline, mission_timeout_s: timeout } };
    const result = await mutation.submit<{ application: Application; release: Release }>(application ? `applications/${application.id}/configuration-releases` : "configurations",
      application ? configuration : { name, project_id: profile.project_id, configuration });
    if (result) { if (onSaved) onSaved(result.release); else router.push(configurationHref(result.application.id)); }
  }
  return <form className="cv-form project-create" onSubmit={submit}>
    {!application && <label className="cv-field">Configuration name<input className="cv-input" required maxLength={120} value={name} onChange={e => setName(e.target.value)} /></label>}
    <label className="cv-field">Task instruction<input className="cv-input" required maxLength={1000} value={instruction} onChange={e => setInstruction(e.target.value)} /></label>
    <p>{profile.name} · revision {profile.revision} · {profile.spec.control_rate_hz} control steps/second. Joint order and limits come from this profile.</p>
    <fieldset><legend>Target joint positions</legend>{joints.map(j => <label className="cv-field" key={j.name}>{j.name} ({j.kind === "prismatic" ? "metres" : "radians"}, {j.lower} to {j.upper})<input className="cv-input" type="number" required step="any" min={j.lower!} max={j.upper!} value={targets[j.name]} onChange={e => setTargets(t => ({ ...t, [j.name]: e.target.value }))} /></label>)}</fieldset>
    <label className="cv-field">Policy<select className="cv-input" value={policy} onChange={e => setPolicy(e.target.value)}><option value="reference">Joint-position reference controller</option><option value="installed">Installed joint-position policy</option></select></label>
    {policy === "reference" ? <p>A controlled reference moves towards the specified targets. No learned model is used.</p> : <>
      <p>The operator must install a compatible joint-state policy worker. Its runtime and artifact identity are checked before activation; entering them does not install a model.</p>
      <label className="cv-field">Installed runtime name<input className="cv-input" required maxLength={120} value={runtime} onChange={e => setRuntime(e.target.value)} /></label>
      <label className="cv-field">Policy artifact SHA-256<input className="cv-input" required pattern="[a-f0-9]{64}" value={digest} onChange={e => setDigest(e.target.value)} /></label>
    </>}
    <details><summary>Execution limits and success criteria</summary>
      <label className="cv-field">Maximum control steps<input className="cv-input" type="number" required min={1} max={500} value={steps} onChange={e => setSteps(Number(e.target.value))} /></label>
      <label className="cv-field">Policy response timeout (milliseconds)<input className="cv-input" type="number" required min={1} max={30000} value={deadline} onChange={e => setDeadline(Number(e.target.value))} /></label>
      <label className="cv-field">Task timeout (seconds)<input className="cv-input" type="number" required min={1} max={3600} value={timeout} onChange={e => setTimeoutSeconds(Number(e.target.value))} /></label>
      <label className="cv-field">Position tolerance (joint SI units)<input className="cv-input" type="number" required min={0.000001} max={1} step="any" value={positionTolerance} onChange={e => setPositionTolerance(Number(e.target.value))} /></label>
      <label className="cv-field">Settled velocity tolerance (joint SI units/second)<input className="cv-input" type="number" required min={0.000001} max={1} step="any" value={velocityTolerance} onChange={e => setVelocityTolerance(Number(e.target.value))} /></label>
    </details>
    <p>Cloud planning is not connected to this joint-state runtime yet. The simulator and policy worker currently require operator setup.</p>
    {mutation.error && <p role="alert">{mutation.error}</p>}
    <button className="cv-btn cv-btn--primary" disabled={disabled || mutation.busy || !instruction.trim() || !name.trim()}>{mutation.busy ? "Saving…" : application ? "Save new release" : "Create configuration"}</button>
  </form>;
}
