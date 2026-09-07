import Link from "next/link";

import { FOOTER } from "@/content/homepage";

/** One top rule, the wordmark and category, real links, and a mono legal row. */
export function SiteFooter() {
  const year = new Date().getFullYear();
  return (
    <footer className="border-t border-subtle bg-background">
      <div className="mx-auto w-full max-w-(--content-max) px-5 pt-12 pb-8 sm:px-8 md:px-10 lg:px-16">
        <div className="flex flex-wrap items-start justify-between gap-x-12 gap-y-6">
          <div>
            <p className="type-body font-semibold tracking-tight text-primary">{FOOTER.wordmark}</p>
            <p className="type-small mt-2 max-w-[44ch] text-muted">{FOOTER.category}</p>
          </div>
          <nav aria-label="Footer">
            <ul className="flex flex-wrap gap-x-6 gap-y-2">
              {FOOTER.links.map((link) => (
                <li key={link.href}>
                  <a href={link.href} className="type-small inline-flex min-h-11 items-center text-secondary hover:text-primary hover:underline">
                    {link.label}
                  </a>
                </li>
              ))}
            </ul>
          </nav>
        </div>
        <div className="mt-8 flex flex-wrap justify-between gap-x-6 gap-y-2 border-t border-subtle pt-4 font-mono text-[0.8125rem] tracking-[0.02em] text-muted">
          <span>{FOOTER.copyright(year)}</span>
          <Link href="/" className="hover:text-primary hover:underline">
            {FOOTER.domain}
          </Link>
        </div>
      </div>
    </footer>
  );
}
