import Link from "next/link";

const NAV_ITEMS = [
  { href: "/platform", label: "Platform" },
  { href: "/solutions", label: "Solutions" },
  { href: "/security", label: "Security" },
  { href: "/company", label: "Company" },
  { href: "/writing", label: "Writing" },
] as const;

/** Marketing header: wordmark, primary nav, sign in, and the demo CTA. */
export function SiteHeader() {
  return (
    <header className="border-b border-line bg-field">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-6 px-6 py-4">
        <Link
          href="/"
          className="font-display text-xl font-semibold tracking-tight text-ink"
        >
          Convoy
        </Link>
        <nav aria-label="Main" className="hidden items-center gap-6 md:flex">
          {NAV_ITEMS.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="text-sm text-muted hover:text-ink"
            >
              {item.label}
            </Link>
          ))}
        </nav>
        <div className="flex items-center gap-4">
          <Link href="/sign-in" className="text-sm text-muted hover:text-ink">
            Sign in
          </Link>
          <Link
            href="/demo"
            className="inline-flex items-center rounded-md bg-pine px-4 py-2 text-sm font-medium text-card hover:bg-pine-deep"
          >
            Request a demo
          </Link>
        </div>
      </div>
    </header>
  );
}
