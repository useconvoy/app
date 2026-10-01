"use client";

import { useId, useState } from "react";
import type { FormEvent } from "react";
import { useWorkspace } from "@/lib/configurations/client";
import { addRobot } from "@/lib/configurations/mutations";
import { platformPaths } from "@/lib/configurations/runs";
import { CONFIGURED_DEVICE } from "@/lib/configurations/types";
import type { Configuration } from "@/lib/configurations/types";
import type { Device, Project, Robot as PlatformRobot } from "@/lib/platform/client";
import { Modal } from "../Overlay";
import { usePlatform } from "../platform";
import { OfflinePicker } from "./OfflineEvaluations";

type Kind = "device" | "simulator" | "offline";
const KINDS: ReadonlyArray<{ id: Kind; label: string }> = [{ id: "device", label: "Live device" }, { id: "simulator", label: "Simulator" }, { id: "offline", label: "Simulator · offline" }];

/**
 * Add robot: a name, and where its data comes from. A live device (one of the
 * account's devices) gives telemetry and traces; a control-plane project gives
 * evals, rollouts and replays from its real evaluations and episodes, optionally
 * only one platform robot's; offline evaluations (imported, unsigned) give evals
 * tagged "Offline sim". Robots are added for testing.
 */
export function AddRobotDialog({ config, onClose, onAdded }: { config: Configuration; onClose: () => void; onAdded: (name: string) => void }) {
  const ws = useWorkspace();
  const id = useId();
  const [name, setName] = useState("");
  const [kind, setKind] = useState<Kind>("device");
  const [deviceId, setDeviceId] = useState("");
  const [projectId, setProjectId] = useState("");
  const [platformRobotId, setPlatformRobotId] = useState("");
  const [offlineIds, setOfflineIds] = useState<string[]>([]);
  const [checked, setChecked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const devices = usePlatform<Device[]>(platformPaths.devices());
  const projects = usePlatform<Project[]>(platformPaths.projects());
  const robots = usePlatform<PlatformRobot[]>(projectId ? platformPaths.robots(projectId) : null);
  const deviceList = devices.state.status === "ready" && Array.isArray(devices.state.data)
    ? devices.state.data.filter(device => !device.simulated).toSorted((a, b) => Number(b.status === "online") - Number(a.status === "online") || a.name.localeCompare(b.name)) : [];
  const projectList = projects.state.status === "ready" && Array.isArray(projects.state.data) ? projects.state.data : [];
  const robotList = robots.state.status === "ready" && Array.isArray(robots.state.data) ? robots.state.data : [];
  // Without the account's device list, the workspace's configured device is the one choice.
  const deviceChoices = deviceList.length ? deviceList.map(device => ({ value: device.id, label: `${device.name} · ${device.status.replaceAll("_", " ")}` }))
    : devices.state.status === "loading" ? [] : [{ value: CONFIGURED_DEVICE, label: "Workspace device" }];
  const chosenDevice = kind === "device" ? (deviceChoices.some(choice => choice.value === deviceId) ? deviceId : deviceChoices[0]?.value ?? "") : "";
  const errors = {
    name: !name.trim() ? "Enter a name." : ws.workspace?.robots.some(robot => robot.configId === config.id && robot.name.trim().toLowerCase() === name.trim().toLowerCase()) ? "This configuration has a robot with this name." : null,
    device: kind === "device" && !chosenDevice ? "Choose a device." : null,
    offline: kind === "offline" && !offlineIds.length ? "Choose an offline eval." : null,
  };

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setChecked(true);
    if (errors.name || errors.device || errors.offline) return;
    setBusy(true);
    setError(null);
    const offline = kind === "offline";
    const input = {
      configId: config.id, name, deviceId: chosenDevice || null, projectId: !offline && projectId || null,
      platformRobotId: !offline && projectId && platformRobotId ? platformRobotId : null, offlineEvaluationIds: offline ? offlineIds : null,
    };
    const result = await ws.save(current => addRobot(current, input, Date.now()).workspace);
    setBusy(false);
    if (result.ok) onAdded(name.trim()); else setError(result.error);
  }

  const formId = `${id}-form`;
  return <Modal open onClose={onClose} title="Add robot"
    footer={<>
      <button className="cv-btn cv-btn--secondary" type="button" onClick={onClose}>Cancel</button>
      <button className="cv-btn cv-btn--primary" type="submit" form={formId} disabled={busy}>{busy ? "Adding…" : "Add robot"}</button>
    </>}>
    <form id={formId} className="cv-form cv-form--dialog" onSubmit={event => void submit(event)} noValidate>
      <div className="cv-field">
        <label htmlFor={`${id}-name`}>Name</label>
        <input id={`${id}-name`} className="cv-input" type="text" value={name} maxLength={60} autoComplete="off" disabled={busy} data-autofocus=""
          aria-invalid={checked && errors.name ? true : undefined} aria-describedby={checked && errors.name ? `${id}-name-error` : undefined} onChange={event => setName(event.target.value)} />
        {checked && errors.name && <span className="cv-field__error" id={`${id}-name-error`}>{errors.name}</span>}
      </div>
      <fieldset className="cv-field" disabled={busy}>
        <legend>Type</legend>
        <div className="cv-segment">{KINDS.map(item => <label key={item.id} className="cv-segment__item">
          <input type="radio" name={`${id}-kind`} value={item.id} checked={kind === item.id} onChange={() => setKind(item.id)} />{item.label}
        </label>)}</div>
      </fieldset>
      {kind === "device" && <div className="cv-field">
        <label htmlFor={`${id}-device`}>Device</label>
        <select id={`${id}-device`} className="cv-input" value={chosenDevice} disabled={busy || !deviceChoices.length} onChange={event => setDeviceId(event.target.value)}
          aria-invalid={checked && errors.device ? true : undefined} aria-describedby={checked && errors.device ? `${id}-device-error` : undefined}>
          {!deviceChoices.length && <option value="">Loading devices…</option>}
          {deviceChoices.map(choice => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
        </select>
        {checked && errors.device && <span className="cv-field__error" id={`${id}-device-error`}>{errors.device}</span>}
      </div>}
      {kind === "offline" && <fieldset className="cv-field" disabled={busy}>
        <legend>Offline evals</legend>
        <OfflinePicker value={offlineIds} onChange={setOfflineIds} describedBy={checked && errors.offline ? `${id}-offline-error` : undefined} />
        {checked && errors.offline && <span className="cv-field__error" id={`${id}-offline-error`}>{errors.offline}</span>}
      </fieldset>}
      {kind !== "offline" && <label className="cv-field">Evals from
        <select className="cv-input" value={projectId} disabled={busy} onChange={event => { setProjectId(event.target.value); setPlatformRobotId(""); }}>
          <option value="">None</option>
          {projectList.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}
        </select>
      </label>}
      {kind !== "offline" && projectId && <label className="cv-field">Platform robot
        <select className="cv-input" value={platformRobotId} disabled={busy} onChange={event => setPlatformRobotId(event.target.value)}>
          <option value="">All robots</option>
          {robotList.map(robot => <option key={robot.id} value={robot.id}>{robot.name}</option>)}
        </select>
      </label>}
      {error && <p className="cv-form-error" role="alert">{error}</p>}
    </form>
  </Modal>;
}
