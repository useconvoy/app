import React from "react";

export function SiteFooter({ brand = "Convoy", description = "Deployment infrastructure for physical AI. In development.", links = [], domain = "deployconvoy.com", year = new Date().getFullYear() }) {
  return (
    <footer className="cv-footer">
      <div className="cv-container">
        <div className="cv-footer__row">
          <div><div className="cv-footer__brand">{brand}</div><p className="cv-footer__meta">{description}</p></div>
          {links.length > 0 && <ul className="cv-footer__links">{links.map((l) => <li key={l.href}><a href={l.href}>{l.label}</a></li>)}</ul>}
        </div>
        <div className="cv-footer__legal"><span>© {year} {brand}</span><span>{domain}</span></div>
      </div>
    </footer>
  );
}
