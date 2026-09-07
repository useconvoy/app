import React from "react";
import { Button } from "../actions/Button.jsx";

export function SiteHeader({ links = [], cta, current, brand = "Convoy", tag = "Precision Release", href = "#" }) {
  const [open, setOpen] = React.useState(false);
  const menuId = "cv-mobile-menu";
  React.useEffect(() => {
    if (!open) return;
    const onKey = (e) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);
  return (
    <header className="cv-header">
      <div className="cv-container cv-header__bar">
        <a className="cv-header__brand" href={href}><span>{brand}</span>{tag && <span className="cv-header__tag">{tag}</span>}</a>
        <nav className="cv-header__nav" aria-label="Primary">
          {links.map((l) => <a key={l.href} className="cv-header__link" href={l.href} aria-current={current === l.href ? "page" : undefined}>{l.label}</a>)}
          {cta && <Button className="cv-header__cta" variant="primary" size="sm" href={cta.href}>{cta.label}</Button>}
        </nav>
        <button className="cv-header__toggle" type="button" aria-expanded={open} aria-controls={menuId} onClick={() => setOpen(!open)}>
          <span className="cv-header__burger" aria-hidden="true"><span /><span /><span /></span>{open ? "Close" : "Menu"}
        </button>
      </div>
      <nav id={menuId} className="cv-header__menu" aria-label="Primary, mobile" hidden={!open}>
        <ul className="cv-header__menu-list">
          {links.map((l) => <li key={l.href}><a href={l.href} onClick={() => setOpen(false)}>{l.label}</a></li>)}
        </ul>
        {cta && <div className="cv-header__menu-cta"><Button block href={cta.href} onClick={() => setOpen(false)}>{cta.label}</Button></div>}
      </nav>
    </header>
  );
}
