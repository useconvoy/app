"use client";

import { useEffect, useId, useRef, useState } from "react";

import { NAV, SITE } from "@/content/homepage";

/**
 * 72px header on desktop, 64px on mobile, in flow rather than sticky so
 * nothing ever covers an anchor target or a focused control. Wordmark left,
 * native anchor links and one primary action right.
 * Below 900px only the wordmark, Contact, and a real menu button remain;
 * the button reports aria-expanded, the menu closes on Escape and hands
 * focus back to the button, and links close it on click.
 */
export function SiteHeader() {
  const [open, setOpen] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuId = useId();

  useEffect(() => {
    if (!open) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  const contact = NAV.links[NAV.links.length - 1];

  return (
    <header className="relative border-b border-subtle bg-background">
      <div className="mx-auto flex h-16 w-full max-w-(--content-max) items-center justify-between gap-6 px-5 sm:px-8 md:h-[72px] md:px-10 lg:px-16">
        <a href="#top" className="text-[20px] font-semibold tracking-[-0.02em] text-primary no-underline" aria-label={`${SITE.name} home`}>
          {SITE.name}
        </a>

        <nav aria-label="Main" className="hidden items-center gap-2 md:flex">
          {NAV.links.map((link) => (
            <a
              key={link.href}
              href={link.href}
              className="inline-flex min-h-12 items-center rounded px-3 text-[16px] font-medium text-secondary transition-colors duration-(--duration-feedback) hover:bg-surface-subtle hover:text-primary"
            >
              {link.label}
            </a>
          ))}
          <a href={NAV.cta.href} className="btn btn-primary ml-2">
            {NAV.cta.label}
          </a>
        </nav>

        <div className="flex items-center gap-2 md:hidden">
          <a href={contact.href} className="type-label inline-flex min-h-12 items-center px-3 text-primary">
            {contact.label}
          </a>
          <button
            ref={buttonRef}
            type="button"
            className="btn btn-secondary min-w-12 gap-2 px-3 text-[14px]"
            aria-expanded={open}
            aria-controls={menuId}
            onClick={() => setOpen((value) => !value)}
          >
            <span aria-hidden="true" className="flex h-3 w-[18px] flex-col justify-between">
              <span className="block h-[2px] rounded-full bg-current" />
              <span className="block h-[2px] rounded-full bg-current" />
              <span className="block h-[2px] rounded-full bg-current" />
            </span>
            {open ? "Close" : "Menu"}
          </button>
        </div>
      </div>

      <nav
        id={menuId}
        aria-label="Main"
        hidden={!open}
        data-mobile-menu
        className="absolute inset-x-0 top-full z-20 border-b border-subtle bg-surface shadow-[0_12px_24px_-16px_rgba(24,34,31,0.25)] md:hidden"
      >
        <ul className="mx-auto flex w-full max-w-(--content-max) flex-col px-5 pt-2 pb-4 sm:px-8">
          {NAV.links.map((link) => (
            <li key={link.href}>
              <a
                href={link.href}
                onClick={() => setOpen(false)}
                className="type-body flex min-h-14 items-center border-b border-subtle font-medium text-primary hover:bg-surface-subtle"
              >
                {link.label}
              </a>
            </li>
          ))}
          <li className="pt-4">
            <a href={NAV.cta.href} onClick={() => setOpen(false)} className="btn btn-primary w-full">
              {NAV.cta.label}
            </a>
          </li>
        </ul>
      </nav>
    </header>
  );
}
