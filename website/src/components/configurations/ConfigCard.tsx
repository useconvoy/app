import Link from "next/link";
import type { ReactNode } from "react";
import { Icon } from "./Icons";

/** 3 / 2 / 1-column grid of configuration cards. */
export function ConfigCards({ children }: { children: ReactNode }) { return <div className="cfg-cards">{children}</div>; }

/**
 * A configuration card: the whole card is one link (no interactive children).
 * Title, status badges on their own row, an optional verdict note, purpose, the
 * four-part stack (robot, edge hardware, edge models, cloud models, plus extra
 * rows) and a mono foot (counts, attention badges, latest eval with provenance,
 * "Updated 12 min ago").
 */
export function ConfigCard({ href, name, badges, note, purpose, stack, foot, updated }: {
  href: string; name: string; badges: ReactNode; note?: ReactNode; purpose: string;
  stack: ReadonlyArray<{ label: string; value: ReactNode }>; foot: ReactNode; updated?: ReactNode;
}) {
  return <Link className="cfg-card" href={href}>
    <div className="cfg-card__head"><h2 className="cfg-card__title">{name}</h2><span className="cfg-badges">{badges}</span></div>
    {note}
    <p className="cfg-card__purpose">{purpose}</p>
    <dl className="cfg-card__stack">{stack.map(row => <div key={row.label}><dt>{row.label}</dt><dd>{row.value}</dd></div>)}</dl>
    <div className="cfg-card__foot">{foot}{updated && <span className="cfg-card__updated">{updated}</span>}</div>
  </Link>;
}

/** Dashed "Add configuration" tile at the end of the grid. */
export function AddConfigurationTile({ href, label = "Add configuration" }: { href: string; label?: string }) {
  return <Link className="cfg-card cfg-card--add" href={href}><Icon name="plus" />{label}</Link>;
}
