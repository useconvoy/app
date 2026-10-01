import Link from "next/link";
import type { ReactNode } from "react";
import { Icon, type IconName } from "./Icons";

/** A plain-sentence title and the next step; never an illustration. Inside panels prefer `portal-empty`. */
export function EmptyState({ title, text, action, icon = "search" }: { title: ReactNode; text?: ReactNode; action?: ReactNode; icon?: IconName }) {
  return <div className="cfg-empty"><Icon name={icon} /><p className="cfg-empty__title">{title}</p>{text && <p className="cfg-empty__text">{text}</p>}{action}</div>;
}

/** `portal-loading` with `role="status"`. */
export function LoadingState({ label = "Reading the workspace…" }: { label?: string }) {
  return <div className="portal-loading" role="status">{label}</div>;
}

/** Unknown id in the URL: says what was not found and links back. */
export function NotFoundState({ title, text, href, linkLabel }: { title: string; text?: ReactNode; href: string; linkLabel: string }) {
  return <EmptyState icon="info" title={title} text={text} action={<Link className="btn btn-secondary cfg-btn" href={href}>{linkLabel}</Link>} />;
}

/**
 * Marks a page body that is still to be built: a dashed panel listing what the
 * page will show. Page work replaces it.
 */
export function PagePlaceholder({ title, items }: { title: string; items: readonly string[] }) {
  return <section className="cfg-placeholder" aria-label="Page body placeholder">
    <p className="portal-eyebrow">Placeholder</p>
    <p className="cfg-placeholder__title">{title}</p>
    <p className="cfg-placeholder__text">This page body is built in the next step. The page frame, data and states above are final.</p>
    <ul>{items.map(item => <li key={item}>{item}</li>)}</ul>
  </section>;
}
