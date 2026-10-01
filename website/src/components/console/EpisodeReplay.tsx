"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { api, ApiError, errorText } from "@/lib/platform/client";

/** `GET episodes/{id}/replay`: the recording's metadata. */
interface Recording {
  episode_id: string; mission_id: string; release_digest: string; steps: number;
  skill: string | null; planner_ms: number | null; wall_seconds: number | null;
  sim_seconds: number | null; source: string;
}
/** `GET episodes/{id}/replay/frames/{index}`: the camera frame before action `index` and that action. */
interface Frame {
  index: number; image_png_base64: string; action: number[] | null;
  reward: number | null; success: boolean | null; policy_ms: number | null;
}

/** Viewing speeds in steps per second. Playback speed is for viewing only; it is not a timing claim. */
const SPEEDS = [2, 5, 10] as const;
/** Frames read ahead of the playhead, so playback does not wait on each request. */
const AHEAD = 4;
/** Frames kept in memory; the ones farthest from the playhead go first. */
const KEEP = 80;
const AXES = ["X", "Y", "Z", "Grip"];

const finite = (value: number | null | undefined): value is number => typeof value === "number" && Number.isFinite(value);
const ms = (value: number | null | undefined) => finite(value) ? `${Math.round(value).toLocaleString("en-US")} ms` : "–";
const seconds = (value: number | null | undefined) => finite(value) ? `${value.toFixed(value < 10 ? 2 : 1)} s` : "–";

/**
 * The episode replay: the recorded simulator camera frames of one control-plane
 * episode, played back with the planner's skill and each step's applied action,
 * policy time, reward and success. Frames are read through the platform proxy a
 * few steps ahead of the playhead; the last frame stays on screen while the next
 * one loads. Space plays or pauses; the arrow keys step.
 */
