/** Restrained footer: wordmark, one-line description, in-page links, legal row with year and domain. No badges. */
export interface SiteFooterProps {
  brand?: string;
  /** Default "Deployment infrastructure for physical AI." */
  description?: string;
  links?: { label: string; href: string }[];
  domain?: string;
  /** Link target for the domain; pass "" to render plain text. */
  domainHref?: string;
  year?: number;
}
export declare function SiteFooter(props: SiteFooterProps): JSX.Element;
