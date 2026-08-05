import type { Metadata } from "next";
import Link from "next/link";

import { SiteFooter } from "./(marketing)/components/site-footer";
import { SiteHeader } from "./(marketing)/components/site-header";

export const metadata: Metadata = {
  title: "Page not found",
  description: "This page does not exist.",
};

/** Root 404, framed with the marketing shell. */
export default function NotFound() {
  return (
    <div className="flex min-h-screen flex-col bg-field">
      <SiteHeader />
      <main className="flex flex-1 items-center justify-center px-6 py-24">
        <div className="text-center">
          <p className="font-mono text-xs tracking-widest text-muted">404</p>
          <h1 className="mt-4 font-display text-4xl font-medium text-ink">
            This page does not exist.
          </h1>
          <p className="mt-4 text-muted">
            The address may be old, or it may never have been real. Either
            way, nothing ran and nothing was recorded.
          </p>
          <Link
            href="/"
            className="mt-8 inline-flex items-center rounded-md bg-pine px-5 py-2.5 text-sm font-medium text-card hover:bg-pine-deep"
          >
            Back to the home page
          </Link>
        </div>
      </main>
      <SiteFooter />
    </div>
  );
}
