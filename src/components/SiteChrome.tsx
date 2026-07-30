"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { IconArrowUpRight } from "./icons";

const SECTIONS = [
  { id: "problem", label: "The problem" },
  { id: "run", label: "One run" },
  { id: "gateway", label: "Gateway" },
  { id: "lifecycle", label: "Lifecycle" },
  { id: "reality", label: "What's real" },
] as const;

/**
 * Marketing header. On the landing page it scroll-spies the section anchors so
 * a reader always knows where they are in a long page; on every other public
 * page the same markup renders without the observer.
 */
export function SiteHeader({ withSectionNav = false }: { withSectionNav?: boolean }) {
  const [active, setActive] = useState<string | null>(null);

  useEffect(() => {
    if (!withSectionNav) return;
    if (typeof IntersectionObserver === "undefined") return;

    const targets = SECTIONS.map((section) => document.getElementById(section.id)).filter(
      (element): element is HTMLElement => element !== null,
    );
    if (targets.length === 0) return;

    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
        if (visible) setActive(visible.target.id);
      },
      { rootMargin: "-45% 0px -50% 0px" },
    );
    targets.forEach((target) => observer.observe(target));
    return () => observer.disconnect();
  }, [withSectionNav]);

  return (
    <header className="site-header">
      <div className="wrap site-header-inner">
        {/* No aria-label: the visible text is the accessible name, so a
            voice-control user saying "Convoy Labs" matches it (WCAG 2.5.3). */}
        <Link href="/" className="logo">
          <span className="brand-mark" aria-hidden="true">
            C
          </span>
          <span>Convoy Labs</span>
        </Link>

        {withSectionNav && (
          <nav className="site-nav" aria-label="Page sections">
            {SECTIONS.map((section) => (
              <a
                key={section.id}
                href={`#${section.id}`}
                aria-current={active === section.id ? "true" : undefined}
              >
                {section.label}
              </a>
            ))}
          </nav>
        )}

        {/* The walkthrough is the primary action, and its label is short enough
            to stay on one line at 320px. The workspace link drops out below
            640px, where it would compete for the same row. */}
        <div className="site-header-actions">
          <Link className="header-link" href="/dashboard">
            Sample workspace
          </Link>
          <Link className="btn btn-primary site-cta" href="/start">
            Start here <IconArrowUpRight size={15} />
          </Link>
        </div>
      </div>
    </header>
  );
}

export function SiteFooter() {
  return (
    <footer className="site-footer">
      <div className="wrap">
        <div className="footer-grid">
          <div>
            <Link href="/" className="logo">
              <span className="brand-mark" aria-hidden="true">
                C
              </span>
              <span>Convoy Labs</span>
            </Link>
            <p>
              The control plane for company agents. This site and the workspace behind it are a
              working proof of concept: the governance lifecycle runs for real, and the external
              systems it acts on are simulations you can open and inspect.
            </p>
          </div>

          <nav aria-label="Product">
            <span>Product</span>
            <Link href="/start">Guided walkthrough</Link>
            <Link href="/dashboard">Sample workspace</Link>
            <Link href="/environments">Environments &amp; policies</Link>
            <Link href="/systems">Simulated systems</Link>
          </nav>

          <nav aria-label="Evidence">
            <span>Evidence</span>
            <Link href="/agents/agent_closed_won_paperwork">Closed-Won agent</Link>
            <Link href="/runs/run_hist_1">A complete run trace</Link>
            <Link href="/approvals">Approval queue</Link>
            <Link href="/audit">Audit log</Link>
          </nav>

          <nav aria-label="Account">
            <span>Account</span>
            <Link href="/login">Sign in</Link>
            <Link href="/register">Create an account</Link>
            <span className="micro" style={{ textTransform: "none", letterSpacing: 0 }}>
              Accounts are optional. The walkthrough needs no credentials.
            </span>
          </nav>
        </div>

        <div className="wrap footer-bottom" style={{ width: "100%" }}>
          <span>Convoy Labs — company agents, governed.</span>
          <span>
            Meridian Labs, its people, companies, deals, credentials, and activity are fictional
            sample data.
          </span>
        </div>
      </div>
    </footer>
  );
}
