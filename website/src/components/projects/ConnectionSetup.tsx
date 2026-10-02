"use client";

import { useEffect, useEffectEvent, useRef, useState } from "react";
import { api, ApiError, errorText, type Device } from "@/lib/platform/client";
import { useProjectResource } from "@/lib/projects/client";
import { notifySessionExpired } from "@/lib/configurations/session-events";

interface Computer extends Device { hardware: Record<string, string | number | boolean | null>; last_seen_at?: string | null }
interface SetupStatus { enrollment: { id: string; status: string; expires_at: string }; device: Computer | null }
interface Setup extends SetupStatus { command: string; run_command: string; data_dir: string }

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
  return <section className="project-connection" aria-label="Connect a computer">
    <h3>Connect a {simulated ? "simulator computer" : "robot computer"}</h3>
    <p>Run the Convoy agent on that computer to connect it over Wi-Fi or Ethernet.</p>
    {error && <p role="alert">{error}</p>}
    {(!current || ["expired", "revoked"].includes(current.enrollment.status)) && <button type="button" className="cv-btn cv-btn--secondary" disabled={busy || !name.trim()} onClick={() => void act()}>Create connection token</button>}
    {current?.enrollment.status === "open" && setup && <>
      <p>Waiting for enrollment. This one-use token expires {new Date(current.enrollment.expires_at).toLocaleTimeString()}.</p>
      <p>With the Convoy agent installed, run this in a terminal on the computer you want to connect:</p>
      <pre className="project-spec project-command" aria-label="Connection command">{setup.command}</pre>
      <div className="project-actions"><button type="button" className="cv-link" onClick={() => void navigator.clipboard.writeText(setup.command).catch(() => setError("Copy failed. Select and copy the command above."))}>Copy connection command</button>
      <button type="button" className="cv-link" disabled={busy} onClick={() => void act(true)}>Cancel connection token</button></div>
    </>}
    {current?.enrollment.status === "expired" && <p>The token expired. Create a new one to connect.</p>}
    {current?.enrollment.status === "revoked" && <p>The token was cancelled.</p>}
    {current?.enrollment.status === "consumed" && <>
      <p>{current.device ? `${current.device.name} enrolled and selected.` : "The token was used, but the connection is no longer available."}</p>
      {setup && current.device && <><p>Keep the agent running to report connection health and computer sensors:</p><pre className="project-spec project-command" aria-label="Agent run command">{setup.run_command}</pre></>}
    </>}
    {pollingId && <button type="button" className="cv-link" onClick={() => setRevision(n => n + 1)}>Check connection</button>}
  </section>;
}

export function ComputerDetails({ deviceId }: { deviceId: string }) {
  const resource = useProjectResource<Computer>(deviceId ? `robot-connections/${deviceId}` : null, 0, true);
  if (!deviceId) return null;
  const device = resource.data;
  const hardware = device?.hardware ?? {};
  function value(key: string, suffix = "") {
    const v = hardware[key];
    return typeof v === "string" && v ? v : typeof v === "number" && Number.isFinite(v) ? `${Math.round(v)}${suffix}` : "Not reported";
  }
  return <section className="project-connection" aria-label="Computer details">
    <h3>Reported computer hardware</h3>
    {resource.error && <p role="alert">{resource.error}</p>}
    {!device && !resource.error && <p>Reading connection…</p>}
    {device && <>
      <p>{device.name} · {device.status === "never_seen" ? "Enrolled; waiting for the agent heartbeat" : device.status}</p>
      {hardware.synthetic === true && <p>Synthetic demo hardware. These values are not measurements of the host.</p>}
      <dl className="cv-facts"><div><dt>Architecture</dt><dd>{value("arch")}</dd></div><div><dt>Operating system</dt><dd>{value("os")}</dd></div>
        <div><dt>CPU cores</dt><dd>{value("cpu_count")}</dd></div><div><dt>Memory</dt><dd>{value("mem_total_mb", " MiB")}</dd></div>
        <div><dt>GPU</dt><dd>{value("gpu_name")}</dd></div><div><dt>Jetson model</dt><dd>{value("jetson_model")}</dd></div></dl>
      <p>These are agent-reported computer details. The robot’s mechanics come from its separate profile.</p>
    </>}
  </section>;
}
