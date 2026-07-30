import type { Metadata } from "next";
import Link from "next/link";
import { MarketingFooter, MarketingHeader } from "@/components/MarketingChrome";
import { StartGuide } from "@/components/StartGuide";
import { IconArrowUpRight, IconCheck, IconShieldCheck } from "@/components/icons";

export const metadata: Metadata = {
  title: "Start Here — Guided Convoy Labs demo",
  description:
    "Follow a company agent through environments, testing, promotion, human approvals, and audit in the Meridian Labs sample workspace.",
  alternates: { canonical: "/start" },
};

export default function StartPage() {
  return (
    <div className="marketing-site start-site">
      <a className="skip-link" href="#start-main">Skip to content</a>
      <MarketingHeader />
      <main id="start-main">
        <section className="start-hero">
          <div className="marketing-container start-hero-grid">
            <div>
              <div className="hero-kicker">
                <span className="hero-kicker-line" aria-hidden="true" />
                Guided product walkthrough
              </div>
              <h1>Start with one agent. Follow every consequential decision.</h1>
              <p>
                You&apos;re entering Meridian Labs, a fictional RevOps workspace preloaded with two environments, four
                company agents, and sample overnight history. The complete path takes about eight minutes.
              </p>
              <div className="hero-actions">
                <a className="btn btn-primary marketing-hero-button" href="#walkthrough">
                  Begin the walkthrough <IconArrowUpRight size={16} />
                </a>
                <Link className="btn marketing-hero-button" href="/dashboard">Open workspace</Link>
              </div>
            </div>
            <aside className="start-brief-card">
              <span className="preview-eyebrow">Before you enter</span>
              <h2>Know what is real.</h2>
              <ul>
                <li><IconCheck size={15} /><span><b>Working:</b> gateway, policies, tests, promotion, approvals, traces, audit, kill switch.</span></li>
                <li><IconShieldCheck size={15} /><span><b>Simulated:</b> external systems, connector credentials, and all Meridian activity.</span></li>
              </ul>
              <p>No login, API key, or setup is required. Hands-on steps can change the shared sample state.</p>
            </aside>
          </div>
        </section>

        <section className="start-section" id="walkthrough" aria-labelledby="walkthrough-title">
          <div className="marketing-container start-layout">
            <div className="start-intro">
              <span className="marketing-section-label">The eight-minute path</span>
              <h2 id="walkthrough-title">From fleet overview to causal audit.</h2>
              <p>
                Each step tells you what to do, what to notice, and whether it changes the sample workspace. Your
                progress stays on this device only.
              </p>
            </div>
            <StartGuide />
          </div>
        </section>

        <section className="start-next">
          <div className="marketing-container start-next-grid">
            <div>
              <span className="marketing-section-label">Choose your next question</span>
              <h2>Go deeper without losing the thread.</h2>
            </div>
            <div className="start-next-links">
              <Link href="/environments">
                <span>Admin / security</span>
                <strong>Inspect credentials and policies</strong>
                <IconArrowUpRight size={16} />
              </Link>
              <Link href="/agents/agent_closed_won_paperwork">
                <span>Operator</span>
                <strong>Inspect the featured agent</strong>
                <IconArrowUpRight size={16} />
              </Link>
              <Link href="/runs/run_hist_1">
                <span>Technical evaluator</span>
                <strong>Read a complete tool-call trace</strong>
                <IconArrowUpRight size={16} />
              </Link>
            </div>
          </div>
        </section>
      </main>
      <MarketingFooter />
    </div>
  );
}
