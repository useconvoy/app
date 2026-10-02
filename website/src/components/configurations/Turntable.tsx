"use client";

import Image from "next/image";
import { useRef, useState, useSyncExternalStore } from "react";
import type { RobotPreview } from "@/lib/configurations/previews";
import { Icon } from "./Icons";

const REDUCE = "(prefers-reduced-motion: reduce)";
function subscribe(onChange: () => void) {
  const query = window.matchMedia(REDUCE);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

/** The OS asks for reduced motion. The server and hydration render as reduced, so nothing moves before the client knows. */
export function useReducedMotion(): boolean {
  return useSyncExternalStore(subscribe, () => window.matchMedia(REDUCE).matches, () => true);
}

/**
 * A simulated robot turning once round, on the stage colour: a muted, looping, inline video with a
 * pause button, or only its first frame when the visitor asks for reduced motion.
 */
export function Turntable({ preview }: { preview: RobotPreview }) {
  const reduced = useReducedMotion();
  const video = useRef<HTMLVideoElement>(null);
  const [paused, setPaused] = useState(true);
  function toggle() {
    const element = video.current;
    if (!element) return;
    if (element.paused) element.play().catch(() => setPaused(true));
    else element.pause();
  }
  return <div className="cv-stage" style={{ aspectRatio: `${preview.width} / ${preview.height}` }}>
    {reduced
      ? <Image src={preview.poster} width={preview.width} height={preview.height} alt={preview.label} />
      : <>
        <video ref={video} poster={preview.poster} width={preview.width} height={preview.height} aria-label={preview.label}
          autoPlay muted loop playsInline preload="auto" onPlay={() => setPaused(false)} onPause={() => setPaused(true)}>
          {preview.sources.map(source => <source key={source.src} src={source.src} type={source.type} />)}
        </video>
        <button className="cv-stage__toggle" type="button" onClick={toggle} aria-label={paused ? "Play rotation" : "Pause rotation"}>
          <Icon name={paused ? "play" : "pause"} />
        </button>
      </>}
  </div>;
}
