/** Display types for the manifests validated and stored by the management API. */
export const PAIRED_PROFILE = "metaworld-smolvla-text-skill-v1";
export const CONTROLLED_PLANNER_RUNTIME = "convoy-controlled-text-skill-v1";

interface Artifact { runtime: string; artifact_sha256: string }

export interface ActionManifest {
  schema_version: 1;
  profile: string;
  policy: Artifact;
  environment: Record<string, unknown>;
  execution: Record<string, unknown>;
}

export interface PairedManifest {
  schema_version: 2;
  profile: typeof PAIRED_PROFILE;
  action_manifest: ActionManifest;
  planner: Artifact & { runtime: "convoy-llamacpp-text-skill-v1" | typeof CONTROLLED_PLANNER_RUNTIME; protocol_sha256: string };
  task: { instruction: string; skill_id: string };
  catalog_sha256: string;
  planning: { timeout_ms: number };
  placement: { policy: "development-local-cpu"; planner: "development-local" | "development-jetson-lan" | "development-remote-cpu" | "development-local-controlled" };
}

export interface RegisteredManifest {
  schema_version: 3;
  profile: "registered-joint-policy-v1";
  policy: Artifact;
  environment: { engine: "mujoco"; version: string; robot_profile_sha256: string; asset_sha256: string };
  interface: { joint_names: string[]; command_interface: "joint-position"; action_bounds: number[][]; control_rate_hz: number };
  task: { instruction: string; target_joint_positions: number[]; position_tolerance: number; velocity_tolerance: number };
  execution: { max_steps: number; decision_timeout_ms: number; mission_timeout_s: number };
}

export type ReleaseManifest = ActionManifest | PairedManifest | RegisteredManifest;

export function releaseComponents(manifest: ReleaseManifest) {
  // Robot and evaluation compatibility uses the composition's top-level profile,
  // never the nested action policy's visual observation profile.
  return manifest.schema_version === 2
    ? { profile: manifest.profile, action: manifest.action_manifest, pairing: manifest }
    : { profile: manifest.profile, action: manifest, pairing: null };
}
