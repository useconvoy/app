"use client";

import { useMemo } from "react";
import type { LiveBinding } from "@/lib/configurations/live";
import { robotRuns } from "@/lib/configurations/runs";
import type { RunView } from "@/lib/configurations/runs";
import { getConfiguration, getRevision, robotReadings } from "@/lib/configurations/selectors";
import type { RobotReadings } from "@/lib/configurations/selectors";
import { robotStatus } from "@/lib/configurations/status";
import type { RobotStatus } from "@/lib/configurations/status";
import type { ConvoyWorkspace, Robot } from "@/lib/configurations/types";
import { useLiveRobots } from "./LiveDeviceProvider";
import { usePlatformProjects } from "./platform";

export interface RobotView {
  robot: Robot;
  live: LiveBinding;
  /** Displayed health and measured values (the same rule on every page). */
  readings: RobotReadings;
  /** Stored runs and, with `projectId`, the project's evaluations and episodes; newest first. */
  runs: RunView[];
  status: RobotStatus;
  /** Platform evals still loading, or why they could not be read. */
  runsLoading: boolean;
  runsError: string | null;
}

/** Everything the pages show about robots: live readings, evals (stored and platform) and status. Pass a memoized array. */
export function useRobotViews(ws: ConvoyWorkspace, robots: readonly Robot[], now: number | null): { views: RobotView[]; retry: () => void } {
  const live = useLiveRobots(robots);
  const { projects, retry } = usePlatformProjects(robots.map(robot => robot.projectId));
  const views = useMemo(() => robots.map(robot => {
    const config = robot.configId ? getConfiguration(ws, robot.configId) : null;
    const readings = robotReadings(robot, config ? getRevision(config, robot.rev) : null, live[robot.id], now);
    const remote = robot.projectId ? projects.get(robot.projectId) : undefined;
    const runs = robotRuns(ws, robot, remote?.status === "ready" ? remote.data : null);
    return {
      robot, live: live[robot.id], readings, runs, status: robotStatus(robot, readings, live[robot.id] ?? null, runs),
      runsLoading: !!robot.projectId && (!remote || remote.status === "loading" || remote.status === "idle"),
      runsError: remote?.status === "error" ? remote.message : null,
    };
  }), [ws, robots, live, projects, now]);
  return { views, retry };
}
