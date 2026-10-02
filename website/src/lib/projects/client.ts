import { useEffect, useState } from "react";
import { api, ApiError, errorText } from "@/lib/platform/client";
import { notifySessionExpired } from "@/lib/configurations/session-events";

export interface RobotProfile {
  id: string; project_id: string; name: string; revision: number; digest: string;
  spec: { embodiment: string; command_interface: string; adapter: string; control_rate_hz: number; joints: unknown[]; sensors: unknown[] };
  simulation: { state: string; engines: string[]; runtime_verified: boolean; dynamics_source: string; detail: string };
}
export interface Fleet { id: string; project_id: string; name: string; robot_ids: string[] }

/** Resource changes clear old results; a response from a previous project cannot overwrite this one. */
export function useProjectResource<T>(path: string, revision = 0, poll = false) {
  const [result, setResult] = useState<{ path: string; data?: T; error?: string }>();
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    function tick() {
      if (!active) return;
      if (document.visibilityState === "visible") void read();
      else timer = setTimeout(tick, 5000);
    }
    async function read() {
      try {
        const data = await api<T>(path);
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
