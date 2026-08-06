import Link from "next/link";

import { RouteMark } from "@/components/brand/route-mark";

import { MobileNav } from "./mobile-nav";

const NAV_ITEMS = [
  { href: "/platform", label: "Platform" },
  { href: "/solutions", label: "Solutions" },
  { href: "/security", label: "Security" },
  { href: "/company", label: "Company" },
  { href: "/writing", label: "Writing" },
] as const;

/**
 * Marketing header: the mark and wordmark, primary nav, sign in, and the demo
 * CTA.
 *
 * Sticky with a translucent backdrop, so the page reads as one continuous
 * document while scrolling rather than a stack of screens. The header itself
 * stays a server component; only the small-screen toggle is client code.
 */
export function SiteHeader() {
  return (
    <header className="sticky top-0 z-50 border-b border-line-soft bg-[color-mix(in_srgb,var(--color-field)_88%,transparent)] backdrop-blur-[10px]">
      <div className="mx-auto flex h-[68px] max-w-[1160px] items-center justify-between gap-6 px-5 sm:px-7">
        <Link
          href="/"
          className="flex items-center gap-2.5 text-ink"
          aria-label="Convoy Labs home"
        >
          <RouteMark width={30} />
          <span className="font-display text-[19px] font-extrabold tracking-[0.06em]">
            CONVOY
          </span>
        </Link>

        <nav aria-label="Main" className="hidden items-center gap-7 md:flex">
          {NAV_ITEMS.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="text-[14.5px] font-medium text-muted transition-[color] duration-75 hover:text-ink"
            >
              {item.label}
            </Link>
          ))}
        </nav>

        <div className="flex items-center gap-3">
          <Link
            href="/sign-in"
            className="hidden text-[14.5px] font-medium text-muted transition-[color] duration-75 hover:text-ink md:inline"
          >
            Sign in
          </Link>
          <Link
            href="/demo"
            className="inline-flex items-center rounded-[10px] bg-pine px-4 py-2.5 text-[14.5px] font-semibold text-card-alt transition-[background-color] duration-75 hover:bg-pine-deep"
          >
            Request a demo
          </Link>
          <MobileNav items={NAV_ITEMS} />
        </div>
      </div>
    </header>
  );
}
