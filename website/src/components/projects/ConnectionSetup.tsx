"use client";

import { useEffect, useEffectEvent, useRef, useState } from "react";
import { Facts } from "@/components/configurations/Tiles";
import { api, ApiError, errorText, type Device } from "@/lib/platform/client";
import { useProjectResource } from "@/lib/projects/client";
import { notifySessionExpired } from "@/lib/configurations/session-events";

interface Computer extends Device { hardware: Record<string, string | number | boolean | null>; last_seen_at?: string | null }
interface SetupStatus { enrollment: { id: string; status: string; expires_at: string }; device: Computer | null }
interface Setup extends SetupStatus { command: string; run_command: string; simulator_command?: string | null; data_dir: string }

/** A one-use connection token for the computer, its command, and the enrolled computer once it connects. */
export function ConnectionSetup({ projectId, name, simulated, onConnected }: {
  projectId: string; name: string; simulated: boolean; onConnected: (device: Device) => void;
}) {
  const [setup, setSetup] = useState<Setup>();
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const locked = useRef(false);
  const delivered = useRef<string | undefined>(undefined);
  const pollingId = setup?.enrollment.status === "open" ? setup.enrollment.id : null;
  const connected = useEffectEvent((device: Computer) => onConnected(device));
  useEffect(() => {
    if (!pollingId) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function read() {
      try {
        const result = await api<SetupStatus>(`robot-connections/enrollments/${pollingId}`);
        if (!active) return;
        setError(undefined);
        setSetup(previous => previous?.enrollment.id === pollingId ? { ...previous, ...result,
          command: result.enrollment.status === "open" ? previous.command : "" } : previous);
        const device = result.device;
        if (device && delivered.current !== device.id && device.simulated === simulated) {
          delivered.current = device.id;
          connected(device);
        }
        if (result.enrollment.status !== "open") return;
      } catch (cause) {
        if (!active) return;
        if (cause instanceof ApiError && cause.status === 401) { notifySessionExpired(); return; }
        setError(errorText(cause));
      }
      if (active) timer = setTimeout(tick, 5000);
    }
    function tick() {
      if (!active) return;
      if (document.visibilityState === "visible") void read();
      else timer = setTimeout(tick, 5000);
    }
    void read();
    return () => { active = false; clearTimeout(timer); };
  }, [pollingId, revision, simulated]);
  const current = setup;
  async function act(cancel = false) {
    if (locked.current) return;
    locked.current = true; setBusy(true); setError(undefined);
    try {
      if (cancel && setup) {
        const result = await api<SetupStatus>(`robot-connections/enrollments/${setup.enrollment.id}/cancel`, {});
        setSetup({ ...setup, ...result, command: "" });
      } else {
        const result = await api<Setup>("robot-connections/enrollments", { project_id: projectId, name, simulated });
        setSetup(result); delivered.current = undefined;
      }
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 401) notifySessionExpired();
      setError(errorText(cause));
    } finally { locked.current = false; setBusy(false); }
  }
  return <section className="cv-subsection" aria-label="Connect a computer">
    <h3>Connect a {simulated ? "simulator computer" : "robot computer"}</h3>
    <p className="cv-muted">Run the Convoy agent on that computer to connect it over Wi-Fi or Ethernet.</p>
    {error && <p className="cv-form-error" role="alert">{error}</p>}
    {(!current || ["expired", "revoked"].includes(current.enrollment.status)) && <div><button type="button" className="cv-btn cv-btn--secondary cv-btn--small" disabled={busy || !name.trim()} onClick={() => void act()}>Create connection token</button></div>}
    {current?.enrollment.status === "open" && setup && <>
      <p>Waiting for enrollment. This one-use token expires {new Date(current.enrollment.expires_at).toLocaleTimeString()}. With the Convoy {simulated ? "simulator runtime" : "agent"} installed, run this on the computer:</p>
      <pre className="cv-code" aria-label="Connection command">{setup.command}</pre>
      <div className="cv-actions"><button type="button" className="cv-link" onClick={() => void navigator.clipboard.writeText(setup.command).catch(() => setError("Copy failed. Select and copy the command above."))}>Copy connection command</button>
        <button type="button" className="cv-link" disabled={busy} onClick={() => void act(true)}>Cancel connection token</button>
        <button type="button" className="cv-link" onClick={() => setRevision(n => n + 1)}>Check connection</button></div>
    </>}
    {current?.enrollment.status === "expired" && <p>The token expired. Create a new one to connect.</p>}
    {current?.enrollment.status === "revoked" && <p>The token was cancelled.</p>}
    {current?.enrollment.status === "consumed" && <>
      <p>{current.device ? `${current.device.name} enrolled and selected.` : "The token was used, but the connection is no longer available."}</p>
      {setup && current.device && <><p>{setup.simulator_command ? "Start the simulator service. It reports computer health, verifies the robot model when requested, and runs deployed tasks:" : "Keep the agent running to report connection health and computer sensors:"}</p><pre className="cv-code" aria-label="Agent run command">{setup.simulator_command ?? setup.run_command}</pre></>}
    </>}
  </section>;
}

/** The enrolled computer's reported hardware (never the robot's mechanics, which come from its profile). */
export function ComputerDetails({ deviceId }: { deviceId: string }) {
  const resource = useProjectResource<Computer>(deviceId ? `robot-connections/${deviceId}` : null, 0, true);
  if (!deviceId) return null;
  const device = resource.data;
  const hardware = device?.hardware ?? {};
  function value(key: string, suffix = "") {
    const v = hardware[key];
    return typeof v === "string" && v ? v : typeof v === "number" && Number.isFinite(v) ? `${Math.round(v)}${suffix}` : null;
  }
  return <section className="cv-subsection" aria-label="Computer details">
    <h3>{device ? `${device.name} · ${device.status === "never_seen" ? "Enrolled; waiting for the agent heartbeat" : device.status}` : "Computer"}</h3>
    {resource.error && <p className="cv-form-error" role="alert">{resource.error}</p>}
    {!device && !resource.error && <p className="cv-muted">Reading connection…</p>}
    {device && <>
      {hardware.synthetic === true && <p className="cv-muted">Synthetic demo hardware, not measured on the host.</p>}
      <Facts items={[
        { label: "Architecture", value: value("arch") }, { label: "Operating system", value: value("os") },
        { label: "CPU cores", value: value("cpu_count") }, { label: "Memory", value: value("mem_total_mb", " MiB") },
        { label: "GPU", value: value("gpu_name") }, { label: "Jetson model", value: value("jetson_model") },
      ]} />
    </>}
  </section>;
}
