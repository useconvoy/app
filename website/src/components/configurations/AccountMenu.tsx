"use client";

import { useEffect, useId, useRef, useState } from "react";
import { useSession } from "./Session";

/** Up to two initials from the address's local part ("ada.lovelace@…" → "AL"); never the address itself. */
export function initials(email: string): string {
  const parts = (email.split("@")[0] ?? "").split(/[^\p{L}\p{N}]+/u).filter(Boolean);
  return parts.slice(0, 2).map(part => part[0]).join("").toUpperCase() || "A";
}

/** The signed-in account as initials; its menu holds Sign out. The address is not shown. */
export function AccountMenu() {
  const session = useSession();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const id = useId();
  useEffect(() => {
    if (!open) return;
    root.current?.querySelector<HTMLElement>("[data-menu-item]")?.focus();
    function onPointer(event: PointerEvent) { if (!root.current?.contains(event.target as Node)) setOpen(false); }
    function onKey(event: KeyboardEvent) { if (event.key === "Escape") { setOpen(false); button.current?.focus(); } }
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("pointerdown", onPointer); document.removeEventListener("keydown", onKey); };
  }, [open]);
  // Tabbing away closes it; a pointer press outside is handled above (some browsers do not focus a clicked button).
  return <div className="cv-account" ref={root} onBlur={event => { if (open && event.relatedTarget && !root.current?.contains(event.relatedTarget)) setOpen(false); }}>
    <button ref={button} className="cv-account__button" type="button" aria-label="Account" aria-expanded={open} aria-controls={`${id}-menu`} onClick={() => setOpen(value => !value)}>
      <span className="cv-avatar" aria-hidden="true">{initials(session.email)}</span>
      <svg className="cv-icon cv-icon--sm" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 6l4 4 4-4" /></svg>
    </button>
    <div className="cv-account__menu" id={`${id}-menu`} hidden={!open}>
      <button className="cv-account__item" type="button" data-menu-item="" disabled={session.signingOut} onClick={() => void session.signOut()}>{session.signingOut ? "Signing out…" : "Sign out"}</button>
    </div>
  </div>;
}
