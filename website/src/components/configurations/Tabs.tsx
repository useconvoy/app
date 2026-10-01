"use client";

import Link from "next/link";
import { useId, useRef } from "react";
import type { KeyboardEvent, ReactNode } from "react";
import { useQueryState } from "./hooks";

export interface TabItem { id: string; label: string; count?: number | string | null }

/**
 * Button tabs (`role="tablist"`) with arrow / Home / End keys and automatic
 * activation. Controlled: pair with `useQueryTab` to keep the tab in the URL.
 * A click is a new history entry; moving with the keys replaces it, so Back does
 * not step through every tab passed on the way. Panels: wrap each in <TabPanel>
 * with the same `idPrefix` and tab id.
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
  return <div ref={list} className="application-nav cfg-tabs" role="tablist" aria-label={label}>
    {tabs.map((tab, index) => {
      const selected = tab.id === value;
      return <button key={tab.id} id={`${prefix}-tab-${tab.id}`} type="button" role="tab" aria-selected={selected} aria-controls={`${prefix}-panel-${tab.id}`} tabIndex={selected ? 0 : -1}
        onClick={() => onChange(tab.id)} onKeyDown={event => onKeyDown(event, index)}>
        {tab.label}{tab.count !== undefined && tab.count !== null && tab.count !== "" && <span className="cfg-tabs__count">{typeof tab.count === "number" ? tab.count.toLocaleString("en-US") : tab.count}</span>}
      </button>;
    })}
  </div>;
}

/** The panel for one tab; hidden panels stay mounted so their state survives a switch. */
export function TabPanel({ idPrefix, tabId, selected, children }: { idPrefix: string; tabId: string; selected: boolean; children: ReactNode }) {
  return <div id={`${idPrefix}-panel-${tabId}`} role="tabpanel" aria-labelledby={`${idPrefix}-tab-${tabId}`} hidden={!selected}>{children}</div>;
}

/** The selected tab from `?{param}=`, falling back to the first tab for a missing or unknown value. */
export function useQueryTab(tabs: readonly TabItem[], param = "tab"): [string, (id: string, options?: { replace?: boolean }) => void] {
  const [value, setValue] = useQueryState(param);
  const selected = tabs.some(tab => tab.id === value) ? value as string : tabs[0]?.id ?? "";
  return [selected, (id: string, options?: { replace?: boolean }) => setValue(id === tabs[0]?.id ? null : id, options)];
}

/** Link tabs (`nav.application-nav`), e.g. sections that are separate URLs. */
export function LinkTabs({ items, label }: { items: ReadonlyArray<{ label: string; href: string; current: boolean; count?: number | string | null }>; label: string }) {
  return <nav className="application-nav cfg-tabs" aria-label={label}>
    {items.map(item => <Link key={item.href} href={item.href} aria-current={item.current ? "page" : undefined}>{item.label}{item.count !== undefined && item.count !== null && <span className="cfg-tabs__count">{item.count}</span>}</Link>)}
  </nav>;
}
