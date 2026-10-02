import { useEffect, useState } from "react";
import { api, ApiError, errorText } from "@/lib/platform/client";
import type { Application, Project } from "@/lib/platform/client";
import { notifySessionExpired } from "@/lib/configurations/session-events";

export interface ProfileJoint { name: string; kind: string; lower: number | null; upper: number | null }
export interface ProfileSimulation { engine: string; engine_version: string; controller: string; asset: { sha256: string; uri: string; format: string } }
export interface RobotProfile {
  id: string; project_id: string; name: string; revision: number; digest: string;
  spec: { embodiment: string; command_interface: string; adapter: string; control_rate_hz: number; joints: ProfileJoint[]; sensors: unknown[]; simulations: ProfileSimulation[]; execution_profile?: string | null };
  simulation: { state: string; engines: string[]; runtime_verified: boolean; dynamics_source: string; detail: string };
}
export interface Fleet { id: string; project_id: string; name: string; robot_ids: string[] }

/** Resource changes clear old results; a response from a previous project cannot overwrite this one. */
export function useProjectResource<T>(path: string | null, revision = 0, poll = false) {
  const [result, setResult] = useState<{ path: string | null; data?: T; error?: string }>();
  useEffect(() => {
    if (path === null) return;
    const resourcePath = path;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    function tick() {
      if (!active) return;
      if (document.visibilityState === "visible") void read();
      else timer = setTimeout(tick, 5000);
    }
    async function read() {
      try {
        const data = await api<T>(resourcePath);
        if (active) setResult({ path, data });
      } catch (error) {
        if (!active) return;
        if (error instanceof ApiError && error.status === 401) { notifySessionExpired(); return; }
        setResult(previous => ({ path, data: previous?.path === path ? previous.data : undefined, error: errorText(error) }));
      }
      if (active && poll) timer = setTimeout(tick, 5000);
    }
    void read();
    return () => { active = false; clearTimeout(timer); };
  }, [path, revision, poll]);
  return result?.path === path ? result : { path };
}

/** A runnable project configuration (an application with immutable releases) and its project. */
export interface RunnableConfiguration { application: Application; project: Project }
export interface RunnableConfigurations { items?: RunnableConfiguration[]; error?: string }

/**
 * Every runnable configuration in the account's projects: the projects, then each one's
 * applications (the API lists applications per project). `items` is undefined until read.
 */
export function useRunnableConfigurations(enabled = true, revision = 0): RunnableConfigurations {
  const key = enabled ? String(revision) : null;
  const [result, setResult] = useState<RunnableConfigurations & { key: string | null }>();
  useEffect(() => {
    if (key === null) return;
    let active = true;
    void (async () => {
      try {
        const projects = await api<Project[]>("projects");
        const lists = await Promise.all(projects.map(project => api<Application[]>(`applications?project_id=${encodeURIComponent(project.id)}`)));
        if (active) setResult({ key, items: projects.flatMap((project, i) => lists[i].map(application => ({ application, project }))) });
      } catch (cause) {
        if (!active) return;
        if (cause instanceof ApiError && cause.status === 401) { notifySessionExpired(); return; }
        setResult({ key, error: errorText(cause) });
      }
    })();
    return () => { active = false; };
  }, [key]);
  return result?.key === key && key !== null ? result : {};
}
