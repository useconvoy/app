import type { Metadata } from "next";
import Link from "next/link";
import { SiteFooter, SiteHeader } from "@/components/SiteChrome";
import { StartGuide } from "@/components/StartGuide";
import { IconArrowUpRight, IconCheck, IconHand } from "@/components/icons";
import "../marketing.css";

export const metadata: Metadata = {
  title: "Start here — the guided Convoy Labs walkthrough",
  description:
    "Seven steps, about eight minutes. Follow one company agent through environments, testing, promotion, two human approvals and the audit trail in the Meridian Labs sample workspace.",
  alternates: { canonical: "/start" },
  openGraph: {
    type: "website",
    url: "/start",
    title: "Start here — the guided Convoy Labs walkthrough",
    description:
      "Follow one agent from Sandbox to Production and watch the gateway stop it twice. No account, no key, no install.",
  },
};

export default function StartPage() {
  return (
    <div className="site">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <SiteHeader />

      {/* tabIndex -1 so the skip link actually moves focus, not just the scroll
          position (WCAG 2.4.1). */}
      <main id="main" tabIndex={-1}>
        <section className="start-hero" aria-labelledby="start-title">
          <div className="wrap start-hero-grid">
            <div>
              <p className="eyebrow">
                <span className="eyebrow-rule" aria-hidden="true" />
                Guided walkthrough · about eight minutes
              </p>
              <h1 className="display" id="start-title">
                Start with one agent. Follow every consequential decision it makes.
              </h1>
              <p className="lede">
                You are about to enter Meridian Labs — a fictional revenue-operations workspace with
                two environments, four company agents, and a night of seeded history behind it. Seven
                steps, in order, each one telling you what to do and what to watch for.
              </p>
              <div className="hero-actions">
                <a className="btn btn-primary" href="#walkthrough">
                  Begin at step one <IconArrowUpRight size={16} />
                </a>
                <Link className="btn" href="/dashboard">
                  Skip to the workspace
                </Link>
              </div>
            </div>

            <aside className="start-brief">
              <p className="eyebrow">
                <span className="eyebrow-rule" aria-hidden="true" />
                Before you go in
              </p>
              <h2>Know which half is real.</h2>
              <dl>
                <div>
                  <dt className="is-real">
                    <IconCheck size={12} /> Implemented
                  </dt>
                  <dd>
                    The policy gateway, environments and their rules, the scenario suite, the
                    promotion gate, approvals, run traces, the audit log and the kill switch. All of
                    it runs.
                  </dd>
                </div>
                <div>
                  <dt className="is-sim">
                    <IconHand size={12} /> Simulated
                  </dt>
                  <dd>
                    The systems on the other side of the gateway, the credentials it injects, and
                    every person, company and deal in Meridian Labs.
                  </dd>
                </div>
              </dl>
              <p>
                No login, API key or setup. Steps 4 to 6 change the shared sample workspace, and each
                one says so before you click.
              </p>
            </aside>
          </div>
        </section>

        <section className="band" id="walkthrough" aria-labelledby="walkthrough-title">
          <div className="wrap-narrow">
            <div className="band-head" style={{ marginBottom: 34 }}>
              <p className="eyebrow">
                <span className="eyebrow-rule" aria-hidden="true" />
                The path
              </p>
              <h2 className="h2" id="walkthrough-title">
                From fleet overview to causal audit.
              </h2>
              <p className="lede">
                Each step says what it changes before you click it. Three of the seven write to the
                shared sample workspace; the rest only read. Your progress is kept on this device and
                nowhere else.
              </p>
            </div>
            <StartGuide />
          </div>
        </section>

        <section className="band band-paper" aria-labelledby="next-title">
          <div className="wrap">
            <div className="band-head">
              <p className="eyebrow">
                <span className="eyebrow-rule" aria-hidden="true" />
                After the walkthrough
              </p>
              <h2 className="h2" id="next-title">
                Pick the thread you want to pull.
              </h2>
            </div>

            <div className="band-body">
              <div className="lanes">
                <Link className="lane" href="/environments">
                  <span className="lane-role">IT &amp; security</span>
                  <h3>Credentials and permission rules</h3>
                  <p>
                    Which handle each environment binds, and every rule with its effect, conditions,
                    approvers and timeout.
                  </p>
                  <span className="lane-go">
                    Open environments <IconArrowUpRight size={15} />
                  </span>
                </Link>

                <Link className="lane" href="/approvals">
                  <span className="lane-role">Operations</span>
                  <h3>The approval queue</h3>
                  <p>
                    Both kinds of pause in one place: what the agent wanted to do, why it stopped,
                    and the payload you are signing off on.
                  </p>
                  <span className="lane-go">
                    Open approvals <IconArrowUpRight size={15} />
                  </span>
                </Link>

                <Link className="lane" href="/audit">
                  <span className="lane-role">Technical evaluation</span>
                  <h3>The audit trail</h3>
                  <p>
                    Append-only, with causal references from an event back to its run, tool call,
                    approval and decision.
                  </p>
                  <span className="lane-go">
                    Open the audit log <IconArrowUpRight size={15} />
                  </span>
                </Link>
              </div>
            </div>
          </div>
        </section>
      </main>

      <SiteFooter />
    </div>
  );
}
