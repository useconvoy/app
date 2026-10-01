/**
 * One-word states for badges: a robot's status and a configuration's status.
 * A robot bound to a live device shows its displayed health (`robotReadings`,
 * the same rule everywhere); any other robot shows whether an eval is running on
 * it, unless the document declares a health for it.
 */
import type { LiveBinding } from "./live";
import type { RobotReadings } from "./selectors";
import type { RunView } from "./runs";
import type { ConfigStatus, Robot } from "./types";

export type RobotStatus = "online" | "attention" | "degraded" | "offline" | "connecting" | "no-data" | "running" | "idle";
export const ROBOT_STATUS_LABEL: Record<RobotStatus, string> = {
  online: "Online", attention: "Needs attention", degraded: "Degraded", offline: "Offline", connecting: "Connecting", "no-data": "No data", running: "Running", idle: "Idle",
};
export type Tone = "neutral" | "success" | "warning" | "error" | "info";
export const ROBOT_STATUS_TONE: Record<RobotStatus, Tone> = {
  online: "success", attention: "warning", degraded: "warning", offline: "neutral", connecting: "neutral", "no-data": "neutral", running: "info", idle: "neutral",
};

export function robotStatus(robot: Pick<Robot, "deviceId">, readings: Pick<RobotReadings, "health">, live: Pick<LiveBinding, "status"> | null, runs: readonly Pick<RunView, "result">[]): RobotStatus {
  const health = readings.health;
  if (health === "attention" || health === "degraded" || health === "offline") return health;
  if (robot.deviceId) {
    if (health === "healthy") return "online";
    return !live || live.status === "loading" ? "connecting" : "no-data";
  }
  if (runs.some(run => run.result === "running" || run.result === "queued")) return "running";
  return health === "healthy" ? "online" : "idle";
}

/** "Live device" for a robot bound to a device, else "Simulator" ("Simulator · offline" when only offline imports feed it) or "Robot". */
export function robotType(robot: Pick<Robot, "deviceId" | "kind" | "projectId" | "offlineEvaluationIds">): string {
  if (robot.deviceId) return "Live device";
  if (robot.kind !== "simulator") return "Robot";
  return robot.offlineEvaluationIds?.length && !robot.projectId ? "Simulator · offline" : "Simulator";
}

export const CONFIG_STATUS_LABEL: Record<ConfigStatus, string> = { draft: "Draft", testing: "Testing", production: "Production" };
export const CONFIG_STATUS_TONE: Record<ConfigStatus, Tone> = { draft: "neutral", testing: "info", production: "success" };
