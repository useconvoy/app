"use client";

import { useId, useState } from "react";
import type { ReactNode } from "react";
import { Badge } from "../Badges";
import { Icon } from "../Icons";

/** One definition line for an info toggle: a terse term and what it means. */
export interface Definition { term: string; text: string }
export interface Fact { label: string; value: ReactNode; title?: string }

/**
 * A card for one evidence panel: title, the "Measured" provenance badge and an info toggle that shows
 * the panel's definitions as terse lines; then the body and a foot (small facts, a one-line note, the
 * source link). Head, body and foot are the card's three rows: side by side, two cards share them
 * (a subgrid of the row), so they are equal in height and their feet line up.
 */
export function EvidenceCard({ title, provenance, definitions, facts, note, source, compact = false, children }: {
  title: string;
  /** Tooltip for the "Measured" badge: where the values were measured. */
  provenance: string;
  definitions: readonly Definition[];
  facts?: readonly Fact[];
  note?: string;
  source?: ReactNode;
  compact?: boolean;
  children: ReactNode;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  return <section className={`cv-card cv-ev${compact ? " cv-ev--compact" : ""}`} aria-labelledby={`${id}-title`}>
    <div className="cv-card__head cv-ev__head">
      <div className="cv-ev__title"><h2 id={`${id}-title`}>{title}</h2><Badge tone="info" title={provenance}>Measured</Badge></div>
      <button className="cv-icon-btn cv-ev__info" type="button" aria-expanded={open} aria-controls={`${id}-definitions`}
        aria-label={`${title}: definitions`} title="Definitions" onClick={() => setOpen(value => !value)}><Icon name="info" /></button>
    </div>
    <div className="cv-ev__body">
      <dl className="cv-ev__definitions" id={`${id}-definitions`} hidden={!open}>
        {definitions.map(item => <div key={item.term}><dt>{item.term}</dt><dd>{item.text}</dd></div>)}
      </dl>
      {children}
    </div>
    <div className="cv-ev__foot">
      {!!facts?.length && <dl className="cv-ev__facts">{facts.map(item => <div key={item.label} title={item.title}><dt>{item.label}</dt><dd>{item.value}</dd></div>)}</dl>}
      {(note || source) && <div className="cv-ev__line">{note && <p className="cv-ev__note">{note}</p>}{source && <span className="cv-ev__source">{source}</span>}</div>}
    </div>
  </section>;
}