export function EpisodeReplay({ episodeId }: { episodeId: string }) {
  const [recording, setRecording] = useState<Recording | null>(null);
  const [problem, setProblem] = useState<{ text: string; retry: boolean } | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [frames, setFrames] = useState<ReadonlyMap<number, Frame>>(() => new Map());
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<number>(5);
  const requested = useRef(new Set<number>());
  const steps = recording?.steps ?? 0;

  useEffect(() => {
    let current = true;
    void api<Recording>(`episodes/${encodeURIComponent(episodeId)}/replay`).then(value => {
      if (current) { setRecording(value); setProblem(null); }
    }).catch(cause => {
      if (!current) return;
      const missing = cause instanceof ApiError && cause.status === 404;
      setProblem({ text: missing ? "No recording for this episode." : errorText(cause), retry: !missing });
    });
    return () => { current = false; };
  }, [episodeId, attempt]);

  // Read the frame at the playhead and the next few; keep at most KEEP frames.
  useEffect(() => {
    if (!recording) return;
    let current = true;
    for (let i = index; i <= Math.min(recording.steps, index + AHEAD); i++) {
      if (frames.has(i) || requested.current.has(i)) continue;
      requested.current.add(i);
      void api<Frame>(`episodes/${encodeURIComponent(episodeId)}/replay/frames/${i}`).then(frame => {
        setFrames(previous => {
          const next = new Map(previous).set(i, frame);
          if (next.size > KEEP) {
            const far = [...next.keys()].toSorted((a, b) => Math.abs(b - i) - Math.abs(a - i)).slice(0, next.size - KEEP);
            for (const key of far) { next.delete(key); requested.current.delete(key); }
          }
          return next;
        });
      }).catch(cause => {
        requested.current.delete(i);
        if (current) { setPlaying(false); setProblem({ text: errorText(cause), retry: true }); }
      });
    }
    return () => { current = false; };
  }, [recording, episodeId, index, frames]);

  // Advance once the next frame is in; stop at the last step.
  useEffect(() => {
    if (!playing || !recording || problem || index >= recording.steps || !frames.has(index + 1)) return;
    const timer = window.setTimeout(() => {
      setIndex(index + 1);
      if (index + 1 >= recording.steps) setPlaying(false);
    }, 1000 / speed);
    return () => window.clearTimeout(timer);
  }, [playing, recording, problem, index, frames, speed]);

  const retry = useCallback(() => { setProblem(null); requested.current.clear(); setAttempt(value => value + 1); setFrames(previous => new Map(previous)); }, []);
  const go = useCallback((next: number) => { setPlaying(false); setIndex(Math.max(0, Math.min(steps, next))); }, [steps]);
  const toggle = useCallback(() => {
    if (!recording) return;
    if (index >= recording.steps) { setIndex(0); setPlaying(true); return; }
    setPlaying(value => !value);
  }, [recording, index]);

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const target = event.target as HTMLElement;
    if (target.closest("input, select, textarea")) return;
    if (event.key === " " && !target.closest("button")) { event.preventDefault(); toggle(); }
    else if (event.key === "ArrowRight") { event.preventDefault(); go(index + 1); }
    else if (event.key === "ArrowLeft") { event.preventDefault(); go(index - 1); }
  }

  if (!recording) {
    return <div className="cv-player cv-player--status" role="status">
      {problem ? <p>{problem.text} {problem.retry && <button className="cv-link" type="button" onClick={retry}>Retry</button>}</p> : <p><span className="cv-spinner" aria-hidden="true" />Loading recording…</p>}
    </div>;
  }

  // The frame at the playhead, else the nearest earlier one while it loads.
  let shown: Frame | undefined;
  for (let i = index; i >= 0 && !shown; i--) shown = frames.get(i);
  const exact = frames.get(index) ?? null;
  const reward = exact?.reward ?? null, success = exact?.success ?? null;
  const ended = index >= recording.steps;
  const label = playing && !ended ? "Pause" : ended ? "Replay" : "Play";
  const progress = recording.steps ? index / recording.steps * 100 : 0;

  return <div className="cv-player" onKeyDown={onKeyDown}>
    <div className="cv-player__stage">
      <div className="cv-player__frame">
        {shown
          /* The recorded PNG as stored; no image optimizer or external URL. */
          // eslint-disable-next-line @next/next/no-img-element
          ? <img src={`data:image/png;base64,${shown.image_png_base64}`} alt={`Recorded robot camera at action ${shown.index} of ${recording.steps}`} width={480} height={480} />
          : <span className="cv-player__wait"><span className="cv-spinner" aria-hidden="true" />Loading frame…</span>}
        <span className="cv-player__step" aria-hidden="true">{index} / {recording.steps}</span>
        {shown && !exact && <span className="cv-player__buffer" role="status">Loading…</span>}
      </div>
      <div className="cv-player__bar" aria-hidden="true"><span style={{ width: `${progress}%` }} /></div>
      <div className="cv-player__controls">
        <button className="cv-ctl" type="button" aria-label="Previous step" disabled={index === 0} onClick={() => go(index - 1)}>
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M12 3.5v9L6 8zM3.5 3.5v9" /></svg></button>
        <button className="cv-ctl cv-ctl--play" type="button" aria-label={label} disabled={!!problem} onClick={toggle}>
          <svg viewBox="0 0 16 16" aria-hidden="true"><path fill={label === "Play" ? "currentColor" : "none"} d={label === "Pause" ? "M5.5 3.5v9M10.5 3.5v9" : label === "Replay" ? "M2.75 8a5.25 5.25 0 1 0 1.6-3.8M2.75 2.75v2.5h2.5" : "M5.5 3.5v9l7-4.5z"} /></svg></button>
        <button className="cv-ctl" type="button" aria-label="Next step" disabled={ended} onClick={() => go(index + 1)}>
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 3.5v9l6-4.5zM12.5 3.5v9" /></svg></button>
        <input className="cv-range" type="range" min={0} max={recording.steps} value={index} aria-label="Replay position"
          aria-valuetext={`Step ${index} of ${recording.steps}`} onChange={event => go(Number(event.target.value))} />
        <select className="cv-speed" aria-label="Playback speed" value={speed} onChange={event => setSpeed(Number(event.target.value))}>
          {SPEEDS.map(value => <option key={value} value={value}>{value} steps/s</option>)}
        </select>
      </div>
      {problem && <p className="cv-player__problem" role="alert">{problem.text} <button className="cv-link" type="button" onClick={retry}>Retry</button></p>}
    </div>
    <dl className="cv-player__readout">
      <div><dt>Step</dt><dd>{index} / {recording.steps}</dd></div>
      <div><dt>Skill</dt><dd className="cv-mono">{recording.skill ?? "–"}</dd></div>
      <div><dt>Planner</dt><dd>{ms(recording.planner_ms)}</dd></div>
      <div className="cv-player__action"><dt>Action</dt><dd>{AXES.map((axis, i) => {
        const value = exact?.action?.[i];
        return <span key={axis}><small>{axis}</small>{finite(value) ? value.toFixed(2) : "–"}</span>;
      })}</dd></div>
      <div><dt>Policy</dt><dd>{ms(exact?.policy_ms)}</dd></div>
      <div><dt>Reward</dt><dd>{finite(reward) ? reward.toFixed(2) : "–"}</dd></div>
      <div><dt>Success</dt><dd>{success === null ? "–" : success ? "Yes" : "No"}</dd></div>
      <div><dt>Wall time</dt><dd>{seconds(recording.wall_seconds)}</dd></div>
      <div><dt>Sim time</dt><dd>{seconds(recording.sim_seconds)}</dd></div>
    </dl>
  </div>;
}
