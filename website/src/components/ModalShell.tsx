"use client";

/**
 * The shared modal shell: backdrop, dialog box, and the two dismissals
 * every modal owes its user, the Escape key and a click outside the box.
 * Content stays the caller's; the shell only owns open-state chrome.
 * Dismissal calls onClose exactly once; the caller decides what closing
 * means (usually setOpen(false) without resetting drafts).
 */
import { useEffect, useRef, type ReactNode } from "react";

export interface ModalShellProps {
  labelledBy: string;
  onClose: () => void;
  children: ReactNode;
  /** Width class for the dialog box; defaults to a form-sized column. */
  widthClassName?: string;
}

export function ModalShell({
  labelledBy,
  onClose,
  children,
  widthClassName = "max-w-lg",
}: ModalShellProps) {
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  // Move focus into the dialog so Escape and tabbing act on it immediately.
  useEffect(() => {
    dialogRef.current?.focus();
  }, []);

  return (
    <div
      className="fixed inset-0 z-40 flex items-start justify-center overflow-y-auto bg-ink/40 p-6"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        tabIndex={-1}
        className={[
          "w-full rounded-lg border border-line bg-card p-6 shadow-lg outline-none",
          widthClassName,
        ].join(" ")}
      >
        {children}
      </div>
    </div>
  );
}
