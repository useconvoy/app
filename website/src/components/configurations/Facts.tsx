import type { ReactNode } from "react";

export interface FactItem { label: ReactNode; value: ReactNode; detail?: ReactNode }

/** `cfg-section` with the kit's section label (h2 + note). */
export function Section({ title, note, children, id }: { title: ReactNode; note?: ReactNode; children: ReactNode; id?: string }) {
  return <section className="cfg-section" aria-labelledby={id}>
    <div className="portal-section-label"><h2 id={id}>{title}</h2>{note && <span>{note}</span>}</div>
    {children}
  </section>;
}

/** `portal-panel` with eyebrow, title and an optional heading action. */
export function Panel({ eyebrow, title, action, children, titleId, className = "" }: { eyebrow?: ReactNode; title: ReactNode; action?: ReactNode; children: ReactNode; titleId?: string; className?: string }) {
  return <section className={`portal-panel${className ? ` ${className}` : ""}`} aria-labelledby={titleId}>
    <div className="portal-panel-heading"><div>{eyebrow && <p className="portal-eyebrow">{eyebrow}</p>}<h2 id={titleId}>{title}</h2></div>{action}</div>
    {children}
  </section>;
}

/** One `portal-fact` (dt label, mono dd value, optional detail line). */
export function Fact({ label, value, detail }: FactItem) {
  return <div className="portal-fact"><dt>{label}</dt><dd>{value}{detail && <span className="portal-fact-detail">{detail}</span>}</dd></div>;
}
export function FactList({ facts, className = "" }: { facts: readonly FactItem[]; className?: string }) {
  return <dl className={`portal-facts${className ? ` ${className}` : ""}`}>{facts.map((fact, i) => <Fact key={i} {...fact} />)}</dl>;
}
/** "Model metadata"-style panel: eyebrow, title, Edit action and fact rows. */
export function FactPanel({ eyebrow, title, action, facts, titleId, children }: { eyebrow?: ReactNode; title: ReactNode; action?: ReactNode; facts: readonly FactItem[]; titleId?: string; children?: ReactNode }) {
  return <Panel eyebrow={eyebrow} title={title} action={action} titleId={titleId}><FactList facts={facts} />{children}</Panel>;
}
/** Three facts in a strip (`portal-health-strip`): device contact, observed health, latest telemetry. */
export function HealthStrip({ facts }: { facts: readonly FactItem[] }) {
  return <dl className="portal-health-strip">{facts.map((fact, i) => <Fact key={i} {...fact} />)}</dl>;
}

/** The teal device board from the device view: drawing, eyebrow, model, repo line, inline facts and one action. */
export function DeviceBoard({ eyebrow, title, repo, facts, action, drawingLabel = "Edge device", titleId }: { eyebrow: ReactNode; title: ReactNode; repo?: ReactNode; facts?: ReactNode; action?: ReactNode; drawingLabel?: string; titleId?: string }) {
  return <section className="portal-device-board" aria-labelledby={titleId}>
    <div className="portal-device-drawing" aria-hidden="true"><svg viewBox="0 0 180 138"><rect x="30" y="25" width="120" height="88" rx="2" /><rect x="62" y="45" width="56" height="48" rx="2" /><path d="M75 55h30v28H75zM18 43h12m-12 13h12m-12 13h12m-12 13h12m-12 13h12m120-52h12m-12 13h12m-12 13h12m-12 13h12m-12 13h12M49 13v12m15-12v12m15-12v12m15-12v12m15-12v12m15-12v12M49 113v12m15-12v12m15-12v12m15-12v12m15-12v12m15-12v12" /><circle cx="42" cy="37" r="3" /><circle cx="138" cy="101" r="3" /></svg><span>{drawingLabel}</span></div>
    <div className="portal-device-model"><p className="portal-eyebrow">{eyebrow}</p><h2 id={titleId}>{title}</h2>{repo && <p className="portal-model-repo">{repo}</p>}{facts && <div className="portal-inline-facts">{facts}</div>}</div>
    {action}
  </section>;
}
