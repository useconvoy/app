import Link from "next/link";
import type { ReactNode } from "react";

/** A short title, at most one short line, and the next step. */
export function EmptyState({ title, text, action }: { title: ReactNode; text?: ReactNode; action?: ReactNode }) {
  return <div className="cv-empty"><p className="cv-empty__title">{title}</p>{text && <p className="cv-empty__text">{text}</p>}{action}</div>;
}

export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return <div className="cv-loading" role="status"><span className="cv-spinner" aria-hidden="true" />{label}</div>;
}

/** Unknown id in the URL: what was not found, and a way back. */
export function NotFoundState({ title, href, linkLabel }: { title: string; href: string; linkLabel: string }) {
  return <EmptyState title={title} action={<Link className="cv-btn cv-btn--secondary" href={href}>{linkLabel}</Link>} />;
}
