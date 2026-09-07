import React from "react";
import { Button } from "../actions/Button.jsx";

/* Ordinary navigation links; the mobile menu is a disclosure (button aria-expanded/aria-controls → nav). Escape closes and restores focus to the toggle. */
export function SiteHeader({ links = [], cta, current, brand = "Convoy", tag = "", href = "#", sticky = true, defaultOpen = false, id = "cv-mobile-menu" }) {
  const [open, setOpen] = React.useState(defaultOpen);
  const toggleRef = React.useRef(null);
  const menuRef = React.useRef(null);
  const close = React.useCallback((restore) => { setOpen(false); if (restore && toggleRef.current) toggleRef.current.focus(); }, []);
  React.useEffect(() => {
    if (!open) return;
    const onKey = (e) => { if (e.key === "Escape") { e.preventDefault(); close(true); } };
    const onDown = (e) => { if (menuRef.current && !menuRef.current.contains(e.target) && toggleRef.current && !toggleRef.current.contains(e.target)) setOpen(false); };
    window.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown);
    return () => { window.removeEventListener("keydown", onKey); document.removeEventListener("pointerdown", onDown); };
  }, [open, close]);
  const isCurrent = (l) => (current === l.href ? "true" : undefined);
  return (
    <header className={`cv-header${sticky ? " cv-header--sticky" : ""}`}>
      <div className="cv-container cv-header__bar">
        <a className="cv-header__brand" href={href}><span>{brand}</span>{tag && <span className="cv-header__tag">{tag}</span>}</a>
        <nav className="cv-header__nav" aria-label="Primary">
          {links.map((l) => <a key={l.href} className="cv-header__link" href={l.href} aria-current={isCurrent(l)}>{l.label}</a>)}
          {cta && <Button className="cv-header__cta" variant="primary" size="sm" href={cta.href}>{cta.label}</Button>}
        </nav>
        <button ref={toggleRef} className="cv-header__toggle" type="button" aria-expanded={open} aria-controls={id} onClick={() => (open ? close(false) : setOpen(true))}>
          <span className="cv-header__burger" aria-hidden="true"><span /><span /><span /></span>{open ? "Close" : "Menu"}
        </button>
      </div>
      <nav id={id} ref={menuRef} className="cv-header__menu" aria-label="Primary" hidden={!open}>
        <ul className="cv-header__menu-list">
          {links.map((l) => <li key={l.href}><a href={l.href} aria-current={isCurrent(l)} onClick={() => close(false)}>{l.label}</a></li>)}
        </ul>
        {cta && <div className="cv-header__menu-cta"><Button block href={cta.href} onClick={() => close(false)}>{cta.label}</Button></div>}
      </nav>
    </header>
  );
}
