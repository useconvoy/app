"use client";

import { useEffect, useId, useRef, useState } from "react";

import { NAV, SITE } from "@/content/homepage";

/**
 * Sticky header, 72px on desktop and 64px on mobile, taller only if its row
 * wraps under text enlargement; its measured height drives the root's
 * --header-height, so scroll padding keeps anchor targets and focused
 * controls out from under it and the open menu never exceeds the viewport.
 * Wordmark left, ordinary anchor links and one primary action right. Below
 * 900px the wordmark, Contact, and a real menu button remain: a disclosure
 * (aria-expanded / aria-controls) that Escape closes with focus returned to
 * the button, a pointer-down outside closes, and a link click closes. The
 * link for the section in view carries aria-current="true" in both navs.
 */
export function SiteHeader() {
  const [open, setOpen] = useState(false);
  const [current, setCurrent] = useState<string | null>(null);
  const headerRef = useRef<HTMLElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLElement>(null);
  const menuId = useId();

  // The header's real height (it may wrap under text enlargement) feeds the
  // root's --header-height, which sets scroll padding for anchors and focused
  // controls and the open menu's available height.
  useEffect(() => {
    const header = headerRef.current;
    if (!header || typeof ResizeObserver === "undefined") return;
    const apply = () => document.documentElement.style.setProperty("--header-height", `${Math.ceil(header.getBoundingClientRect().height)}px`);
    apply();
    const observer = new ResizeObserver(apply);
    observer.observe(header);
    return () => {
      observer.disconnect();
      document.documentElement.style.removeProperty("--header-height");
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        setOpen(false);
        buttonRef.current?.focus();
      }
    }
    function onPointerDown(event: PointerEvent) {
      const target = event.target as Node;
      if (menuRef.current?.contains(target) || buttonRef.current?.contains(target)) return;
      setOpen(false);
    }
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointerDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointerDown);
    };
  }, [open]);

  // The current link follows the section that occupies the reading band of
  // the viewport. Sections that are not linked from the nav clear it.
  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return;
    const linked = new Set(NAV.links.map((link) => link.href.slice(1)));
    const sections = Array.from(document.querySelectorAll<HTMLElement>("main section[id]"));
    if (sections.length === 0) return;
    const visible = new Map<string, number>();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) visible.set(entry.target.id, entry.intersectionRatio);
          else visible.delete(entry.target.id);
        }
        let best: string | null = null;
        let bestRatio = 0;
        for (const [id, ratio] of visible) {
          if (ratio > bestRatio) {
            best = id;
            bestRatio = ratio;
          }
        }
        setCurrent(best && linked.has(best) ? `#${best}` : null);
      },
      { rootMargin: "-25% 0px -55% 0px", threshold: [0, 0.1, 0.25, 0.5, 0.75, 1] },
    );
    sections.forEach((section) => observer.observe(section));
    return () => observer.disconnect();
  }, []);

  const contact = NAV.links[NAV.links.length - 1];
  const isCurrent = (href: string) => (current === href ? true : undefined);

  return (
    <header ref={headerRef} className="sticky top-0 z-30 border-b border-subtle bg-background">
      <div className="mx-auto flex min-h-16 w-full max-w-(--content-max) flex-wrap items-center justify-between gap-x-4 gap-y-2 px-5 py-2 sm:px-8 md:min-h-[72px] md:gap-x-6 md:px-10 lg:px-16">
        <a href="#top" className="text-xl font-semibold tracking-[-0.02em] text-primary no-underline" aria-label={`${SITE.name} home`}>
          {SITE.name}
        </a>

        <nav aria-label="Main" className="hidden items-center gap-2 md:flex">
          {NAV.links.map((link) => (
            <a
              key={link.href}
              href={link.href}
              aria-current={isCurrent(link.href)}
              className="inline-flex min-h-12 items-center rounded px-3 text-base font-medium text-secondary transition-colors duration-(--duration-feedback) hover:bg-surface-subtle hover:text-primary aria-[current]:text-primary aria-[current]:underline aria-[current]:decoration-accent aria-[current]:decoration-1 aria-[current]:underline-offset-[6px]"
            >
              {link.label}
            </a>
          ))}
          <a href={NAV.cta.href} className="btn btn-primary ml-2">
            {NAV.cta.label}
          </a>
        </nav>

        <div className="flex items-center gap-2 md:hidden">
          <a href={contact.href} className="type-label inline-flex min-h-12 items-center px-2 text-primary">
            {contact.label}
          </a>
          <button
            ref={buttonRef}
            type="button"
            className="btn btn-secondary min-w-12 gap-2 px-3 text-sm"
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
        ref={menuRef}
        aria-label="Main"
        hidden={!open}
        data-mobile-menu
        className="absolute inset-x-0 top-full z-20 max-h-[calc(100dvh-var(--header-height))] overflow-y-auto border-b border-subtle bg-surface shadow-[0_12px_24px_-16px_rgba(24,34,31,0.25)] md:hidden"
      >
        <ul className="mx-auto flex w-full max-w-(--content-max) flex-col px-5 pt-2 pb-4 sm:px-8">
          {NAV.links.map((link) => (
            <li key={link.href}>
              <a
                href={link.href}
                aria-current={isCurrent(link.href)}
                onClick={() => setOpen(false)}
                className="type-body flex min-h-14 items-center border-b border-subtle font-medium text-primary hover:bg-surface-subtle aria-[current]:underline aria-[current]:decoration-accent aria-[current]:decoration-1 aria-[current]:underline-offset-[6px]"
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
