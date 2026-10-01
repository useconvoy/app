import Link from "next/link";
import { isInternalHref } from "@/lib/configurations/routes";
import type { CheckVerdict, CompatibilityCheck } from "@/lib/configurations/types";
import { ProvenanceBadge } from "./Badges";
import { Icon, type IconName } from "./Icons";

const VERDICT: Record<CheckVerdict, { icon: IconName; label: string }> = {
  pass: { icon: "check", label: "Pass" }, warn: { icon: "warning", label: "Warning" }, block: { icon: "blocked", label: "Blocked" }, pending: { icon: "clock", label: "Not measured" },
};
export const VERDICT_LABEL: Record<CheckVerdict, string> = { pass: "Pass", warn: "Warning", block: "Blocked", pending: "Not measured" };

/**
 * One compatibility check: verdict icon, title, detail (with an optional action), verdict and evidence badge.
 * The action is a link only when its target is inside this app (`isInternalHref`); otherwise its label is plain text.
 */
export function CompatRow({ check, now }: { check: CompatibilityCheck; now?: number | null }) {
  const verdict = VERDICT[check.verdict];
  const href = check.action && isInternalHref(check.action.href) ? check.action.href : null;
  return <li className={`cfg-check cfg-check--${check.verdict}`}>
    <span className="cfg-check__icon"><Icon name={verdict.icon} /></span>
    <div>
      <p className="cfg-check__title">{check.title}</p>
      <p className="cfg-check__detail">{check.detail}{check.action && <> {href ? <Link className="cfg-btn-text" href={href}>{check.action.label}</Link> : <span>{check.action.label}.</span>}</>}</p>
    </div>
    <div className="cfg-check__meta"><span className="cfg-check__verdict">{verdict.label}</span><ProvenanceBadge provenance={check.evidence} now={now} /></div>
  </li>;
}

/** The checks list (place it in a panel). */
export function CompatList({ checks, now }: { checks: readonly CompatibilityCheck[]; now?: number | null }) {
  return <ul className="cfg-checks">{checks.map(check => <CompatRow key={check.id} check={check} now={now} />)}</ul>;
}
