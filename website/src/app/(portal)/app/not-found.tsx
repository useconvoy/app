import Link from "next/link";

import { portalNotFoundCopy } from "@/lexicon";

/**
 * The console's own 404, rendered inside the portal shell so a mistyped
 * address never throws someone out to the marketing site. Calm and plain:
 * what happened, and the one way back.
 */
export default function PortalNotFound() {
  return (
    <div className="mx-auto flex max-w-5xl flex-col items-center py-24 text-center">
      <p className="font-mono text-xs tracking-widest text-muted">404</p>
      <h1 className="mt-4 font-display text-3xl text-ink">{portalNotFoundCopy.title}</h1>
      <p className="mt-3 max-w-md text-sm text-muted">{portalNotFoundCopy.body}</p>
      <Link
        href="/app"
        className="mt-8 inline-flex items-center rounded-md border border-pine bg-pine px-4 py-2 text-sm font-medium text-card hover:bg-pine-deep"
      >
        {portalNotFoundCopy.backToOverview}
      </Link>
    </div>
  );
}
