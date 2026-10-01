"use client";

import { useEffect, useId, useRef } from "react";
import type { ReactNode, RefObject } from "react";
import { Icon } from "./Icons";

const FOCUSABLE = "a[href], button:not([disabled]), input:not([disabled]):not([type=hidden]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])";

/**
 * Dialog behaviour shared by Modal and Drawer while `open`: focus moves into the
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
    (node?.querySelector<HTMLElement>("[data-autofocus]") ?? focusables()[0] ?? node)?.focus();
    document.body.classList.add("cfg-locked");
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
      document.body.classList.remove("cfg-locked");
      if (opener?.isConnected) opener.focus();
    };
  }, [open, dialog]);
}

interface OverlayProps { open: boolean; onClose: () => void; title: ReactNode; eyebrow?: ReactNode; children: ReactNode; footer?: ReactNode; closeLabel?: string; subtitle?: ReactNode }

/** Centred dialog over a scrim (`cfg-overlay` > `cfg-dialog`); 640px wide, no shadow. Clicking the scrim closes it. */
export function Modal({ open, onClose, title, eyebrow, children, footer, closeLabel = "Close", subtitle }: OverlayProps) {
  const ref = useRef<HTMLElement>(null);
  const id = useId();
  useDialog(open, ref, onClose);
  if (!open) return null;
  return <div className="cfg-overlay" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <section ref={ref} className="cfg-dialog" role="dialog" aria-modal="true" aria-labelledby={`${id}-title`} tabIndex={-1}>
      <div className="cfg-dialog__head"><div>{eyebrow && <p className="portal-eyebrow">{eyebrow}</p>}<h2 id={`${id}-title`}>{title}</h2>{subtitle}</div>
        <button className="cfg-icon-btn cfg-icon-btn--ghost" type="button" aria-label={closeLabel} onClick={onClose}><Icon name="close" /></button></div>
      <div className="cfg-dialog__body">{children}</div>
      {footer && <div className="cfg-dialog__foot">{footer}</div>}
    </section>
  </div>;
}

/** Right drawer, 600px, full height (full width on phones), e.g. for `?trace=` and `?rollout=`. */
export function Drawer({ open, onClose, title, eyebrow, children, footer, closeLabel = "Close", subtitle }: OverlayProps) {
  const ref = useRef<HTMLElement>(null);
  const id = useId();
  useDialog(open, ref, onClose);
  if (!open) return null;
  return <div className="cfg-overlay cfg-overlay--drawer" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <aside ref={ref} className="cfg-drawer" role="dialog" aria-modal="true" aria-labelledby={`${id}-title`} tabIndex={-1}>
      <div className="cfg-drawer__head"><div>{eyebrow && <p className="portal-eyebrow">{eyebrow}</p>}<h2 id={`${id}-title`}>{title}</h2>{subtitle}</div>
        <button className="cfg-icon-btn cfg-icon-btn--ghost" type="button" aria-label={closeLabel} onClick={onClose}><Icon name="close" /></button></div>
      <div className="cfg-drawer__body">{children}</div>
      {footer && <div className="cfg-drawer__foot">{footer}</div>}
    </aside>
  </div>;
}
