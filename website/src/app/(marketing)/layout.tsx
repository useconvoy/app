import { SiteFooter } from "./components/site-footer";
import { SiteHeader } from "./components/site-header";

/**
 * Shared frame for marketing pages only; the portal has its own shell.
 *
 * The frame isolates, so the hatched margins can sit at a negative index and
 * land behind the page's content without falling behind the page itself.
 */
export default function MarketingLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="relative isolate flex min-h-screen flex-col bg-field">
      {/* The margins of the sheet. Two hatched columns flanking the reading
          measure, held still while the page scrolls past them.

          Nothing is laid over the reading column itself: a pattern behind body
          text is a tax on every word above it, and on a wide screen the empty
          margins are the part of the page that has nothing else to do.

          They appear only once the window is wider than the measure plus room
          for a margin worth having. Sections that carry their own fill cross
          them, which is the intent: one sheet, with plates set into it. */}
      <div
        aria-hidden="true"
        className="pointer-events-none fixed inset-0 -z-10 hidden justify-between xl:flex"
      >
        <div className="hatch-margin w-[calc((100%-1160px)/2)] border-r border-line-soft" />
        <div className="hatch-margin w-[calc((100%-1160px)/2)] border-l border-line-soft" />
      </div>

      <SiteHeader />
      <main className="flex-1">{children}</main>
      <SiteFooter />
    </div>
  );
}
