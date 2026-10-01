import type { ReactNode } from "react";

/**
 * Page heading: mono eyebrow, H1 with badges, optional lede, right-aligned actions
 * (tertiary → secondary → one primary last), then the mono meta line
 * ("Updated Oct 1, 09:41:20 UTC · Refreshes every 15 seconds", see `fmtUpdated`).
 */
export function PageHeader({ eyebrow, title, badges, actions, meta, lede }: { eyebrow: string; title: ReactNode; badges?: ReactNode; actions?: ReactNode; meta?: ReactNode; lede?: ReactNode }) {
  return <>
    <div className="portal-page-heading cfg-page-head">
      <div>
        <p className="portal-eyebrow">{eyebrow}</p>
        <div className="cfg-title"><h1>{title}</h1>{badges}</div>
        {lede && <p className="cfg-lede">{lede}</p>}
      </div>
      {actions && <div className="cfg-actions">{actions}</div>}
    </div>
    {meta && <p className="portal-updated">{meta}</p>}
  </>;
}
