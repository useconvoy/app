import Link from "next/link";

const FOOTER_COLUMNS = [
  {
    heading: "Product",
    links: [
      { href: "/platform", label: "Platform" },
      { href: "/solutions", label: "Solutions" },
      { href: "/changelog", label: "Changelog" },
    ],
  },
  {
    heading: "Company",
    links: [
      { href: "/company", label: "About" },
      { href: "/writing", label: "Writing" },
      { href: "/demo", label: "Request a demo" },
    ],
  },
  {
    heading: "Trust",
    links: [
      { href: "/security", label: "Security" },
      { href: "/terms", label: "Terms" },
      { href: "/privacy", label: "Privacy" },
    ],
  },
] as const;

/** Marketing footer: nav columns, legal links, copyright. */
export function SiteFooter() {
  return (
    <footer className="border-t border-line bg-field">
      <div className="mx-auto max-w-6xl px-6 py-12">
        <div className="flex flex-col justify-between gap-10 sm:flex-row">
          <div>
            <p className="font-display text-lg font-semibold text-ink">Convoy</p>
            <p className="mt-2 max-w-xs text-sm text-muted">
              Routine work, done carefully.
            </p>
          </div>
          <nav
            aria-label="Footer"
            className="grid grid-cols-2 gap-8 sm:grid-cols-3"
          >
            {FOOTER_COLUMNS.map((column) => (
              <div key={column.heading}>
                <h2 className="text-sm font-semibold text-ink">
                  {column.heading}
                </h2>
                <ul className="mt-3 space-y-2">
                  {column.links.map((link) => (
                    <li key={link.href}>
                      <Link
                        href={link.href}
                        className="text-sm text-muted hover:text-ink"
                      >
                        {link.label}
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </nav>
        </div>
        <div className="mt-10 flex flex-col gap-2 border-t border-line-soft pt-6 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-xs text-muted">
            &copy; {new Date().getFullYear()} Convoy. All rights reserved.
          </p>
          <p className="text-xs text-muted">
            <Link href="/terms" className="hover:text-ink">
              Terms
            </Link>
            {" · "}
            <Link href="/privacy" className="hover:text-ink">
              Privacy
            </Link>
          </p>
        </div>
      </div>
    </footer>
  );
}
