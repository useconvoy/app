"use client";

/**
 * The select, drawn entirely by the design system.
 *
 * The trigger is a button wearing the shared CONTROL skin; the open panel
 * is our own listbox instead of the operating system's menu, so it renders
 * in the site's own paper and ink everywhere. Long lists get a search box
 * at the top of the panel automatically. A hidden input carries the value,
 * so the component drops into plain server-action forms exactly where a
 * native select stood.
 *
 * Dismissal follows the modal rules: Escape closes and returns focus to
 * the trigger, and so does a click anywhere outside the control.
 */
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";

import { CONTROL } from "./field";

export interface SelectOption {
  value: string;
  label: string;
}

export interface SelectProps {
  options: SelectOption[];
  name?: string;
  id?: string;
  /** Uncontrolled initial value; the first option is selected when absent. */
  defaultValue?: string;
  /** Controlled value; pair with onValueChange. */
  value?: string;
  onValueChange?: (value: string) => void;
  className?: string;
  "aria-label"?: string;
  /** Option count at which the panel grows its search box. */
  searchThreshold?: number;
  disabled?: boolean;
}

export function Select({
  options,
  name,
  id,
  defaultValue,
  value,
  onValueChange,
  className,
  searchThreshold = 8,
  disabled,
  ...aria
}: SelectProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [internal, setInternal] = useState(defaultValue ?? options[0]?.value ?? "");
  const [highlighted, setHighlighted] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  const selectedValue = value ?? internal;
  const selected = options.find((option) => option.value === selectedValue);
  const searchable = options.length >= searchThreshold;

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return options;
    return options.filter((option) => option.label.toLowerCase().includes(needle));
  }, [options, query]);

  function close(refocus = true) {
    setOpen(false);
    setQuery("");
    if (refocus) triggerRef.current?.focus();
  }

  function choose(option: SelectOption) {
    if (value === undefined) setInternal(option.value);
    onValueChange?.(option.value);
    close();
  }

  function openPanel() {
    if (disabled) return;
    const index = visible.findIndex((option) => option.value === selectedValue);
    setHighlighted(index >= 0 ? index : 0);
    setOpen(true);
  }

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        close(false);
      }
    }
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open]);

  useEffect(() => {
    if (open && searchable) searchRef.current?.focus();
  }, [open, searchable]);

  useEffect(() => {
    if (!open) return;
    const row = listRef.current?.querySelector(`[data-index="${highlighted}"]`);
    row?.scrollIntoView?.({ block: "nearest" });
  }, [open, highlighted]);

  function onKeyDown(event: ReactKeyboardEvent) {
    if (event.key === "Escape" && open) {
      event.preventDefault();
      close();
      return;
    }
    if (!open) {
      if (["ArrowDown", "ArrowUp", "Enter", " "].includes(event.key)) {
        event.preventDefault();
        openPanel();
      }
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setHighlighted((current) => Math.min(current + 1, visible.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setHighlighted((current) => Math.max(current - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      const option = visible[highlighted];
      if (option) choose(option);
    } else if (event.key === "Tab") {
      close(false);
    }
  }

  return (
    <div ref={rootRef} className="relative min-w-0" onKeyDown={onKeyDown}>
      {name ? <input type="hidden" name={name} value={selectedValue} /> : null}
      <button
        ref={triggerRef}
        type="button"
        id={id}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={aria["aria-label"]}
        onClick={() => (open ? close() : openPanel())}
        className={[CONTROL, "flex items-center justify-between gap-2 text-left", className ?? ""].join(" ")}
      >
        <span className="truncate">{selected?.label ?? ""}</span>
        <svg
          aria-hidden="true"
          width="10"
          height="6"
          viewBox="0 0 10 6"
          fill="none"
          className={["shrink-0 transition-transform", open ? "rotate-180" : ""].join(" ")}
        >
          <path
            d="M1 1l4 4 4-4"
            className="stroke-muted"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>
      {open && (
        <div className="absolute z-50 mt-1 w-full min-w-44 rounded-md border border-line bg-card shadow-lg">
          {searchable && (
            <div className="border-b border-line-soft p-1.5">
              <input
                ref={searchRef}
                type="search"
                value={query}
                onChange={(event) => {
                  setQuery(event.target.value);
                  setHighlighted(0);
                }}
                placeholder="Search"
                aria-label="Search options"
                className="w-full rounded-sm border border-line bg-card px-2 py-1 text-sm text-ink placeholder:text-muted/70 focus:outline-2 focus:outline-offset-[-1px] focus:outline-pass"
              />
            </div>
          )}
          <ul
            ref={listRef}
            role="listbox"
            aria-label={aria["aria-label"]}
            className="m-0 max-h-64 list-none overflow-y-auto p-1"
          >
            {visible.length === 0 ? (
              <li className="px-2.5 py-1.5 text-sm text-muted">No matches</li>
            ) : (
              visible.map((option, index) => (
                <li
                  key={option.value}
                  role="option"
                  aria-selected={option.value === selectedValue}
                  data-index={index}
                  onMouseEnter={() => setHighlighted(index)}
                  onMouseDown={(event) => {
                    // mousedown, not click: selection must win against the
                    // outside-click closer and any form blur handlers.
                    event.preventDefault();
                    choose(option);
                  }}
                  className={[
                    "flex cursor-pointer items-center justify-between gap-2 rounded-sm px-2.5 py-1.5 text-sm",
                    index === highlighted ? "bg-field text-ink" : "text-ink",
                    option.value === selectedValue ? "font-medium" : "",
                  ].join(" ")}
                >
                  <span className="truncate">{option.label}</span>
                  {option.value === selectedValue && (
                    <svg aria-hidden="true" width="11" height="9" viewBox="0 0 11 9" fill="none" className="shrink-0">
                      <path
                        d="M1.5 4.6 4.2 7.2 9.5 1.6"
                        className="stroke-pine"
                        strokeWidth="1.8"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  )}
                </li>
              ))
            )}
          </ul>
        </div>
      )}
    </div>
  );
}
