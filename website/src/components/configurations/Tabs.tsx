"use client";

import { useId, useRef } from "react";
import type { KeyboardEvent, ReactNode } from "react";
import { useQueryState } from "./hooks";

export interface TabItem { id: string; label: string; count?: number | null }

/**
 * Button tabs (`role="tablist"`) with arrow / Home / End keys and automatic
 * activation. Controlled: pair with `useQueryTab` to keep the tab in the URL.
 * A click is a new history entry; moving with the keys replaces it. Panels: wrap
 * each in <TabPanel> with the same `idPrefix` and tab id.
 */
export function Tabs({ tabs, value, onChange, label, idPrefix }: { tabs: readonly TabItem[]; value: string; onChange: (id: string, options?: { replace?: boolean }) => void; label: string; idPrefix?: string }) {
  const fallback = useId();
  const prefix = idPrefix ?? fallback;
  const list = useRef<HTMLDivElement>(null);
  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const last = tabs.length - 1;
    const next = event.key === "ArrowRight" ? (index === last ? 0 : index + 1) : event.key === "ArrowLeft" ? (index === 0 ? last : index - 1) : event.key === "Home" ? 0 : event.key === "End" ? last : null;
    if (next === null) return;
    event.preventDefault();
    onChange(tabs[next].id, { replace: true });
    list.current?.querySelectorAll<HTMLButtonElement>("[role=tab]")[next]?.focus();
  }
  return <div ref={list} className="cv-tabs" role="tablist" aria-label={label}>
    {tabs.map((tab, index) => {
      const selected = tab.id === value;
      return <button key={tab.id} id={`${prefix}-tab-${tab.id}`} type="button" role="tab" aria-selected={selected} aria-controls={`${prefix}-panel-${tab.id}`} tabIndex={selected ? 0 : -1}
        onClick={() => onChange(tab.id)} onKeyDown={event => onKeyDown(event, index)}>
        {tab.label}{tab.count !== undefined && tab.count !== null && <span className="cv-tabs__count">{tab.count.toLocaleString("en-US")}</span>}
      </button>;
    })}
  </div>;
}

/** The panel for one tab; hidden panels stay mounted so their state survives a switch. */
export function TabPanel({ idPrefix, tabId, selected, children }: { idPrefix: string; tabId: string; selected: boolean; children: ReactNode }) {
  return <div className="cv-tabpanel" id={`${idPrefix}-panel-${tabId}`} role="tabpanel" aria-labelledby={`${idPrefix}-tab-${tabId}`} hidden={!selected}>{children}</div>;
}

/**
 * The selected tab from `?{param}=`; a missing or unknown value selects `fallback`
 * (default: the first tab), which is also kept out of the URL.
 */
export function useQueryTab(tabs: readonly TabItem[], options: { param?: string; fallback?: string | null } = {}): [string, (id: string, options?: { replace?: boolean }) => void] {
  const param = options.param ?? "tab";
  const [value, setValue] = useQueryState(param);
  const fallback = tabs.some(tab => tab.id === options.fallback) ? options.fallback as string : tabs[0]?.id ?? "";
  const selected = tabs.some(tab => tab.id === value) ? value as string : fallback;
  return [selected, (id: string, change?: { replace?: boolean }) => setValue(id === fallback ? null : id, change)];
}
