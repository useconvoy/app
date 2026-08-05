"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

export interface PaletteRoute {
  href: string;
  label: string;
}

/**
 * The command-K jump: a trigger button in the top bar plus a modal dialog
 * listing the nav routes the current role can see. Arrows move, enter jumps,
 * escape closes and returns focus to the trigger; tab is trapped inside.
 */
export function CommandPalette({ routes }: { routes: PaletteRoute[] }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);

  const close = useCallback(() => {
    setOpen(false);
    triggerRef.current?.focus();
  }, []);

  const openPalette = useCallback(() => {
    setActiveIndex(0);
    setOpen(true);
  }, []);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        if (open) close();
        else openPalette();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, close, openPalette]);

  useEffect(() => {
    if (open) itemRefs.current[0]?.focus();
  }, [open]);

  function moveFocus(next: number) {
    const wrapped = (next + routes.length) % routes.length;
    setActiveIndex(wrapped);
    itemRefs.current[wrapped]?.focus();
  }

  function onDialogKeyDown(event: React.KeyboardEvent) {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      moveFocus(activeIndex + 1);
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      moveFocus(activeIndex - 1);
      return;
    }
    // Tab stays inside the dialog: the route buttons are its only stops.
    if (event.key === "Tab") {
      event.preventDefault();
      moveFocus(activeIndex + (event.shiftKey ? -1 : 1));
    }
  }

  function jump(href: string) {
    setOpen(false);
    triggerRef.current?.focus();
    router.push(href);
  }

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={openPalette}
        className="flex items-center gap-2 rounded-md border border-line px-3 py-1.5 text-sm text-muted transition-colors hover:border-line hover:text-ink"
      >
        Jump to
        <kbd className="rounded border border-line-soft bg-field px-1.5 py-0.5 font-mono text-[11px] text-muted">
          ⌘K
        </kbd>
      </button>
      {open ? (
        <div
          className="fixed inset-0 z-50 flex items-start justify-center bg-graphite/40 pt-[18vh]"
          onClick={close}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-label="Jump to"
            onClick={(event) => event.stopPropagation()}
            onKeyDown={onDialogKeyDown}
            className="w-full max-w-sm rounded-lg border border-line bg-card p-2 shadow-lg"
          >
            <p className="px-3 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-muted">
              Jump to
            </p>
            <ul className="flex flex-col">
              {routes.map((route, index) => (
                <li key={route.href}>
                  <button
                    ref={(element) => {
                      itemRefs.current[index] = element;
                    }}
                    type="button"
                    onClick={() => jump(route.href)}
                    onFocus={() => setActiveIndex(index)}
                    className={`w-full rounded-md px-3 py-2 text-left text-sm transition-colors ${
                      index === activeIndex ? "bg-field text-ink" : "text-muted"
                    }`}
                  >
                    {route.label}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </div>
      ) : null}
    </>
  );
}
