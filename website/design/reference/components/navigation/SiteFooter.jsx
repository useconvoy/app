import React from "react";

export function SiteFooter({ brand = "Convoy", description = "Deployment infrastructure for physical AI.", links = [], domain = "deployconvoy.com", domainHref = "https://deployconvoy.com", year = 2026 }) {
  return (
    <footer className="cv-footer">
      <div className="cv-container">
        <div className="cv-footer__row">
          <div><div className="cv-footer__brand">{brand}</div><p className="cv-footer__meta">{description}</p></div>
          {links.length > 0 && <nav aria-label="Footer"><ul className="cv-footer__links">{links.map((l) => <li key={l.href}><a href={l.href}>{l.label}</a></li>)}</ul></nav>}
        </div>
        <div className="cv-footer__legal"><span>© {year} {brand}</span>{domainHref ? <a href={domainHref}>{domain}</a> : <span>{domain}</span>}</div>
      </div>
    </footer>
  );
}
