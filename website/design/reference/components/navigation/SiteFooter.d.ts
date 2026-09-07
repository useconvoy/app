/** Restrained page footer: wordmark, one-line description, in-page links, mono legal row with the domain. */
export interface SiteFooterProps {
  brand?: string;
  description?: string;
  links?: { label: string; href: string }[];
  /** Shown verbatim in the legal row. Default "deployconvoy.com". */
  domain?: string;
  year?: number;
}
export declare function SiteFooter(props: SiteFooterProps): JSX.Element;
