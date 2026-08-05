import { SiteFooter } from "./components/site-footer";
import { SiteHeader } from "./components/site-header";

/** Shared frame for marketing pages only; the portal has its own shell. */
export default function MarketingLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-h-screen flex-col bg-field">
      <SiteHeader />
      <main className="flex-1">{children}</main>
      <SiteFooter />
    </div>
  );
}
