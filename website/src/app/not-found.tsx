import type { Metadata } from "next";
import Link from "next/link";

import { SiteFooter } from "@/components/SiteFooter";
import { SiteHeader } from "@/components/SiteHeader";

export const metadata: Metadata = {
  title: "Page not found",
  robots: { index: false, follow: false },
};

/**
 * Ordinary 404. Paths from the previous site are not redirected here by
 * design; whether to add redirects is a deployment decision (see README).
 */
export default function NotFound() {
  return (
    <>
      <SiteHeader />
      <main id="main" className="bg-background">
        <div className="mx-auto w-full max-w-(--content-max) px-5 py-20 sm:px-8 md:px-10 lg:px-16">
          <p className="type-eyebrow">404</p>
          <h1 className="type-h2 mt-3 text-primary">This page does not exist.</h1>
          <p className="type-lead mt-4 text-secondary">
            The address may be out of date. Everything Convoy publishes today is on the home page.
          </p>
          <Link href="/" className="btn btn-primary mt-8">
            Back to the home page
          </Link>
        </div>
      </main>
      <SiteFooter />
    </>
  );
}
