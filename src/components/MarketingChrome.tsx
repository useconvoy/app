import Link from "next/link";
import { IconArrowUpRight } from "./icons";

export function MarketingHeader() {
  return (
    <header className="marketing-header">
      <div className="marketing-container marketing-header-inner">
        <Link href="/" className="marketing-brand" aria-label="C Convoy Labs home">
          <span className="brand-mark">C</span>
          <span>Convoy Labs</span>
        </Link>
        <nav className="marketing-nav" aria-label="Marketing">
          <Link href="/#how-it-works">How it works</Link>
          <Link href="/#architecture">Architecture</Link>
          <Link href="/#fit">Good agent work</Link>
          <Link href="/#faq">FAQ</Link>
        </nav>
        <div className="marketing-header-actions">
          <Link className="marketing-link" href="/login">
            Sign in
          </Link>
          <Link className="btn btn-primary marketing-cta" href="/register">
            Create account <IconArrowUpRight size={15} />
          </Link>
        </div>
      </div>
    </header>
  );
}

export function MarketingFooter() {
  return (
    <footer className="marketing-footer">
      <div className="marketing-container marketing-footer-grid">
        <div>
          <Link href="/" className="marketing-brand">
            <span className="brand-mark">C</span>
            <span>Convoy Labs</span>
          </Link>
          <p>
            The control plane for company agents. A working, self-contained proof of concept built around a fictional
            RevOps workspace.
          </p>
        </div>
        <nav aria-label="Product">
          <span>Product</span>
          <Link href="/start">Guided demo</Link>
          <Link href="/register">Create account</Link>
          <Link href="/dashboard">Sample workspace</Link>
          <Link href="/environments">Environments</Link>
          <Link href="/audit">Audit log</Link>
        </nav>
        <nav aria-label="Evidence">
          <span>Evidence</span>
          <Link href="/agents/agent_closed_won_paperwork">Closed-Won agent</Link>
          <Link href="/runs/run_hist_1">Sample run trace</Link>
          <Link href="/approvals">Approval queue</Link>
          <Link href="/systems">Simulated systems</Link>
        </nav>
      </div>
      <div className="marketing-container marketing-footer-bottom">
        <span>Convoy Labs · Company agents, governed.</span>
        <span>Meridian Labs, its people, companies, credentials, and activity are fictional sample data.</span>
      </div>
    </footer>
  );
}
