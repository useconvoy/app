"use client";

import type { ReactNode } from "react";
import { Icon, type IconName } from "./Icons";

/** Row of search, filter chips and sort (`cfg-toolbar`); put the sort field last with `end`. */
export function Toolbar({ children }: { children: ReactNode }) { return <div className="cfg-toolbar">{children}</div>; }

export interface ChipOption<T extends string> { id: T; label: string; count?: number | null; icon?: IconName }
/** Toggle chips (`aria-pressed`), one selected; also works as a time-range control in a panel heading. */
export function FilterChips<T extends string>({ label, options, value, onChange }: { label: string; options: ReadonlyArray<ChipOption<T>>; value: T; onChange: (id: T) => void }) {
  return <div className="cfg-chips" role="group" aria-label={label}>
    {options.map(option => <button key={option.id} className="cfg-chip" type="button" aria-pressed={option.id === value} onClick={() => onChange(option.id)}>
      {option.icon && <Icon name={option.icon} />}{option.label}{option.count !== undefined && option.count !== null && <span className="cfg-chip__count">{option.count.toLocaleString("en-US")}</span>}
    </button>)}
  </div>;
}

/** Labelled search input with the search icon. */
export function SearchField({ label, value, onChange, placeholder }: { label: string; value: string; onChange: (value: string) => void; placeholder?: string }) {
  return <label className="cfg-field cfg-search">{label}<span className="cfg-search__box"><Icon name="search" /><input className="field-input cfg-input" type="search" value={value} placeholder={placeholder} onChange={event => onChange(event.target.value)} /></span></label>;
}

/** Labelled select; `end` pushes it to the right edge of a toolbar. */
export function SelectField<T extends string>({ label, value, options, onChange, end = false, hint }: { label: string; value: T; options: ReadonlyArray<{ value: T; label: string; disabled?: boolean }>; onChange: (value: T) => void; end?: boolean; hint?: ReactNode }) {
  return <label className={`cfg-field${end ? " cfg-toolbar__end" : ""}`}>{label}
    <select className="field-input cfg-input" value={value} onChange={event => onChange(event.target.value as T)}>{options.map(option => <option key={option.value} value={option.value} disabled={option.disabled}>{option.label}</option>)}</select>
    {hint && <span className="cfg-field__hint">{hint}</span>}
  </label>;
}
