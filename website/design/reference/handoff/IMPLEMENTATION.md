# Implementation handoff — Convoy landing (useconvoy/app)

Target: `useconvoy/app` on the Convoy cloud machine, served at deployconvoy.com via the existing AWS Lightsail / Route 53 setup. This document tells Claude Code what to build and where the design truth lives.

## What to ship
1. One responsive landing page matching `ui_kits/landing/index.html` at 1440 / 768 / 390 / 320. Section order and copy are fixed by the brief (see `ui_kits/landing/LandingPage.jsx`); do not add pages, docs, pricing or demo links.
2. A contact form (work email, company/team, deployment problem required; name, model/hardware optional). The design preview sends nothing. Production must POST to an endpoint configured from the **verified repository configuration** (env/secrets already in the repo) — do not invent an inbox address. Show a real success message only on a 2xx response; show the error Notice otherwise.
3. Semantic HTML: one `h1`, `h2` per section, `<details>` FAQ, `<nav aria-label>`, skip link, `aria-current`, `aria-expanded`/`aria-controls` on the menu toggle, `role="alert"` on errors. Keyboard focus ring must remain visible (`:focus-visible` in `tokens/base.css`).

## Design truth
- Tokens: `tokens/*.css` (copy verbatim; keep the `≤640px` type overrides and the layout-padding overrides).
- Component styles: `components/components.css` — plain CSS classes (`.cv-*`); port as-is or map to your CSS-module/Tailwind setup preserving every value.
- Component behaviour: `components/**/**.jsx` — React reference implementations (props in the sibling `.d.ts`). Framework-agnostic; port to the app's stack.
- Page layout classes: `<style>` block in `ui_kits/landing/index.html` (`.lp-*`).
- Fonts: IBM Plex Sans 400/500/600 + IBM Plex Mono 400/500. Self-host (npm `@fontsource/ibm-plex-sans`, `@fontsource/ibm-plex-mono`) instead of the Google Fonts link used in the preview; add `font-display: swap`.

## Behaviour notes
- Header is in flow, not sticky. Mobile menu is a `<nav hidden>` toggled by a real button; Escape closes; links close on click.
- Motion is limited to the 120/180/240ms transitions in the CSS and the user-triggered "Trace execution" button. Respect `prefers-reduced-motion` (already in base.css; the trace falls back to a static highlight).
- No `100vh` hero, no autoplay, no analytics-dependent rendering.
- Diagrams are DOM + CSS (+ tiny inline SVG arrows). No images. No canvas.

## Content guards (fail the PR if violated)
- Any occurrence of: customer logos, metrics, uptime, "$", "first/only", "any robot", terminal prompts (`$ `), fake API paths, shipped-fleet language.
- Any workflow step not labelled "proposed"; any diagram without the "conceptual" caption; any form success without a real response.

## QA record (first visual pass, this project)

Checked in-browser at 1440 (native), and at 768 / 390 / 320 via `index.html?w=NNN` (forces the breakpoint styles; QA-only). Results:
- No horizontal overflow at any width; envelope stacks to one column and execution path turns vertical at ≤640.
- Header collapses to a 48px "Menu" button ≤900 with `aria-expanded`/`aria-controls`; Escape closes; CTA becomes full-width inside the menu. Wordmark tag hidden ≤640.
- Form validation blocks submit and shows per-field `role="alert"` errors plus a form-level error Notice; the preview never shows success.
- Contrast (WCAG): muted on paper 4.66, secondary 7.36, white on oxide 6.34, oxide on paper 5.77, status pairs 5.8–6.3, inverse text ≥9.4. Diagram sub-labels moved to secondary ink so nothing on linen drops below 4.5.
- Motion: only 120/180/240ms transitions and the user-triggered trace; `prefers-reduced-motion` collapses transitions and makes the trace a static toggle.
- Console clean apart from the in-browser Babel warning (preview only).

## Suggested structure
```
app/
  styles/tokens.css        ← tokens/*.css concatenated
  styles/components.css    ← components/components.css
  styles/landing.css       ← .lp-* block
  components/{Button,TextField,TextArea,Checkbox,SiteHeader,SiteFooter,StatusBadge,SectionHeading,DependencyRows,WorkflowSteps,Disclosure,Notice,ReleaseEnvelope,ExecutionPath,DiagnosticPanel,DiagramArrow}
  pages/index             ← sections from LandingPage.jsx
  api/contact             ← configured from repo config; validates the same rules as the preview
```
