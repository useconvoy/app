"use client";

import { useEffect, useRef, type ReactNode } from "react";

import { isMotionPaused, subscribeMotionPaused } from "@/lib/motion-preference";

/**
 * The etched field: a faint grid on the paper behind the hero figure, with
 * thin rings that expand and fade. The foreground (children) is untouched
 * and stays crisp; the field is decoration, aria-hidden and never focusable.
 *
 * Behavior (Claude Design's approved exploration, ported):
 * - Idle waves start at random exposed places every 3–5 s, favoring the
 *   margins so they show without running under the copy.
 * - A mouse entering the field starts a wave at the pointer; while hovering,
 *   meaningful movement (≥ 24 px, at most every 650 ms) starts another at
 *   the current position. Existing rings keep their centers. Leaving resumes
 *   random origins.
 * - On touch, a short single-finger tap (< 300 ms, under 10 px of movement
 *   at any point in the gesture) starts a wave at the tap. Scrolling and
 *   pinching are never prevented; a gesture that moves, is cancelled, or
 *   gains a second finger starts nothing.
 * - A wave is three rings 230 ms apart, 2.5–3.2 s each. At most two waves
 *   (six rings) exist; an intentional wave at the cap retires the oldest.
 * - Nothing runs while the field is mostly offscreen, the document is
 *   hidden, the visitor has paused motion, or the OS asks for reduced
 *   motion (checked at mount and on change). Resize clears rings and stale
 *   pointer coordinates; a mouse still inside re-establishes its origin on
 *   its next move, and bounds are read at interaction time. Everything is
 *   torn down on unmount.
 *
 * The server renders the field empty; rings are created only in the browser
 * after mount, so the markup is deterministic.
 */
const IDLE_MIN = 3000;
const IDLE_MAX = 5000;
const HOVER_INTERVAL = 650;
const HOVER_DISTANCE = 24;
const TAP_DISTANCE = 10;
const TAP_TIME = 300;
const MAX_WAVES = 2;
const RINGS_PER_WAVE = 3;
const RING_STAGGER = 230;

type Wave = { rings: HTMLDivElement[]; done: number };
type Point = [number, number];

