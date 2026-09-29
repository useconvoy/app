"use client";

import { useEffect, useState } from "react";
import { api, ApiError, errorText } from "@/lib/platform/client";

interface Recording {
  episode_id: string; mission_id: string; release_digest: string; steps: number;
  skill: string | null; planner_ms: number | null; wall_seconds: number | null;
  sim_seconds: number | null; source: string;
}
interface Frame {
  index: number; image_png_base64: string; action: number[] | null;
  reward: number | null; success: boolean | null; policy_ms: number | null;
}
const measured = (value: number | null, unit: string) => value == null ? "Not recorded" : `${value.toFixed(2)} ${unit}`;

export function EpisodeReplay({ episodeId }: { episodeId: string }) {
  const [recording, setRecording] = useState<Recording | null>(null);
  const [frame, setFrame] = useState<Frame | null>(null);
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(5);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let current = true;
    void api<Recording>(`episodes/${episodeId}/replay`).then(value => {
      if (current) { setRecording(value); setError(null); }
    }).catch(cause => {
      if (current) setError(cause instanceof ApiError && cause.status === 404
        ? "No camera recording is available for this episode. Its outcome and technical details are still available below."
        : errorText(cause));
    });
    return () => { current = false; };
  }, [episodeId, attempt]);
  useEffect(() => {
    if (!recording) return;
    let current = true;
    void api<Frame>(`episodes/${episodeId}/replay/frames/${index}`).then(value => {
      if (current) { setFrame(value); setError(null); }
    }).catch(cause => { if (current) { setPlaying(false); setError(errorText(cause)); } });
    return () => { current = false; };
  }, [recording, episodeId, index, attempt]);
  useEffect(() => {
    if (!playing || !recording || frame?.index !== index || index >= recording.steps || error) return;
    const timer = window.setTimeout(() => setIndex(value => value + 1), 1000 / speed);
    return () => window.clearTimeout(timer);
  }, [playing, recording, frame, index, speed, error]);
  const visible = frame?.index === index ? frame : null;
  return <section className="episode-replay" aria-label="Episode playback">
    <div className="console-section-heading"><h4>Watch the simulation</h4><span className="console-status">Recorded episode</span></div>
    {error ? <p role="status">{error} <button onClick={() => setAttempt(value => value + 1)}>Retry recording</button></p> : !recording && <p role="status">Checking for recorded camera frames…</p>}
    {recording && <>
      <p className="console-note">{recording.source}. Playback speed is for viewing; this is not live video or a real-time performance claim.</p>
      <div className="replay-grid">
        <div>
          <div className="replay-camera">
            {visible ? /* Exact journal PNG; no image optimizer or external URL. */
              // eslint-disable-next-line @next/next/no-img-element
              <img src={`data:image/png;base64,${visible.image_png_base64}`} alt={`Recorded robot camera at action ${index} of ${recording.steps}`} width={480} height={480} />
              : <p role="status">Loading action {index}…</p>}
          </div>
          <label>Action {index} / {recording.steps}<input aria-label="Replay position" type="range" min={0} max={recording.steps} value={index} onChange={event => { setPlaying(false); setIndex(Number(event.target.value)); }} /></label>
          <div className="replay-controls">
            <button disabled={index === 0} onClick={() => { setPlaying(false); setIndex(value => Math.max(0, value - 1)); }}>Previous</button>
            <button disabled={!!error} onClick={() => { if (index === recording.steps) setIndex(0); setPlaying(value => !value || index === recording.steps); }}>{playing && index < recording.steps ? "Pause" : index === recording.steps ? "Replay" : "Play"}</button>
            <button disabled={index === recording.steps} onClick={() => { setPlaying(false); setIndex(value => Math.min(recording.steps, value + 1)); }}>Next</button>
            <label>Viewing speed<select value={speed} onChange={event => setSpeed(Number(event.target.value))}>{[2, 5, 10].map(value => <option key={value} value={value}>{value} actions/s</option>)}</select></label>
          </div>
        </div>
        <div className="replay-readout">
          <h4>Planner → skill</h4>
          <p><strong>{recording.skill ?? "No planner decision recorded"}</strong><br />Planner time: {measured(recording.planner_ms, "ms")}</p>
          <h4>Policy → action</h4>
          <p className="console-note">Normalized end-effector movement and gripper command. These are not joint angles or motor torques.</p>
          {visible?.action ? <dl>{visible.action.map((value, axis) => <div key={axis}><dt>{["X", "Y", "Z", "Gripper"][axis]}</dt><dd>{value.toFixed(4)}</dd></div>)}</dl> : <p>{index === 0 ? "Initial observation; no action applied yet." : "Loading action…"}</p>}
          <p>Policy time: {measured(visible?.policy_ms ?? null, "ms")}<br />Reward: {measured(visible?.reward ?? null, "")}<br />Task success: {visible?.success == null ? "Not evaluated" : visible.success ? "Yes" : "No"}</p>
          <h4>Episode timing</h4><p>Wall time: {measured(recording.wall_seconds, "s")}<br />Simulated time: {measured(recording.sim_seconds, "s")}</p>
        </div>
      </div>
    </>}
  </section>;
}
