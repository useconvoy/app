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
export function useProjectResource<T>(path: string, revision = 0) {
  const [result, setResult] = useState<{ path: string; data?: T; error?: string }>();
  useEffect(() => {
    let active = true;
    void api<T>(path).then(data => { if (active) setResult({ path, data }); }).catch(error => {
      if (!active) return;
      if (error instanceof ApiError && error.status === 401) notifySessionExpired();
      setResult({ path, error: errorText(error) });
    });
    return () => { active = false; };
  }, [path, revision]);
  return result?.path === path ? result : { path };
}
