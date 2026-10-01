"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, useSyncExternalStore } from "react";
import type { ReactNode } from "react";
import { platformPaths, standaloneMissions } from "@/lib/configurations/runs";
import type { PlatformEvaluation, PlatformMission, PlatformProject } from "@/lib/configurations/runs";
import { notifySessionExpired } from "@/lib/configurations/session-events";
import { api, ApiError, errorText } from "@/lib/platform/client";
import type { Episode } from "@/lib/platform/client";

export type Remote<T> =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; data: T }
  | { status: "error"; message: string; missing: boolean };

/** Reads are reused for this long; a page that mounts later reads again. */
const FRESH_MS = 20_000;
/** Episodes read per project for missions run outside an evaluation (newest first). */
const EPISODES_PER_PROJECT = 24;

type Entry = { remote: Remote<unknown>; at: number };

/**
 * GET results through the platform proxy for one signed-in session, shared by
 * every page: a path is read once at a time and reused while fresh. A 401 ends
 * the session.
 */
class PlatformCache {
  private entries = new Map<string, Entry>();
  private listeners = new Set<() => void>();
  private version = 0;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  getVersion = () => this.version;
  get(path: string): Remote<unknown> { return this.entries.get(path)?.remote ?? { status: "idle" }; }
  load(path: string, force = false) {
    const entry = this.entries.get(path);
    if (entry?.remote.status === "loading") return;
    if (!force && entry && entry.remote.status !== "idle" && Date.now() - entry.at < FRESH_MS) return;
    // Keep data already shown while it is read again.
    if (!entry || entry.remote.status !== "ready") this.set(path, { status: "loading" });
    else this.entries.set(path, { ...entry, at: Date.now() });
    void api<unknown>(path).then(data => this.set(path, { status: "ready", data })).catch((cause: unknown) => {
      if (cause instanceof ApiError && cause.status === 401) notifySessionExpired();
      this.set(path, { status: "error", message: errorText(cause), missing: cause instanceof ApiError && cause.status === 404 });
    });
  }
  private set(path: string, remote: Remote<unknown>) {
    this.entries.set(path, { remote, at: Date.now() });
    this.version++;
    for (const listener of [...this.listeners]) listener();
  }
}

const PlatformContext = createContext<PlatformCache | null>(null);

/** Mounted inside the session gate, so another account never sees this session's reads. */
export function PlatformProvider({ children }: { children: ReactNode }) {
  const [cache] = useState(() => new PlatformCache());
  return <PlatformContext.Provider value={cache}>{children}</PlatformContext.Provider>;
}

const noSubscribe = () => () => undefined;
const zero = () => 0;

/** Several proxy reads (null entries are skipped), keyed by path. `retry` reads them again. */
export function usePlatformReads(paths: ReadonlyArray<string | null>): { reads: ReadonlyMap<string, Remote<unknown>>; retry: () => void } {
  const cache = useContext(PlatformContext);
  const key = paths.filter((path): path is string => !!path).join("\n");
  const version = useSyncExternalStore(cache ? cache.subscribe : noSubscribe, cache ? cache.getVersion : zero, zero);
  useEffect(() => { if (cache && key) for (const path of key.split("\n")) cache.load(path); }, [cache, key]);
  const reads = useMemo(() => {
    void version;
    return new Map(key ? key.split("\n").map(path => [path, cache ? cache.get(path) : { status: "idle" } as Remote<unknown>]) : []);
  }, [cache, key, version]);
  const retry = useCallback(() => { if (cache && key) for (const path of key.split("\n")) cache.load(path, true); }, [cache, key]);
  return { reads, retry };
}

/** One proxy read. */
export function usePlatform<T>(path: string | null): { state: Remote<T>; retry: () => void } {
  const { reads, retry } = usePlatformReads([path]);
  const state = (path ? reads.get(path) : undefined) as Remote<T> | undefined;
  return { state: state ?? (path ? { status: "loading" } : { status: "idle" }), retry };
}

const loading = (remote: Remote<unknown> | undefined) => !remote || remote.status === "idle" || remote.status === "loading";

/**
 * Each project's evaluations, missions and the episodes of missions run outside an
 * evaluation, keyed by project id. A project is ready once its evaluations are
 * read; missions that cannot be read count as none.
 */
export function usePlatformProjects(projectIds: ReadonlyArray<string | null | undefined>): { projects: ReadonlyMap<string, Remote<PlatformProject>>; retry: () => void } {
  const idKey = [...new Set(projectIds.filter((id): id is string => !!id))].toSorted().join("\n");
  const ids = useMemo(() => idKey ? idKey.split("\n") : [], [idKey]);
  const { reads, retry } = usePlatformReads(ids.flatMap(id => [platformPaths.evaluations(id), platformPaths.missions(id)]));
  const episodeIds = useMemo(() => ids.flatMap(id => {
    const evaluations = reads.get(platformPaths.evaluations(id)), missions = reads.get(platformPaths.missions(id));
    if (evaluations?.status !== "ready" || missions?.status !== "ready") return [];
    const project: PlatformProject = { evaluations: evaluations.data as PlatformEvaluation[], missions: missions.data as PlatformMission[], episodes: {} };
    return standaloneMissions(project).filter(mission => mission.episode_id).toSorted((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at))
      .slice(0, EPISODES_PER_PROJECT).map(mission => mission.episode_id as string);
  }), [ids, reads]);
  const { reads: episodes } = usePlatformReads(episodeIds.map(platformPaths.episode));
  const projects = useMemo(() => new Map(ids.map((id): [string, Remote<PlatformProject>] => {
    const evaluations = reads.get(platformPaths.evaluations(id)), missions = reads.get(platformPaths.missions(id));
    if (loading(evaluations) || loading(missions)) return [id, { status: "loading" }];
    if (evaluations?.status === "error") return [id, evaluations];
    const byId: Record<string, Episode | undefined> = Object.create(null);
    for (const episodeId of episodeIds) {
      const read = episodes.get(platformPaths.episode(episodeId));
      if (read?.status === "ready") byId[episodeId] = read.data as Episode;
    }
    return [id, { status: "ready", data: {
      evaluations: evaluations?.status === "ready" && Array.isArray(evaluations.data) ? evaluations.data as PlatformEvaluation[] : [],
      missions: missions?.status === "ready" && Array.isArray(missions.data) ? missions.data as PlatformMission[] : [],
      episodes: byId,
    } }];
  })), [ids, reads, episodes, episodeIds]);
  return { projects, retry };
}
