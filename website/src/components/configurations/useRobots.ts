"use client";

import { useCallback, useMemo } from "react";
import type { LiveBinding } from "@/lib/configurations/live";
import { robotRuns } from "@/lib/configurations/runs";
import type { RunView } from "@/lib/configurations/runs";
import { getConfiguration, getRevision, robotReadings } from "@/lib/configurations/selectors";
import type { RobotReadings } from "@/lib/configurations/selectors";
import { robotStatus } from "@/lib/configurations/status";
import type { RobotStatus } from "@/lib/configurations/status";
import type { ConvoyWorkspace, Robot } from "@/lib/configurations/types";
import { useLiveRobots } from "./LiveDeviceProvider";
import { useOfflineEvaluations, usePlatformProjects } from "./platform";

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
  const { projects, retry: retryProjects } = usePlatformProjects(robots.map(robot => robot.projectId));
  const offline = useOfflineEvaluations(robots.some(robot => robot.offlineEvaluationIds?.length));
  const views = useMemo(() => robots.map(robot => {
    const config = robot.configId ? getConfiguration(ws, robot.configId) : null;
    const readings = robotReadings(robot, config ? getRevision(config, robot.rev) : null, live[robot.id], now);
    const remote = robot.projectId ? projects.get(robot.projectId) : undefined;
    const imports = robot.offlineEvaluationIds?.length ? offline.state : undefined;
    const runs = robotRuns(ws, robot, remote?.status === "ready" ? remote.data : null, offline.state.status === "ready" ? offline.state.data : null);
    const pending = (read: { status: string } | undefined) => !read || read.status === "loading" || read.status === "idle";
    return {
      robot, live: live[robot.id], readings, runs, status: robotStatus(robot, readings, live[robot.id] ?? null, runs),
      runsLoading: (!!robot.projectId && pending(remote)) || (!!imports && pending(imports)),
      runsError: remote?.status === "error" ? remote.message : imports?.status === "error" ? imports.message : null,
    };
  }), [ws, robots, live, projects, offline.state, now]);
  const retryOffline = offline.retry;
  const retry = useCallback(() => { retryProjects(); retryOffline(); }, [retryProjects, retryOffline]);
  return { views, retry };
}
