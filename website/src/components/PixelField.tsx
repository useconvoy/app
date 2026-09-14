"use client";

import { useEffect, useRef, type ReactNode } from "react";
import { isMotionPaused, subscribeMotionPaused } from "@/lib/motion-preference";

const CELL = 8;
const SPREAD = 96;
const MAX_PIXELS = 180;
const INTERVAL = 65;

/** A bounded, event-driven ink trail. No render loop or React updates per pointer move. */
export function PixelField({ children }: { children: ReactNode }) {
  const rootRef = useRef<HTMLDivElement>(null);
  const layerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const root = rootRef.current;
    const layer = layerRef.current;
    if (!root || !layer) return;
    const reduced = matchMedia("(prefers-reduced-motion: reduce)");
    const fine = matchMedia("(pointer: fine)");
    let visible = false;
    let lastEmit = -INTERVAL;
    let lastPoint: { x: number; y: number } | null = null;
    let idle: ReturnType<typeof setInterval> | undefined;
    const pixels = new Set<HTMLSpanElement>();
    const reason = () => reduced.matches ? "reduced" : isMotionPaused() ? "paused" : document.hidden ? "hidden" : !visible ? "offscreen" : "active";
    const clear = () => {
      pixels.forEach((pixel) => pixel.remove());
      pixels.clear();
      root.style.removeProperty("--pointer-x");
      root.style.removeProperty("--pointer-y");
      root.removeAttribute("data-hovering");
      lastPoint = null;
    };
    const emit = (x: number, y: number, ambient = false) => {
      if (reason() !== "active") return;
      const cx = Math.floor(x / CELL);
      const cy = Math.floor(y / CELL);
      for (let i = 0; i < (ambient ? 10 : 16); i++) {
        const dx = Math.round((Math.random() - 0.5) * (SPREAD * 2 / CELL));
        const dy = Math.round((Math.random() - 0.5) * (SPREAD * 2 / CELL));
        const px = (cx + dx) * CELL;
        const py = (cy + dy) * CELL;
        if (px < 0 || py < 0 || px > root.clientWidth - CELL || py > root.clientHeight - CELL) continue;
        if (pixels.size >= MAX_PIXELS) {
          const first = pixels.values().next().value;
          if (first) { first.remove(); pixels.delete(first); }
        }
        const pixel = document.createElement("span");
        pixel.className = "field-pixel";
        pixel.style.left = `${px}px`;
        pixel.style.top = `${py}px`;
        pixel.style.setProperty("--ink-opacity", String((ambient ? 0.2 : 0.5) * (0.3 + Math.random() * 0.7)));
        pixel.style.setProperty("--ink-life", `${1400 + Math.random() * 1000}ms`);
        if (i % 5 === 0) pixel.dataset.forest = "";
        pixel.addEventListener("animationend", () => { pixel.remove(); pixels.delete(pixel); }, { once: true });
        pixels.add(pixel);
        layer.appendChild(pixel);
      }
    };
    const sync = () => {
      root.dataset.pixelField = reason();
      clearInterval(idle);
      idle = undefined;
      if (reason() !== "active") clear();
      else idle = setInterval(() => {
        if (!lastPoint) emit(root.clientWidth * (0.64 + Math.random() * 0.3), root.clientHeight * Math.random(), true);
      }, 3200);
    };
    const move = (event: PointerEvent) => {
      if (event.pointerType !== "mouse" || !fine.matches || reason() !== "active") return;
      const bounds = root.getBoundingClientRect();
      const point = { x: event.clientX - bounds.left, y: event.clientY - bounds.top };
      root.style.setProperty("--pointer-x", `${point.x}px`);
      root.style.setProperty("--pointer-y", `${point.y}px`);
      root.dataset.hovering = "true";
      const now = performance.now();
      if (now - lastEmit < INTERVAL || (lastPoint && Math.hypot(point.x - lastPoint.x, point.y - lastPoint.y) < 9)) return;
      emit(point.x, point.y);
      lastEmit = now;
      lastPoint = point;
    };
    const leave = () => { lastPoint = null; root.removeAttribute("data-hovering"); };
    const resize = () => { clear(); lastEmit = -INTERVAL; };
    const observer = new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; sync(); }, { threshold: 0 });
    observer.observe(root);
    const resizer = new ResizeObserver(resize);
    resizer.observe(root);
    root.addEventListener("pointermove", move, { passive: true });
    root.addEventListener("pointerleave", leave, { passive: true });
    reduced.addEventListener("change", sync);
    document.addEventListener("visibilitychange", sync);
    const unsubscribe = subscribeMotionPaused(sync);
    sync();
    return () => {
      clearInterval(idle);
      clear();
      observer.disconnect();
      resizer.disconnect();
      unsubscribe();
      root.removeEventListener("pointermove", move);
      root.removeEventListener("pointerleave", leave);
      reduced.removeEventListener("change", sync);
      document.removeEventListener("visibilitychange", sync);
    };
  }, []);

  return (
    <div ref={rootRef} className="pixel-field" data-pixel-field="inactive">
      {children}
      <div className="pixel-grid" aria-hidden="true" />
      <div className="pixel-wash" aria-hidden="true" />
      <div ref={layerRef} className="pixel-trail" aria-hidden="true" />
    </div>
  );
}
