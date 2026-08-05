import Link from "next/link";

/** Closing call-to-action band shared by marketing pages. */
export function CtaBand({
  title = "See a routine run with your own eyes",
  body = "We will walk you through a live rehearsal, checkpoints and all, in about thirty minutes.",
}: {
  title?: string;
  body?: string;
}) {
  return (
    <section
      aria-labelledby="cta-band-heading"
      className="border-t border-line bg-card"
    >
      <div className="mx-auto max-w-3xl px-6 py-16 text-center">
        <h2
          id="cta-band-heading"
          className="font-display text-3xl font-medium text-ink"
        >
          {title}
        </h2>
        <p className="mx-auto mt-4 max-w-xl text-muted">{body}</p>
        <div className="mt-8 flex justify-center gap-4">
          <Link
            href="/demo"
            className="inline-flex items-center rounded-md bg-pine px-5 py-2.5 text-sm font-medium text-card hover:bg-pine-deep"
          >
            Request a demo
          </Link>
          <Link
            href="/platform"
            className="inline-flex items-center rounded-md border border-line bg-card px-5 py-2.5 text-sm font-medium text-ink hover:border-muted"
          >
            Explore the platform
          </Link>
        </div>
      </div>
    </section>
  );
}
