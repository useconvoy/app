
// components/actions/Button.jsx


const Arrow = () => (
  <svg className="cv-btn__arrow" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M3 8h9.5M9 3.5 13.5 8 9 12.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
);

function Button({ variant = "primary", size = "md", block = false, arrow = false, href, disabled = false, type = "button", className = "", children, ...rest }) {
  const cls = ["cv-btn", `cv-btn--${variant}`, size === "sm" ? "cv-btn--sm" : "", block ? "cv-btn--block" : "", className].filter(Boolean).join(" ");
  if (href && !disabled) {
    return <a className={cls} href={href} {...rest}>{children}{arrow && <Arrow />}</a>;
  }
  return <button className={cls} type={type} disabled={disabled} {...rest}>{children}{arrow && <Arrow />}</button>;
}

// components/forms/TextField.jsx


let idCounter = 0;
function useFieldId(id) {
  const ref = React.useRef(id || `cv-f-${++idCounter}`);
  return ref.current;
}

function Field({ id, label, optional = false, hint, error, children }) {
  return (
    <div className="cv-field">
      <label className="cv-field__label" htmlFor={id}>{label}{optional && <span className="cv-field__optional">Optional</span>}</label>
      {children}
      {hint && !error && <div className="cv-field__hint" id={`${id}-hint`}>{hint}</div>}
      {error && <div className="cv-field__error" id={`${id}-err`} role="alert">{error}</div>}
    </div>
  );
}

function TextField({ id, label, optional, hint, error, className = "", ...rest }) {
  const fid = useFieldId(id);
  const describedBy = error ? `${fid}-err` : hint ? `${fid}-hint` : undefined;
  return (
    <Field id={fid} label={label} optional={optional} hint={hint} error={error}>
      <input id={fid} className={`cv-input ${className}`.trim()} aria-invalid={error ? "true" : undefined} aria-describedby={describedBy} required={!optional} {...rest} />
    </Field>
  );
}

// components/forms/TextArea.jsx



function TextArea({ id, label, optional, hint, error, rows = 5, className = "", ...rest }) {
  const fid = useFieldId(id);
  const describedBy = error ? `${fid}-err` : hint ? `${fid}-hint` : undefined;
  return (
    <Field id={fid} label={label} optional={optional} hint={hint} error={error}>
      <textarea id={fid} rows={rows} className={`cv-textarea ${className}`.trim()} aria-invalid={error ? "true" : undefined} aria-describedby={describedBy} required={!optional} {...rest} />
    </Field>
  );
}

// components/forms/Checkbox.jsx


function Checkbox({ label, className = "", ...rest }) {
  return (
    <label className={`cv-check ${className}`.trim()}>
      <input type="checkbox" {...rest} />
      <span>{label}</span>
    </label>
  );
}

// components/navigation/SiteHeader.jsx