export function EtchedField({ children, className = "" }: { children: ReactNode; className?: string }) {
  const rootRef = useRef<HTMLDivElement>(null);
  const wavesRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const root = rootRef.current;
    const layer = wavesRef.current;
    if (!root || !layer) return;

    const reducedQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    const fineQuery = window.matchMedia("(pointer: fine)");
    let alive = true;
    let visible = false;
    let timer: number | null = null;
    let hover: Point | null = null;
    let lastPos: Point | null = null;
    let lastEmit = 0;
    let ink = false;
    let tap: { id: number; x: number; y: number; t: number; moved: number } | null = null;
    const touches = new Set<number>();
    let waves: Wave[] = [];
    let waveId = 0;

    const rnd = (a: number, b: number) => a + Math.random() * (b - a);
    const now = () => performance.now();

    const reason = (): string | null => {
      if (!alive) return "inactive";
      if (reducedQuery.matches) return "reduced";
      if (isMotionPaused()) return "paused";
      if (document.hidden) return "hidden";
      if (!visible) return "offscreen";
      return null;
    };
    const active = () => reason() === null;

    const clearTimer = () => {
      if (timer !== null) {
        window.clearTimeout(timer);
        timer = null;
      }
    };
    const retire = (wave: Wave) => {
      wave.rings.forEach((ring) => ring.remove());
      waves = waves.filter((w) => w !== wave);
    };
    const stop = () => {
      clearTimer();
      waves.slice().forEach(retire);
      layer.textContent = "";
    };

    /** Starts one wave at field coordinates; a priority wave may retire the oldest at the cap. */
    const emit = (x: number, y: number, priority: boolean): boolean => {
      if (!active()) return false;
      if (waves.length >= MAX_WAVES) {
        if (!priority) return false;
        retire(waves[0]);
      }
      ink = !ink;
      const w = layer.clientWidth;
      const h = layer.clientHeight;
      const diameter = Math.max(w, h) * 0.9;
      const duration = rnd(2500, 3200);
      const wave: Wave = { rings: [], done: 0 };
      const id = ++waveId;
      for (let i = 0; i < RINGS_PER_WAVE; i++) {
        const ring = document.createElement("div");
        ring.className = "etched-ring";
        if (ink) ring.dataset.ink = "";
        ring.dataset.wave = String(id);
        ring.style.left = `${x}px`;
        ring.style.top = `${y}px`;
        ring.style.width = `${diameter}px`;
        ring.style.height = `${diameter}px`;
        ring.style.setProperty("--d", `${duration}ms`);
        ring.style.animationDelay = `${i * RING_STAGGER}ms`;
        ring.addEventListener("animationend", () => {
          ring.remove();
          if (++wave.done === RINGS_PER_WAVE) waves = waves.filter((w) => w !== wave);
        });
        wave.rings.push(ring);
        layer.appendChild(ring);
      }
      waves.push(wave);
      lastEmit = now();
      return true;
    };

    /** A random origin, mostly in the left and right margins, sometimes along the top or bottom edge. */
    const idlePoint = (): Point => {
      const w = layer.clientWidth;
      const h = layer.clientHeight;
      const s = Math.random();
      if (s < 0.4) return [rnd(0.04, 0.24) * w, rnd(0.1, 0.9) * h];
      if (s < 0.8) return [rnd(0.76, 0.96) * w, rnd(0.1, 0.9) * h];
      return [rnd(0.2, 0.8) * w, (Math.random() < 0.5 ? rnd(0.02, 0.1) : rnd(0.9, 0.98)) * h];
    };

    const schedule = (ms: number) => {
      clearTimer();
      timer = window.setTimeout(() => {
        timer = null;
        if (!active()) return;
        const p = hover ?? idlePoint();
        emit(p[0], p[1], false);
        schedule(rnd(IDLE_MIN, IDLE_MAX));
      }, ms);
    };

    const sync = () => {
      const why = reason();
      root.dataset.etchedField = why ?? "active";
      if (why === null) {
        if (timer === null && waves.length === 0) schedule(rnd(IDLE_MIN, IDLE_MAX));
      } else {
        stop();
      }
    };

    /** Pointer position relative to the field, from the current bounds. */
    const local = (event: PointerEvent): Point => {
      const rect = layer.getBoundingClientRect();
      return [event.clientX - rect.left, event.clientY - rect.top];
    };

    const reset = () => {
      stop();
      hover = null;
      lastPos = null;
      tap = null;
      touches.clear();
      sync();
    };

    const listeners: Array<() => void> = [];
    function on<K extends keyof HTMLElementEventMap>(target: HTMLElement, type: K, handler: (event: HTMLElementEventMap[K]) => void): void;
    function on<K extends keyof DocumentEventMap>(target: Document, type: K, handler: (event: DocumentEventMap[K]) => void): void;
    function on<K extends keyof WindowEventMap>(target: Window, type: K, handler: (event: WindowEventMap[K]) => void): void;
    function on(target: MediaQueryList, type: "change", handler: (event: MediaQueryListEvent) => void): void;
    function on(target: EventTarget, type: string, handler: (event: never) => void) {
      target.addEventListener(type, handler as EventListener);
      listeners.push(() => target.removeEventListener(type, handler as EventListener));
    }

    on(root, "pointerenter", (event) => {
      if (event.pointerType !== "mouse" || !fineQuery.matches) return;
      hover = local(event);
      lastPos = hover;
      if (active()) {
        emit(hover[0], hover[1], true);
        schedule(rnd(IDLE_MIN, IDLE_MAX));
      }
    });
    on(root, "pointermove", (event) => {
      if (event.pointerType === "touch") {
        if (tap && event.pointerId === tap.id) tap.moved = Math.max(tap.moved, Math.hypot(event.clientX - tap.x, event.clientY - tap.y));
        return;
      }
      if (event.pointerType !== "mouse" || !fineQuery.matches) return;
      const p = local(event);
      if (!hover || !lastPos) {
        // No known origin (a resize cleared it while the mouse stayed inside): this move is the new entry.
        hover = p;
        lastPos = p;
        if (now() - lastEmit >= HOVER_INTERVAL && active()) {
          emit(p[0], p[1], true);
          schedule(rnd(IDLE_MIN, IDLE_MAX));
        }
        return;
      }
      hover = p;
      const distance = Math.hypot(p[0] - lastPos[0], p[1] - lastPos[1]);
      if (now() - lastEmit >= HOVER_INTERVAL && distance >= HOVER_DISTANCE && active()) {
        lastPos = p;
        emit(p[0], p[1], true);
        schedule(rnd(IDLE_MIN, IDLE_MAX));
      }
    });
    on(root, "pointerleave", (event) => {
      if (event.pointerType !== "mouse") return;
      hover = null;
      lastPos = null;
      if (active()) schedule(rnd(IDLE_MIN, IDLE_MAX));
    });
    on(root, "pointerdown", (event) => {
      if (event.pointerType !== "touch") return;
      touches.add(event.pointerId);
      // Only a lone finger can be a tap; a second finger (pinch, two-finger scroll) cancels the intent.
      tap = touches.size === 1 ? { id: event.pointerId, x: event.clientX, y: event.clientY, t: now(), moved: 0 } : null;
    });
    on(root, "pointerup", (event) => {
      if (event.pointerType !== "touch") return;
      touches.delete(event.pointerId);
      if (!tap) return;
      if (event.pointerId !== tap.id) {
        tap = null;
        return;
      }
      const moved = Math.max(tap.moved, Math.hypot(event.clientX - tap.x, event.clientY - tap.y));
      const short = moved < TAP_DISTANCE && now() - tap.t < TAP_TIME && touches.size === 0;
      tap = null;
      if (short && active()) {
        const p = local(event);
        emit(p[0], p[1], true);
        schedule(rnd(IDLE_MIN, IDLE_MAX));
      }
    });
    on(root, "pointercancel", (event) => {
      touches.delete(event.pointerId);
      tap = null;
    });
    on(reducedQuery, "change", sync);
    on(document, "visibilitychange", sync);
    on(window, "resize", reset);
    const unsubscribe = subscribeMotionPaused(sync);

    let observer: IntersectionObserver | null = null;
    if ("IntersectionObserver" in window) {
      observer = new IntersectionObserver(
        (entries) => {
          visible = entries[0].intersectionRatio >= 0.35;
          sync();
        },
        { threshold: [0, 0.35, 0.6] },
      );
      observer.observe(root);
    } else {
      visible = true;
      sync();
    }
    let resizeObserver: ResizeObserver | null = null;
    if ("ResizeObserver" in window) {
      let first = true;
      resizeObserver = new ResizeObserver(() => {
        if (first) {
          first = false;
          return;
        }
        reset();
      });
      resizeObserver.observe(layer);
    }
    sync();

    return () => {
      alive = false;
      stop();
      listeners.forEach((off) => off());
      unsubscribe();
      observer?.disconnect();
      resizeObserver?.disconnect();
      delete root.dataset.etchedField;
    };
  }, []);

  return (
    <div ref={rootRef} className={`etched-field relative ${className}`} data-etched-field="inactive">
      <div aria-hidden="true" className="etched-grid" />
      <div ref={wavesRef} aria-hidden="true" className="etched-waves" />
      <div className="relative z-[1]">{children}</div>
    </div>
  );
}
