"use client";

import { useEffect, useId, useRef } from "react";
import type { ReactNode, RefObject } from "react";
import { Icon } from "./Icons";

const FOCUSABLE = "a[href], button:not([disabled]), input:not([disabled]):not([type=hidden]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])";

/**
 * Dialog behaviour shared by Modal and Sheet while `open`: focus moves into the
 * dialog (its `[data-autofocus]` element, else the first control), Tab stays
 * inside, Escape calls `onClose`, the page behind does not scroll, and focus
 * returns to the element that opened it.
 */
export function useDialog(open: boolean, dialog: RefObject<HTMLElement | null>, onClose: () => void) {
  const close = useRef(onClose);
  useEffect(() => { close.current = onClose; }, [onClose]);
  useEffect(() => {
    if (!open) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const node = dialog.current;
    const focusables = () => node ? Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(element => !element.closest("[hidden]")) : [];
    (node?.querySelector<HTMLElement>("[data-autofocus]") ?? focusables()[0] ?? node)?.focus({ preventScroll: true });
    document.body.classList.add("cv-locked");
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") { event.preventDefault(); close.current(); return; }
      if (event.key !== "Tab" || !node) return;
      const items = focusables();
      if (!items.length) { event.preventDefault(); node.focus(); return; }
      const first = items[0], last = items[items.length - 1];
      if (!node.contains(document.activeElement)) { event.preventDefault(); first.focus(); }
      else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.classList.remove("cv-locked");
      if (opener?.isConnected) opener.focus({ preventScroll: true });
    };
  }, [open, dialog]);
}

interface OverlayProps { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; footer?: ReactNode; meta?: ReactNode; closeLabel?: string }

/** A centred dialog over a scrim. Clicking the scrim closes it. */
export function Modal({ open, onClose, title, children, footer, closeLabel = "Close" }: OverlayProps) {
  const ref = useRef<HTMLElement>(null);
  const id = useId();
  useDialog(open, ref, onClose);
  if (!open) return null;
  return <div className="cv-scrim" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <section ref={ref} className="cv-modal" role="dialog" aria-modal="true" aria-labelledby={`${id}-title`} tabIndex={-1}>
      <div className="cv-modal__head"><h2 id={`${id}-title`}>{title}</h2>
        <button className="cv-icon-btn" type="button" aria-label={closeLabel} onClick={onClose}><Icon name="close" /></button></div>
      <div className="cv-modal__body">{children}</div>
      {footer && <div className="cv-modal__foot">{footer}</div>}
    </section>
  </div>;
}

/** "Delete …?" with Cancel first (focused) and the action. It stays open while the action runs and shows its error. */
export function ConfirmDialog({ title, action, busyAction, onConfirm, onClose, error, busy }: {
  title: string; action: string; busyAction: string; onConfirm: () => void; onClose: () => void; error: string | null; busy: boolean;
}) {
  return <Modal open onClose={onClose} title={title}
    footer={<>
      <button className="cv-btn cv-btn--secondary" type="button" onClick={onClose} data-autofocus="">Cancel</button>
      <button className="cv-btn cv-btn--danger" type="button" disabled={busy} onClick={onConfirm}>{busy ? busyAction : action}</button>
    </>}>
    {error ? <p className="cv-form-error" role="alert">{error}</p> : <p>This cannot be undone.</p>}
  </Modal>;
}

/** A panel that rises from the bottom of the page (the replay). Clicking above it closes it. */
export function Sheet({ open, onClose, title, meta, children, closeLabel = "Close" }: OverlayProps) {
  const ref = useRef<HTMLElement>(null);
  const id = useId();
  useDialog(open, ref, onClose);
  if (!open) return null;
  return <div className="cv-scrim cv-scrim--sheet" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <section ref={ref} className="cv-sheet" role="dialog" aria-modal="true" aria-labelledby={`${id}-title`} tabIndex={-1}>
      <div className="cv-sheet__head">
        <div className="cv-sheet__title"><h2 id={`${id}-title`} tabIndex={-1} data-autofocus="">{title}</h2>{meta}</div>
        <button className="cv-icon-btn" type="button" aria-label={closeLabel} onClick={onClose}><Icon name="close" /></button>
      </div>
      <div className="cv-sheet__body">{children}</div>
    </section>
  </div>;
}
