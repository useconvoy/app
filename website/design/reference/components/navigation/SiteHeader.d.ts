import * as React from "react";

/**
 * Site header: wordmark + tag, inline nav ≥900px, accessible disclosure menu below (aria-expanded/controls, Escape closes).
 * @startingPoint section="Navigation" subtitle="Header with three anchor links, CTA and mobile menu" viewport="700x180"
 */
export interface SiteHeaderProps {
  links?: { label: string; href: string }[];
  /** Primary CTA shown as a small primary button (full-width inside the mobile menu). */
  cta?: { label: string; href: string };
  /** href of the current link; gets aria-current="page". */
  current?: string;
  brand?: string;
  /** Small mono tag after the wordmark; pass "" to hide. Default "Precision Release". */
  tag?: string;
  href?: string;
}
export declare function SiteHeader(props: SiteHeaderProps): JSX.Element;
