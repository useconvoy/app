import { ContactSection } from "@/components/ContactSection";
import { DesignPartnerSection } from "@/components/DesignPartnerSection";
import { ExecutionPath } from "@/components/ExecutionPath";
import { FAQ } from "@/components/FAQ";
import { Hero } from "@/components/Hero";
import { Problem } from "@/components/Problem";
import { ReleaseWorkflow } from "@/components/ReleaseWorkflow";
import { SiteFooter } from "@/components/SiteFooter";
import { SiteHeader } from "@/components/SiteHeader";

/**
 * The landing page, in the order the brief specifies: hero, problem,
 * workflow, execution path, design partnership, FAQ, contact, footer.
 * Everything is server-rendered; the header menu, motion control, and decorative hero field are the
 * client boundaries.
 */
export default function HomePage() {
  return (
    <>
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-50 focus:rounded focus:bg-surface focus:px-4 focus:py-3 focus:text-primary focus:outline focus:outline-2 focus:outline-accent"
      >
        Skip to content
      </a>
      <SiteHeader />
      <main id="main" tabIndex={-1}>
        <Hero />
        <Problem />
        <ReleaseWorkflow />
        <ExecutionPath />
        <DesignPartnerSection />
        <FAQ />
        <ContactSection />
      </main>
      <SiteFooter />
    </>
  );
}
