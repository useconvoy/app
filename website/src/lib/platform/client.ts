import type { ReleaseManifest } from "./manifest";

export interface Project { id: string; name: string }
export interface Robot { id: string; project_id: string; device_id: string; name: string; profile: string; generation: number; evaluation_id: string | null; simulated?: boolean; profile_id?: string | null; source_robot_id?: string | null; simulation_engine?: string | null; fleet_id?: string | null }
export interface Application { id: string; project_id: string; name: string }
export interface Release { id: string; application_id: string; digest: string; manifest: ReleaseManifest }
export interface Deployment { id: string; robot_id: string; release_id: string; generation: number; state: string; detail: string; observed_at: string | null }
export interface Mission { id: string; robot_id: string; deployment_id: string; release_id: string; state: string; detail: string; episode_id: string | null; seed: number; expires_at: number; updated_at: string }
export interface Episode { id: string; mission_id: string; state: string; detail: string; release_digest: string; summary: Record<string, unknown> }
export interface Device { id: string; name: string; simulated: boolean; status: string }
export interface Account { user: { email: string; role: string }; installation: { simulator: boolean; execution_profiles?: string[]; dispatch_paused_at: string | null; quarantined_at: string | null } }
export interface EvaluationSuite { id: string; application_id: string; digest: string; spec: { name: string; seeds: number[]; min_successes: number; scorer: string; contract: Record<string, unknown> } }
export interface EvaluationCase { seed: number; mission_id: string | null; episode_id: string | null }
export interface EvaluationReport { passed: boolean; successes: number; min_successes: number; case_count: number; median_wall_s: number | null; suite_digest: string; release_digest: string; cases: (EvaluationCase & { state: string; passed: boolean; evidence_valid: boolean; steps: number | null; wall_duration_s: number | null })[] }
export interface EvaluationRun { id: string; project_id: string; suite_id: string; release_id: string; robot_id: string; state: string; detail: string; updated_at: string; cases: EvaluationCase[]; report: EvaluationReport | null }
export interface Qualification { release_id: string; gate: { application_id: string; suite_id: string; generation: number } | null; promotion: { id: string; release_id: string; evaluation_id: string; suite_id: string; created_at: string } | null; deployment_allowed: boolean }
export interface EvaluationComparison { candidate: EvaluationRun; baseline: EvaluationRun; success_count_delta: number; scope: string }
/** An owner's offline evaluation: episodes recorded outside the hosted runner, imported unsigned (`source: "offline"`). */
export interface OfflineEvaluation {
  id: string; name: string; task: string; config_label: string; policy_label: string;
  summary: { episodes: number; successes: number; success_rate: number | null; median_steps: number | null; median_wall_seconds: number | null; median_sim_seconds: number | null; stored_bytes: number };
  source: "offline"; signed: false; scope: string; created_at: string; updated_at: string;
}
export type OfflineOutcome = "success" | "failure" | "timeout" | "safety-stop";
export interface OfflineEpisode {
  id: string; evaluation_id: string; seed: number; outcome: OfflineOutcome; steps: number; images: number; action_dim: number;
  metrics: Record<string, number | boolean | string | null>; wall_seconds: number | null; sim_seconds: number | null; stored_bytes: number; created_at: string;
}
export interface OfflineEvaluationDetail extends OfflineEvaluation { episodes: OfflineEpisode[] }

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export async function api<T>(path: string, body?: unknown, key?: string): Promise<T> {
  const response = await fetch(`/api/platform/${path}`, {
    method: body === undefined ? "GET" : "POST", credentials: "same-origin", cache: "no-store",
    headers: { "Content-Type": "application/json", "X-Convoy-Client": "web", ...(key ? { "Idempotency-Key": key } : {}) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok || data === null) throw new ApiError(response.status, typeof data?.error === "string" ? data.error : "The request could not be completed. Refresh to check its outcome.");
  return data as T;
}

export const errorText = (error: unknown) => error instanceof Error ? error.message : "The request could not be completed.";
export const terminal = (state: string) => ["completed", "failed", "cancelled"].includes(state);
export const timestamp = (value: string | null) => value ? new Date(value).toLocaleString() : "No acknowledgement yet";

/** Keep a failed attempt's key so an explicit retry cannot duplicate a committed write. */
export class MutationAttempts {
  private pending = new Map<string, string>();
  async submit<T>(path: string, body: unknown): Promise<T> {
    const identity = `${path}\0${JSON.stringify(body)}`;
    const key = this.pending.get(identity) ?? crypto.randomUUID();
    this.pending.set(identity, key);
    const result = await api<T>(path, body, key);
    this.pending.delete(identity);
    return result;
  }
}