function SiteHeader({ links = [], cta, current, brand = "Convoy", tag = "Precision Release", href = "#" }) {
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

// components/navigation/SiteFooter.jsx


function SiteFooter({ brand = "Convoy", description = "Deployment infrastructure for physical AI. In development.", links = [], domain = "deployconvoy.com", year = new Date().getFullYear() }) {
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

// components/content/StatusBadge.jsx


function StatusBadge({ tone = "neutral", dot = true, children, className = "" }) {
  return <span className={`cv-badge cv-badge--${tone} ${className}`.trim()}>{dot && <span className="cv-badge__dot" aria-hidden="true" />}{children}</span>;
}

// components/content/SectionHeading.jsx


function SectionHeading({ eyebrow, title, lead, as = "h2", id, className = "" }) {
  const Tag = as;
  return (
    <div className={`cv-sh ${className}`.trim()}>
      {eyebrow && <p className="cv-eyebrow">{eyebrow}</p>}
      <Tag id={id} className={as === "h1" ? "cv-h1" : as === "h3" ? "cv-h3" : "cv-h2"}>{title}</Tag>
      {lead && <p className="cv-sh__lead">{lead}</p>}
    </div>
  );
}

// components/content/DependencyRows.jsx


/** Aligned rows: term | one or more description cells. Rows may use `body` (single column) or `model`/`deployment` (two columns). */
function DependencyRows({ columns, rows = [] }) {
  const twoCol = rows.some((r) => r.deployment !== undefined);
  const cols = columns || (twoCol ? ["", "What the model gives you", "What a robot deployment needs"] : ["", ""]);
  const showHead = cols.slice(1).some(Boolean);
  const cls = twoCol ? "cv-dep cv-dep--2" : "cv-dep cv-dep--1";
  return (
    <div className={cls} role="table">
      {showHead && <div className="cv-dep__head" role="row">{cols.map((c, i) => <div key={i} role="columnheader">{c}</div>)}</div>}
      {rows.map((r, i) => (
        <div className="cv-dep__row" role="row" key={r.term}>
          <div className="cv-dep__term" role="rowheader"><span className="cv-dep__idx">{String(i + 1).padStart(2, "0")}</span>{r.term}</div>
          {twoCol ? (
            <>
              <div className="cv-dep__cell" role="cell">{cols[1] && <span className="cv-dep__cell-label">{cols[1]}</span>}{r.model}</div>
              <div className="cv-dep__cell" role="cell">{cols[2] && <span className="cv-dep__cell-label">{cols[2]}</span>}{r.deployment}</div>
            </>
          ) : (
            <div className="cv-dep__cell" role="cell">{r.body}</div>
          )}
        </div>
      ))}
    </div>
  );
}

// components/content/WorkflowSteps.jsx


function WorkflowSteps({ steps = [] }) {
  return (
    <ol className="cv-steps" style={{ listStyle: "none", margin: 0, padding: 0 }}>
      {steps.map((s, i) => (
        <li className="cv-step" key={s.title}>
          <span className="cv-step__num">{String(i + 1).padStart(2, "0")}{s.tag ? ` · ${s.tag}` : ""}</span>
          <h3 className="cv-step__title">{s.title}</h3>
          <p className="cv-step__body">{s.body}</p>
          {s.items && <ul className="cv-step__list">{s.items.map((it) => <li key={it}>{it}</li>)}</ul>}
        </li>
      ))}
    </ol>
  );
}

// components/content/Disclosure.jsx


function Disclosure({ summary, children, open = false, name }) {
  return (
    <details className="cv-disc" open={open || undefined} name={name}>
      <summary className="cv-disc__summary"><span>{summary}</span><span className="cv-disc__icon" aria-hidden="true" /></summary>
      <div className="cv-disc__body">{children}</div>
    </details>
  );
}

// components/content/Notice.jsx


function Notice({ tone = "neutral", children, role }) {
  return <div className={`cv-notice cv-notice--${tone}`} role={role || (tone === "error" ? "alert" : "status")}><span className="cv-notice__dot" aria-hidden="true" />{children}</div>;
}

// components/diagrams/DiagramArrow.jsx


/** Diagram connector. kind: execution (solid) | release (dashed) | optional (dotted). */
function DiagramArrow({ kind = "execution", label }) {
  const dash = kind === "release" ? "6 4" : kind === "optional" ? "2 4" : undefined;
  return (
    <div className={`cv-path__arrow cv-path__arrow--${kind}`} aria-hidden="true" title={label}>
      <svg viewBox="0 0 40 16" fill="none"><path d="M1 8h32" stroke="currentColor" strokeWidth="1.5" strokeDasharray={dash} strokeLinecap="round"/><path d="M29 3.5 34.5 8 29 12.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
    </div>
  );
}

function DiagramLegend({ items = ["execution", "release", "optional"] }) {
  const map = { execution: ["solid", "Execution"], release: ["dashed", "Release · configuration · evidence"], optional: ["dotted", "Optional, configuration-dependent placement"] };
  return <div className="cv-dg__legend">{items.map((k) => <span key={k}><i className={map[k][0]} />{map[k][1]}</span>)}</div>;
}

// components/diagrams/ReleaseEnvelope.jsx



const DEFAULT_GROUPS = [
  { key: "model", title: "Model assets", note: "what was trained", items: ["policy weights", "preprocessing spec", "action space"] },
  { key: "processing", title: "Input and action processing", note: "how signals are shaped", items: ["sensor sync", "normalisation", "command limits"] },
  { key: "runtime", title: "Runtime", note: "what executes it", items: ["inference loop", "timing budget", "health checks"] },
  { key: "target", title: "Target configuration", note: "where it runs", items: ["robot config", "compute placement", "controller interface"] },
  { key: "evidence", title: "Qualification evidence", note: "why it may be released", items: ["test runs", "checks passed", "sign-off"], evidence: true, wide: true },
];

function ReleaseEnvelope({ version = "release v0.3", label = "example", groups = DEFAULT_GROUPS, legend = true, caption = "Labels are conceptual examples. Robot controller and safety remain outside Convoy ownership." }) {
  return (
    <figure className="cv-dg" style={{ margin: 0 }}>
      <div className="cv-env">
        <div className="cv-env__tag"><span>{version}</span>{label && <em>· {label}</em>}</div>
        <div className="cv-env__grid">
          {groups.map((g) => (
            <div key={g.key || g.title} className={["cv-env__group", g.wide ? "cv-env__group--wide" : "", g.evidence ? "cv-env__group--evidence" : ""].filter(Boolean).join(" ")}>
              <h4>{g.title}{g.note && <small>{g.note}</small>}</h4>
              {g.items && <div className="cv-env__items">{g.items.map((it) => <span key={it} className={`cv-env__item${g.evidence ? " cv-env__item--evidence" : ""}`}>{it}</span>)}</div>}
            </div>
          ))}
        </div>
      </div>
      <figcaption className="cv-env__foot">{caption && <span className="cv-dg__caption">{caption}</span>}{legend && <DiagramLegend items={["release"]} />}</figcaption>
    </figure>
  );
}

// components/diagrams/ExecutionPath.jsx



const DEFAULT_NODES = [
  { title: "Sensors", sub: "cameras · joints · force", site: "robot", external: true },
  { title: "Input processing", sub: "sync · normalise", site: "local · site · cloud", optional: true },
  { title: "Model", sub: "learned policy", site: "local · site · cloud", optional: true, model: true },
  { title: "Action processing", sub: "limits · rates", site: "local", optional: false },
  { title: "Robot controller", sub: "outside Convoy", site: "robot · safety", external: true },
];

function ExecutionPath({ nodes = DEFAULT_NODES, scope = "Convoy runtime scope · placement is configuration-dependent", legend = true, traceable = false, caption = "Conceptual execution path. Placement of processing and model differs per configuration; repeatability of the release, not identical physical behaviour, is the goal." }) {
  const [active, setActive] = React.useState(-1);
  const timer = React.useRef();
  const reduced = typeof window !== "undefined" && window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const trace = () => {
    clearTimeout(timer.current);
    if (reduced) { setActive(active === nodes.length ? -1 : nodes.length); return; }
    let i = 0;
    const step = () => { setActive(i); if (i < nodes.length) { i += 1; timer.current = setTimeout(step, 520); } };
    step();
  };
  React.useEffect(() => () => clearTimeout(timer.current), []);
  const lit = (i) => active === nodes.length || i <= active;
  return (
    <figure className="cv-dg" style={{ margin: 0 }}>
      <div className="cv-path">
        {nodes.map((n, i) => (
          <React.Fragment key={n.title}>
            <div className="cv-path__node">
              <div className={["cv-dg__node", n.external ? "cv-dg__node--external" : "", n.model ? "cv-dg__node--model" : ""].filter(Boolean).join(" ")} style={active >= 0 ? { borderColor: lit(i) ? "var(--diagram-release)" : undefined, boxShadow: lit(i) ? "0 0 0 2px var(--color-accent-tint)" : undefined, transition: "border-color var(--dur-slow) var(--ease-diagram), box-shadow var(--dur-slow) var(--ease-diagram)" } : undefined}>
                {n.title}{n.sub && <small>{n.sub}</small>}
              </div>
              <div className={`cv-path__site${n.optional ? " cv-path__site--optional" : ""}`}>{n.site}</div>
            </div>
            {i < nodes.length - 1 && <DiagramArrow kind="execution" />}
          </React.Fragment>
        ))}
      </div>
      {scope && (
        <div className="cv-path__scope" aria-hidden="true">
          <div className="cv-path__scope-bar" style={{ left: "calc(20% + 20px)", right: "calc(20% + 20px)" }}><span className="cv-path__scope-label">{scope}</span></div>
        </div>
      )}
      <figcaption style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-12) var(--space-24)", justifyContent: "space-between", alignItems: "center", marginTop: "var(--space-16)" }}>
        {caption && <span className="cv-dg__caption" style={{ flex: "1 1 320px" }}>{caption}</span>}
        {legend && <DiagramLegend items={["execution", "release", "optional"]} />}
        {traceable && <button type="button" className="cv-btn cv-btn--secondary cv-btn--sm" onClick={trace} aria-pressed={active >= 0}>{active >= 0 ? "Trace again" : "Trace execution"}</button>}
      </figcaption>
    </figure>
  );
}

// components/diagrams/DiagnosticPanel.jsx


const DEFAULT_ROWS = [
  { group: "envelope" },
  { k: "release", v: "v0.3 · example", t: "s" },
  { k: "target", v: "arm-a / site-1 (conceptual)", t: "s" },
  { group: "model assets" },
  { k: "policy", v: "manipulation-policy · one artifact", t: "n" },
  { k: "action_space", v: "joint velocity · 7 dof", t: "n" },
  { group: "processing" },
  { k: "inputs", v: "wrist camera 30 Hz · joint state 100 Hz", t: "n" },
  { k: "limits", v: "velocity · acceleration · workspace", t: "m" },
  { group: "qualification evidence" },
  { k: "runs", v: "3 recorded · checks pass", t: "s" },
  { k: "sign_off", v: "required before release", t: "m" },
];

function DiagnosticPanel({ title = "Release contract · conceptual", status = "not an API", rows = DEFAULT_ROWS, footer = "Field names are illustrative. They show what a release must carry, not a schema or command." }) {
  return (
    <div className="cv-diag">
      <div className="cv-diag__head"><span>{title}</span><span>{status}</span></div>
      <dl className="cv-diag__body" style={{ margin: 0 }}>
        {rows.map((r, i) => r.group ? <div key={i} className="cv-diag__group">{r.group}</div> : (
          <React.Fragment key={i}><dt className="cv-diag__k">{r.k}</dt><dd className={`cv-diag__v ${r.t || ""}`.trim()} style={{ margin: 0 }}>{r.v}</dd></React.Fragment>
        ))}
      </dl>
      {footer && <div className="cv-diag__foot">{footer}</div>}
    </div>
  );
}

window.ConvoyDesignSystem_29acff = { Button, Field, useFieldId, TextField, TextArea, Checkbox, SiteHeader, SiteFooter, StatusBadge, SectionHeading, DependencyRows, WorkflowSteps, Disclosure, Notice, DiagramArrow, DiagramLegend, ReleaseEnvelope, ExecutionPath, DiagnosticPanel };