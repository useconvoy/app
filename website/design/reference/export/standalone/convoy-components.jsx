
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

// components/actions/ActionGroup.jsx


/** Group of CTA actions. Inline on desktop, stacked full-width ≤640px. Buttons for actions, links for destinations. */
function ActionGroup({ children, stack = false, note, className = "" }) {
  return (
    <div className={`cv-actions${stack ? " cv-actions--stack" : ""} ${className}`.trim()}>
      {children}
      {note && <p className="cv-actions__note">{note}</p>}
    </div>
  );
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



/* Ordinary navigation links; the mobile menu is a disclosure (button aria-expanded/aria-controls → nav). Escape closes and restores focus to the toggle. */
function SiteHeader({ links = [], cta, current, brand = "Convoy", tag = "", href = "#", sticky = true, defaultOpen = false, id = "cv-mobile-menu" }) {
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

// components/navigation/SiteFooter.jsx


function SiteFooter({ brand = "Convoy", description = "Deployment infrastructure for physical AI.", links = [], domain = "deployconvoy.com", domainHref = "https://deployconvoy.com", year = 2026 }) {
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

// components/navigation/SkipLink.jsx


/** Visually hidden until focused; first focusable element on the page. */
function SkipLink({ href = "#main", children = "Skip to content" }) {
  return <a className="cv-skip" href={href}>{children}</a>;
}

// components/navigation/PageShell.jsx


/** Page shell: skip link target wiring, sticky header slot, main, footer. */
function PageShell({ header, footer, children, mainId = "main" }) {
  return (
    <div className="cv-page">
      {header}
      <main id={mainId} tabIndex={-1} style={{ outline: "none" }}>{children}</main>
      {footer}
    </div>
  );
}

/** Section container: max-width, page padding, vertical section gap, optional top rule, optional 5/7 grid. */
function Section({ id, labelledBy, rule = true, grid, className = "", children, as = "section" }) {
  const Tag = as;
  const cls = ["cv-section", rule ? "cv-section--rule" : "", className].filter(Boolean).join(" ");
  const inner = grid ? <div className={`cv-section__grid${grid === "wide-left" ? " cv-section__grid--wide-left" : grid === "even" ? " cv-section__grid--even" : ""}`}>{children}</div> : children;
  return <Tag id={id} className={cls} aria-labelledby={labelledBy}><div className="cv-container">{inner}</div></Tag>;
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

// components/content/Disclosure.jsx


/** Native details/summary. Multiple may stay open (omit `name`). Enter/Space toggle, expanded state exposed by the element itself. */
function Disclosure({ summary, children, open = false, name, compact = false }) {
  return (
    <details className={`cv-disc${compact ? " cv-disc--compact" : ""}`} open={open || undefined} name={name}>
      <summary className="cv-disc__summary"><span>{summary}</span><span className="cv-disc__icon" aria-hidden="true" /></summary>
      <div className="cv-disc__body">{children}</div>
    </details>
  );
}

// components/content/WorkflowSteps.jsx



/** Numbered workflow sequence (Package / Qualify / Release). Three columns ≥900px, ordered stack below. Optional per-step disclosure for secondary detail. */
function WorkflowSteps({ steps = [] }) {
  return (
    <ol className="cv-steps">
      {steps.map((s, i) => (
        <li className="cv-step" key={s.title}>
          <span className="cv-step__num">{String(i + 1).padStart(2, "0")}{s.tag ? ` · ${s.tag}` : ""}</span>
          <h3 className="cv-step__title">{s.title}</h3>
          <p className="cv-step__body">{s.body}</p>
          {s.items && <ul className="cv-step__list">{s.items.map((it) => <li key={it}>{it}</li>)}</ul>}
          {s.detail && <Disclosure summary={s.detail.summary} compact>{s.detail.body}</Disclosure>}
        </li>
      ))}
    </ol>
  );
}
const WorkflowSequence = WorkflowSteps;

// components/content/Notice.jsx


function Notice({ tone = "neutral", children, role }) {
  return <div className={`cv-notice cv-notice--${tone}`} role={role || (tone === "error" ? "alert" : "status")}><span className="cv-notice__dot" aria-hidden="true" />{children}</div>;
}

// components/content/PartnershipPanel.jsx



/** Design-partnership panel: heading + lead + CTA on the left, fit/not-yet list on the right; single column ≤900px. */
function PartnershipPanel({ eyebrow = "Design partnership", title, lead, id, actions, fit = [] }) {
  return (
    <div className="cv-panel cv-panel--grid">
      <div>
        <SectionHeading id={id} eyebrow={eyebrow} title={title} lead={lead} />
        {actions && <div className="cv-panel__actions">{actions}</div>}
      </div>
      {fit.length > 0 && (
        <ul className="cv-fit">{fit.map((f) => <li key={f.body}><span className="cv-eyebrow">{f.label}</span><p>{f.body}</p></li>)}</ul>
      )}
    </div>
  );
}

// components/content/ContactEmailPanel.jsx



/** Receiver is configured in ONE place: the `email` prop (landing page passes CONTACT_EMAIL). No form, no backend, no sent state. */
function ContactEmailPanel({
  email = "aws@deployconvoy.com",
  subject = "Robot deployment — Convoy",
  linkLabel = "Email us about your deployment",
  note = "Opens your email application.",
  guideTitle = "Helpful to include",
  guide = ["The model you want to deploy", "Your robot configuration", "The deployment challenge you’re working through"],
  addressLabel = "Email",
}) {
  const href = `mailto:${email}${subject ? `?subject=${encodeURIComponent(subject)}` : ""}`;
  return (
    <div className="cv-panel cv-email">
      <div className="cv-email__address">
        <span className="cv-email__label">{addressLabel}</span>
        <span className="cv-email__value"><a href={href}>{email}</a></span>
      </div>
      <div>
        <Button href={href} arrow>{linkLabel}</Button>
        <p className="cv-email__note" style={{ marginTop: "var(--space-8)" }}>{note}</p>
      </div>
      {guide && guide.length > 0 && (
        <div>
          <p className="cv-email__guide-title">{guideTitle}</p>
          <ul className="cv-email__guide">{guide.map((g) => <li key={g}>{g}</li>)}</ul>
        </div>
      )}
    </div>
  );
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
  { key: "model", title: "Model assets", note: "the trained policy", items: ["policy weights", "preprocessing spec", "action space"] },
  { key: "processing", title: "Input and action processing", note: "how signals are shaped", items: ["sensor alignment", "normalisation", "command translation"] },
  { key: "runtime", title: "Runtime", note: "what executes it", items: ["inference loop", "dependencies", "timing budget"] },
  { key: "target", title: "Target configuration", note: "where it runs", items: ["robot configuration", "compute placement", "controller interface"] },
  { key: "evidence", title: "Evaluation evidence", note: "why it may be released", items: ["task criteria", "runtime criteria", "tested configuration", "conditions and results"], evidence: true, wide: true },
];

const RELEASE_ENVELOPE_CAPTION = "Conceptual architecture: a robot-policy release brings together the model, input and action processing, runtime, target configuration, and evaluation evidence.";

/** The recurring brand device. Tag reads "Robot-policy release"; identity group replaces any version number. No fabricated values. */
function ReleaseEnvelope({ tag = "Robot-policy release", identity = "Release identity", identityNote = "name · configuration · evidence reference", groups = DEFAULT_GROUPS, caption = RELEASE_ENVELOPE_CAPTION, title, describedBy }) {
  const capId = React.useId();
  return (
    <figure className="cv-dg" style={{ margin: 0 }} aria-describedby={caption ? capId : describedBy}>
      {title && <div className="cv-dg__title">{title}</div>}
      <div className="cv-env">
        <div className="cv-env__tag">{tag}</div>
        <div className="cv-env__grid">
          {identity && <div className="cv-env__group cv-env__group--wide cv-env__group--identity"><h4>{identity}{identityNote && <small>{identityNote}</small>}</h4></div>}
          {groups.map((g) => (
            <div key={g.key || g.title} className={["cv-env__group", g.wide ? "cv-env__group--wide" : "", g.evidence ? "cv-env__group--evidence" : ""].filter(Boolean).join(" ")}>
              <h4>{g.title}{g.note && <small>{g.note}</small>}</h4>
              {g.items && <ul className="cv-env__items" style={{ listStyle: "none", margin: 0, padding: 0 }}>{g.items.map((it) => <li key={it} className={`cv-env__item${g.evidence ? " cv-env__item--evidence" : ""}`}>{it}</li>)}</ul>}
            </div>
          ))}
        </div>
      </div>
      {caption && <figcaption id={capId} className="cv-dg__caption" style={{ marginTop: "var(--space-16)" }}>{caption}</figcaption>}
    </figure>
  );
}

// components/diagrams/ReleaseComposition.jsx




/** Trained model → robot-policy release → robot / controller. orientation="horizontal" for full-width figures (stacks ≤900px via CSS); "vertical" for column placement such as the hero split. Default vertical. */
function ReleaseComposition({ orientation = "vertical", title = "The robot-policy release", caption = RELEASE_ENVELOPE_CAPTION, left = { title: "Trained model", sub: "from your training pipeline", ownership: "your team" }, right = { title: "Robot and controller", sub: "existing controller · safety system", ownership: "outside Convoy" } }) {
  const capId = React.useId();
  const descId = React.useId();
  const vertical = orientation === "vertical";
  return (
    <figure className="cv-dg" style={{ margin: 0 }} aria-describedby={`${descId} ${capId}`}>
      {title && <div className="cv-dg__title">{title}</div>}
      <p id={descId} className="cv-sr-only">A trained model, delivered by your team, enters the robot-policy release. The release is the dashed boundary Convoy is building: release identity, model assets, input and action processing, runtime, target configuration, and evaluation evidence. The release hands processed actions to the robot and its existing controller and safety system, which remain outside Convoy.</p>
      <div className={`cv-comp${vertical ? " cv-comp--vertical" : ""}`}>
        <div className="cv-comp__side"><div className="cv-dg__node cv-dg__node--external">{left.title}{left.sub && <small>{left.sub}</small>}</div><div className="cv-comp__ownership">{left.ownership}</div></div>
        <div className="cv-comp__arrow cv-path__arrow cv-path__arrow--release" aria-hidden="true"><ArrowSvg dash="6 4" /></div>
        <ReleaseEnvelope caption="" />
        <div className="cv-comp__arrow cv-path__arrow" aria-hidden="true"><ArrowSvg /></div>
        <div className="cv-comp__side"><div className="cv-dg__node cv-dg__node--external">{right.title}{right.sub && <small>{right.sub}</small>}</div><div className="cv-comp__ownership">{right.ownership}</div></div>
      </div>
      {caption && <figcaption id={capId} className="cv-dg__caption" style={{ marginTop: "var(--space-16)" }}>{caption}</figcaption>}
    </figure>
  );
}

function ArrowSvg({ dash }) {
  return <svg viewBox="0 0 40 16" fill="none"><path d="M1 8h32" stroke="currentColor" strokeWidth="2" strokeDasharray={dash} strokeLinecap="round" /><path d="M29 3.5 34.5 8 29 12.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>;
}

// components/diagrams/ExecutionPath.jsx



const DEFAULT_NODES = [
  { title: "Sensors", sub: "cameras · joints · force", site: "on the robot", scope: "outside" },
  { title: "Input processing", sub: "align · normalise", site: "placement varies", optional: true, scope: "convoy" },
  { title: "Model", sub: "learned policy", site: "placement varies", optional: true, scope: "convoy", model: true },
  { title: "Action processing", sub: "translate · limit", site: "near the controller", scope: "convoy" },
  { title: "Robot controller", sub: "controller · safety system", site: "on the robot", scope: "outside" },
];

const EXECUTION_PATH_CAPTION = "Execution placement depends on the task’s compute, timing, and failure requirements.";

/** Sensors → Input processing → Model → Action processing → Robot controller. Convoy-scoped nodes sit inside a labelled dashed boundary that is real DOM (not aria-hidden) and survives the vertical layout. */
function ExecutionPath({ nodes = DEFAULT_NODES, scopeLabel = "Convoy runtime boundary", legend = true, traceable = false, caption = EXECUTION_PATH_CAPTION, title }) {
  const [active, setActive] = React.useState(-1);
  const timer = React.useRef();
  const descId = React.useId();
  const capId = React.useId();
  const reduced = typeof window !== "undefined" && window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const trace = () => {
    clearTimeout(timer.current);
    if (reduced) { setActive(active === nodes.length ? -1 : nodes.length); return; }
    let i = 0;
    const step = () => { setActive(i); if (i < nodes.length) { i += 1; timer.current = setTimeout(step, 520); } };
    step();
  };
  React.useEffect(() => () => clearTimeout(timer.current), []);
  const lit = (i) => active >= 0 && (active === nodes.length || i <= active);
  // group consecutive nodes by scope so the boundary wraps the Convoy-scoped run
  const groups = [];
  nodes.forEach((n, i) => { const g = groups[groups.length - 1]; if (g && g.scope === n.scope) g.items.push(i); else groups.push({ scope: n.scope, items: [i] }); });
  const node = (i) => { const n = nodes[i]; return (
    <div className={`cv-path__node${lit(i) ? " is-lit" : ""}`} key={n.title}>
      <div className={["cv-dg__node", n.scope === "outside" ? "cv-dg__node--external" : "", n.model ? "cv-dg__node--model" : ""].filter(Boolean).join(" ")}>{n.title}{n.sub && <small>{n.sub}</small>}</div>
      <div className={`cv-path__site${n.optional ? " cv-path__site--optional" : ""}`}>{n.site}</div>
    </div>
  ); };
  const arrow = (i) => <DiagramArrow key={`a${i}`} kind="execution" />;
  const description = `${nodes.map((n) => n.title).join(", then ")}. ${scopeLabel}: ${nodes.filter((n) => n.scope === "convoy").map((n) => n.title).join(", ")}. Outside Convoy: ${nodes.filter((n) => n.scope === "outside").map((n) => n.title).join(" and ")}.`;
  return (
    <figure className="cv-dg" style={{ margin: 0 }} aria-describedby={`${descId} ${capId}`}>
      {title && <div className="cv-dg__title">{title}</div>}
      <p id={descId} className="cv-sr-only">{description}</p>
      <div className="cv-path">
        {groups.map((g, gi) => (
          <React.Fragment key={gi}>
            <div className={`cv-path__group cv-path__group--${g.scope === "convoy" ? "scope" : "external"}`} data-label={g.scope === "convoy" ? scopeLabel : undefined} role={g.scope === "convoy" ? "group" : undefined} aria-label={g.scope === "convoy" ? scopeLabel : undefined}>
              {g.items.map((i, k) => <React.Fragment key={i}>{node(i)}{k < g.items.length - 1 && arrow(i)}</React.Fragment>)}
            </div>
            {gi < groups.length - 1 && arrow(`g${gi}`)}
          </React.Fragment>
        ))}
      </div>
      <figcaption className="cv-dg__foot">
        {caption && <span id={capId} className="cv-dg__caption">{caption}</span>}
        {legend && <DiagramLegend items={["execution", "release", "optional"]} />}
        {traceable && <button type="button" className="cv-btn cv-btn--secondary cv-btn--sm" onClick={trace} aria-pressed={active >= 0}>{active >= 0 ? "Trace again" : "Trace execution"}</button>}
      </figcaption>
    </figure>
  );
}

// components/diagrams/DiagnosticPanel.jsx


const DEFAULT_ROWS = [
  { group: "release identity" },
  { k: "release", v: "name and configuration reference", t: "m" },
  { group: "model assets" },
  { k: "policy", v: "artifact and preprocessing specification", t: "m" },
  { k: "action_space", v: "units, coordinates, and command form", t: "m" },
  { group: "input and action processing" },
  { k: "inputs", v: "sensor set and alignment", t: "m" },
  { k: "limits", v: "command bounds and translation", t: "m" },
  { group: "runtime and target" },
  { k: "runtime", v: "dependencies and timing budget", t: "m" },
  { k: "target", v: "robot configuration and compute placement", t: "m" },
  { group: "evaluation evidence" },
  { k: "criteria", v: "task and runtime criteria", t: "m" },
  { k: "results", v: "conditions and results for the tested configuration", t: "m" },
];

/** Dark panel listing conceptual field categories of a release. Not used on the launch landing page. Never a terminal or API. */
function DiagnosticPanel({ title = "Release contents · conceptual categories", status = "not a schema", rows = DEFAULT_ROWS, footer = "Category names are illustrative. They show what a release carries, not an API, schema, or command." }) {
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

window.ConvoyDesignSystem_29acff = { Button, ActionGroup, Field, useFieldId, TextField, TextArea, Checkbox, SiteHeader, SiteFooter, SkipLink, PageShell, Section, StatusBadge, SectionHeading, DependencyRows, Disclosure, WorkflowSteps, WorkflowSequence, Notice, PartnershipPanel, ContactEmailPanel, DiagramArrow, DiagramLegend, ReleaseEnvelope, RELEASE_ENVELOPE_CAPTION, ReleaseComposition, ExecutionPath, EXECUTION_PATH_CAPTION, DiagnosticPanel };