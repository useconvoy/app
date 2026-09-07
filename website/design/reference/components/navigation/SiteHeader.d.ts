import * as React from "react";

/**
 * Sticky site header: wordmark, ordinary anchor links + one CTA inline ≥900px; below that a disclosure menu (button with aria-expanded/aria-controls → <nav>). Escape closes and returns focus to the toggle; outside pointer-down closes.
 */
export interface SiteHeaderProps {
  links?: { label: string; href: string }[];
  /** Primary CTA (small inline button; full-width inside the mobile menu). */
  cta?: { label: string; href: string };
  /** href of the current section; that link gets aria-current="true". */
  current?: string;
  brand?: string;
  /** Optional descriptor after the wordmark (e.g. "Physical AI"). Default none; hidden ≤640px. */
  tag?: string;
  href?: string;
  /** position: sticky; top: 0. Default true. Anchor targets clear it via scroll-margin-top in base.css. */
  sticky?: boolean;
  /** Render the mobile menu open initially (specimens only). */
  defaultOpen?: boolean;
  /** id of the mobile nav (aria-controls target). */
  id?: string;
}
export declare function SiteHeader(props: SiteHeaderProps): JSX.Element;
