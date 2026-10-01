"use client";

import { createContext, useContext, useEffect, useMemo, useState, useSyncExternalStore } from "react";
import type { ReactNode } from "react";
import { bindingFor, LiveDevicePoller, UNBOUND } from "@/lib/configurations/live";
import type { LiveBinding, LiveState } from "@/lib/configurations/live";
import type { Robot } from "@/lib/configurations/types";

const LiveContext = createContext<LiveDevicePoller | null>(null);
const EMPTY: LiveState = { devices: Object.create(null) as LiveState["devices"], configuredDeviceId: undefined, polling: false, failures: 0, nextDelayMs: 0, lastPollAt: null };
const noSubscribe = () => () => undefined;
const emptySnapshot = () => EMPTY;

/**
 * Mounted once inside the Configurations session gate (`ConfigurationsRoot`), so it
 * ends with the session: signing out discards the poller and its device data.
 * Holds the shared live-device poller; it only polls while a page shows a robot
 * with a device binding (`useLiveRobots`).
 */
export function LiveDeviceProvider({ children }: { children: ReactNode }) {
  const [poller] = useState(() => new LiveDevicePoller());
  useEffect(() => { poller.start(); return () => poller.stop(); }, [poller]);
  return <LiveContext.Provider value={poller}>{children}</LiveContext.Provider>;
}

function useLiveState(keys: readonly string[]): LiveState {
  const poller = useContext(LiveContext);
  const key = [...new Set(keys)].toSorted().join("\n");
  useEffect(() => (poller && key ? poller.retain(key.split("\n")) : undefined), [poller, key]);
  return useSyncExternalStore(poller ? poller.subscribe : noSubscribe, poller ? poller.getSnapshot : emptySnapshot, emptySnapshot);
}

/**
 * Live bindings for robots, keyed by robot id (robots without `deviceId` are
 * "unbound"). Polls their devices while the calling component is mounted.
 * Pass the result to selectors (`robotReadings`, `attentionRobots`, …).
 */
export function useLiveRobots(robots: ReadonlyArray<Pick<Robot, "id" | "deviceId">>): Record<string, LiveBinding> {
  const state = useLiveState(robots.map(robot => robot.deviceId ?? "").filter(Boolean));
  return useMemo(() => Object.fromEntries(robots.map(robot => [robot.id, bindingFor(robot, state)])), [robots, state]);
}

/** Live binding for one robot (`UNBOUND` without a device id or robot). */
export function useLiveRobot(robot: Pick<Robot, "deviceId"> | null | undefined): LiveBinding {
  const state = useLiveState(robot?.deviceId ? [robot.deviceId] : []);
  return robot?.deviceId ? bindingFor(robot, state) : UNBOUND;
}

/** Polls now, e.g. for a "Check again" button; resolves when the poll finishes. */
export function useLiveRefresh(): () => Promise<void> {
  const poller = useContext(LiveContext);
  return poller ? poller.refresh : async () => undefined;
}
