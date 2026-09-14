"use client";

import { useEffect, useRef, type ReactNode } from "react";
import { isMotionPaused, subscribeMotionPaused } from "@/lib/motion-preference";

/** A small optical focus marker and a light registered to the illustrated bulb. */
export function HeroMotion({ children }: { children: ReactNode }) {
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const root = rootRef.current;
    const art = root?.querySelector<HTMLImageElement>(".hero-art");
    if (!root || !art) return;
    const reduced = matchMedia("(prefers-reduced-motion: reduce)");
    const fine = matchMedia("(pointer: fine)");
    let visible = false;
    let lastGeometry = "";
    const reason = () => reduced.matches ? "reduced" : isMotionPaused() ? "paused" : document.hidden ? "hidden" : !visible ? "offscreen" : "active";
    const clear = () => {
      root.removeAttribute("data-hovering");
    };
    const sync = () => {
      root.dataset.heroMotion = reason();
      if (reason() !== "active" || !fine.matches) clear();
    };
    // object-fit crops differently on phones. Map the bulb's source-image
    // coordinates through that same fit, rather than guessing percentages.
    const positionLamp = () => {
      if (!art.naturalWidth) return;
      const box = art.getBoundingClientRect();
      const parent = root.getBoundingClientRect();
      const geometry = `${parent.width}:${parent.height}:${box.width}:${box.height}`;
      if (lastGeometry && geometry !== lastGeometry) clear();
      lastGeometry = geometry;
      const style = getComputedStyle(art);
      const fit = style.objectFit === "cover" ? Math.max : Math.min;
      const scale = fit(box.width / art.naturalWidth, box.height / art.naturalHeight);
      const [px, py] = style.objectPosition.split(" ").map((value) => parseFloat(value) / 100);
      const x = box.left - parent.left + (box.width - art.naturalWidth * scale) * px + 777 * scale;
      const y = box.top - parent.top + (box.height - art.naturalHeight * scale) * py + 225 * scale;
      root.style.setProperty("--lamp-x", `${x}px`);
      root.style.setProperty("--lamp-y", `${y}px`);
      root.style.setProperty("--lamp-size", `${90 * scale}px`);
      root.dataset.lampReady = "true";
    };
    const resize = () => { clear(); positionLamp(); };
    const move = (event: PointerEvent) => {
      if (event.pointerType !== "mouse" || !fine.matches || reason() !== "active") return;
      const bounds = root.getBoundingClientRect();
      root.style.setProperty("--pointer-x", `${event.clientX - bounds.left}px`);
      root.style.setProperty("--pointer-y", `${event.clientY - bounds.top}px`);
      root.dataset.hovering = "true";
    };
    const observer = new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; sync(); });
    observer.observe(root);
    const resizer = new ResizeObserver(positionLamp);
    resizer.observe(root);
    resizer.observe(art);
    art.addEventListener("load", positionLamp);
    window.addEventListener("resize", resize, { passive: true });
    root.addEventListener("pointermove", move, { passive: true });
    root.addEventListener("pointerleave", clear, { passive: true });
    reduced.addEventListener("change", sync);
    fine.addEventListener("change", sync);
    document.addEventListener("visibilitychange", sync);
    const unsubscribe = subscribeMotionPaused(sync);
    positionLamp();
    sync();
    return () => {
      clear();
      observer.disconnect();
      resizer.disconnect();
      unsubscribe();
      art.removeEventListener("load", positionLamp);
      window.removeEventListener("resize", resize);
      root.removeEventListener("pointermove", move);
      root.removeEventListener("pointerleave", clear);
      reduced.removeEventListener("change", sync);
      fine.removeEventListener("change", sync);
      document.removeEventListener("visibilitychange", sync);
    };
  }, []);

  return (
    <div ref={rootRef} className="hero-motion" data-hero-motion="inactive">
      {children}
      <div className="hero-lamp" aria-hidden="true" />
      <svg className="hero-focus" viewBox="0 0 36 36" aria-hidden="true" focusable="false">
        <path d="M3 11V3h8m14 0h8v8M3 25v8h8m14 0h8v-8" fill="none" stroke="currentColor" strokeWidth="1" />
        <circle cx="18" cy="18" r="8" fill="currentColor" fillOpacity=".08" />
        <path d="M15 18h6m-3-3v6" fill="none" stroke="currentColor" strokeWidth="1" />
      </svg>
    </div>
  );
}
