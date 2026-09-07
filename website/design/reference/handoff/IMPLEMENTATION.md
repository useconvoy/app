# Implementation handoff — Convoy landing (useconvoy/app)

Target: `useconvoy/app` on the Convoy cloud machine, deployconvoy.com (AWS Lightsail / Route 53, already configured). Claude Code is implementing from this design system; this document records the launch-sync deltas, the component/state matrix, copy, and what was actually tested here.

## Deltas in this pass (integrate these)

The live site (useconvoy/app main c0d3f13, PR60 + PR61) already carries the launch copy and behaviour; **do not revert it**. Integrate only the component-level deltas below where the app's implementation differs.

Added components: `SkipLink`, `PageShell` + `Section`, `ActionGroup`, `ReleaseComposition` (default vertical; horizontal for full-width figures only), `PartnershipPanel`, `ContactEmailPanel`.

Fix after Codex Chrome review at 1440×900: the hero previously mounted the horizontal composition inside the 7-column region (~280px envelope, overlapping chips). Now the hero uses the vertical composition, the hero grid is top-aligned, and envelope chips wrap instead of `nowrap`.
Changed components: `SiteHeader` (sticky; no wordmark tag by default; Escape closes **and restores focus to the toggle**; pointer-down outside closes; `aria-current="true"` on the current link in both navs), `SiteFooter` (domain is a link; year 2026; no badges), `ReleaseEnvelope` (identity group replaces version numbers; "Evaluation evidence" with criteria/conditions items; single approved caption; 16/14px text), `ExecutionPath` (Convoy runtime boundary is a labelled `role="group"` that survives the vertical layout and is never `aria-hidden`; sr-only relationship sentence; placement caption; no rates), `WorkflowSteps` (no "proposed" tags; `WorkflowSequence` alias; optional `detail` disclosure), `Disclosure` (`compact`), `DiagnosticPanel` (categories only, no values — **not on the landing page**).
Removed from launch: the preview contact form and all preview/"not connected" wording, `Stage: In development` badge, "Precision Release" header tag, `release v0.3 · example`, fabricated targets/rates/test counts/pass results, "roll back" promises, "a person replies" and "working session" promises, any POST/endpoint instructions.
Tokens: `--text-h3-line` 1.2, `--text-lead-line` 1.55, new `--text-caption-size/line` 14/1.45, `--text-diagram-label` 16, `--text-diagram-meta` 14, `--header-height` 72/64, `--ease` `cubic-bezier(0.2,0,0,1)`, `--ease-diagram` `cubic-bezier(0.16,1,0.3,1)`. `html{scroll-padding-top}` and `[id]{scroll-margin-top}` clear the sticky header.

## Contact (launch behaviour)
`ContactEmailPanel` renders the address `aws@deployconvoy.com` as selectable text and as a `mailto:` link with subject "Robot deployment — Convoy", plus the primary link "Email us about your deployment" and the note "Opens your email application." Guidance list: model, robot configuration, deployment challenge. **No form, no backend, no email provider, no sent/success state.** The receiver is a single constant (`CONTACT_EMAIL` in `LandingPage.jsx`, or the `email` prop).

## Component / state matrix (rendered in `ui_kits/landing/state-board.html`)
- SiteHeader — desktop: default, hover, current link (oxide underline), long labels; mobile: closed, open (current link marked), toggle focus/`aria-expanded`, long labels; Escape → closes + focus back on toggle; link click closes.
- Button / ActionGroup — primary, secondary, text, inverse; sm; disabled; pressed (`translateY(1px)`, deep oxide); keyboard focus (2px oxide, 3px offset); inline vs stacked full-width ≤640.
- Hero — 5/7 split above 1200px (1440) with the **vertical** `ReleaseComposition` in the 7-column region, top-aligned; single column at ≤1200px (1024, 768, mobile) with copy + CTAs before the figure. The horizontal composition is for full-width figures only (≥ ~820px container).
- ReleaseComposition / ReleaseEnvelope — vertical (default; hero) or horizontal (`orientation="horizontal"`, full-width only; stacks ≤900); identity + five groups; envelope grid single-column ≤640; chips wrap naturally (`overflow-wrap:anywhere`); labels never below 16/14px.
- ExecutionPath — horizontal ≥640, vertical below in the same order; boundary group labelled in both; optional trace (user-triggered) with reduced-motion complete state.
- DependencyRows / WorkflowSteps — aligned rows / three columns ≥900; ordered stacks below.
- PartnershipPanel / ContactEmailPanel — 7/5 grid / single column; address sized `clamp(18px, 4.6vw + 2px, 28px)` (18px at ≤640) so `aws@deployconvoy.com` fits on one line at 320 CSS px; `overflow-wrap:break-word` as fallback only.
- Disclosure — collapsed, expanded, focused summary (inset outline), long answer, multiple open; native `<details>` (Enter/Space, expanded state exposed by the element).
- SiteFooter — description, footer nav, legal row with domain link; no badges.

## Public copy (launch)
Eyebrow: Deployment infrastructure for physical AI · H1: Deploy AI models to real robots. · Lead: Convoy is building the runtime and release workflow that connects trained models to robot sensors, compute, and controllers, starting with learned manipulation. · CTAs: Discuss your deployment / Explore the workflow · Invitation: Help shape the next robot deployment workflow. · Figure label: The robot-policy release · Caption: Conceptual architecture: a robot-policy release brings together the model, input and action processing, runtime, target configuration, and evaluation evidence. · Gap heading/intro + four rows (Part XIII) as in `LandingPage.jsx` · Workflow intro: We’re building a workflow around the complete robot-policy release. · Package / Qualify / Release bodies as supplied · Execution placement depends on the task’s compute, timing, and failure requirements. · Partnership: Each partnership starts with a defined model, robot configuration, and deployment goal. · FAQ heading: Questions about Convoy. · Availability: Convoy is in development. We’re speaking with robotics teams about focused design partnerships, with compatibility and scope defined around each deployment. · Closing: Tell us about your next robot deployment. / Share your model, robot configuration, and the deployment challenge you’re working through. · Footer: Convoy — Deployment infrastructure for physical AI. © 2026 Convoy. deployconvoy.com.

## Accessibility & responsive requirements
- Ordinary anchor links; disclosure pattern for the mobile menu (button `aria-expanded`/`aria-controls`), per WAI-ARIA APG disclosure navigation. Escape closes and restores focus.
- Targets ≥48px (WCAG 2.2 2.5.8 exceeds 24px minimum). Focus not obscured by the sticky header (2.4.11) via scroll padding.
- Complex diagrams: visible caption + sr-only relationship description (WAI complex images tutorial).
- `prefers-reduced-motion`: transitions collapse; trace shows the complete state.
- Contrast: text ≥4.5:1 (muted `#647168` only on paper; secondary ink on linen), boundaries ≥3:1 (`#78867D` on paper 3.3:1).
- Reflow at 320 CSS px / 400% zoom: single column, no horizontal scroll.

References: https://www.w3.org/WAI/ARIA/apg/patterns/disclosure/examples/disclosure-navigation/ · https://www.w3.org/WAI/ARIA/apg/patterns/disclosure/ · https://www.w3.org/WAI/tutorials/images/complex/ · https://www.w3.org/TR/WCAG22/ · https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html · https://www.w3.org/WAI/WCAG22/Understanding/focus-not-obscured-minimum.html · https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/a#linking_to_an_email_address · https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/At-rules/@media/prefers-reduced-motion

## Design truth
- Tokens: `tokens/*.css`. Component CSS: `components/components.css`. Page layout: `ui_kits/landing/landing.css` (drop the `qa-frame` rule). Behaviour: `components/**/*.jsx` with props in `.d.ts`. **Not for production:** `components/viewport-scopes.css`, the `?w=` switch in `index.html`, `Desktop-1440.html`, `Mobile-390.html`, `state-board.html` — design-review helpers only.
- Fonts: IBM Plex Sans 400/500/600 + Mono 400/500; self-host (`@fontsource/ibm-plex-sans`, `@fontsource/ibm-plex-mono`), `font-display: swap`.

## Testing record (this pass)

**Actual checks (real DOM, real interaction, in this tool's ~924px-wide preview):**
- `ui_kits/landing/index.html` at native 924px (falls in the 900–1200 band): hero stacks to one column, header inline nav, no horizontal scroll, buttons single-line after fix (`white-space:nowrap` inline; wrap only when stacked/block).
- `state-board.html` at 924px: horizontal ReleaseComposition and ExecutionPath, desktop rows/steps/panels/footer; the 390px column renders the `.cv-viewport-*` scoped variants (see below). Vertical composition arrows fixed to rotate correctly.
- Keyboard: Menu button click → menu open (`aria-expanded=true`); Escape → menu closed and **focus returned to the toggle** with visible ring (observed). Disclosure summary focus ring (inset) visible; native `<details>` toggles with Enter/Space.
- Contrast (computed from tokens): secondary on linen 6.66; muted on paper 4.66; oxide on paper 5.77 / on white 6.34; accent-hover on tint 7.35; strong border on paper 3.47 (≥3:1 non-text). Muted ink is never used on linen (audited in CSS).
- Console: clean apart from the Babel in-browser warning (design preview only).
- Standalone `export/standalone/index.html` renders identically without the compiled bundle.

**Forced-width simulations (`.cv-viewport-md/-sm` scopes via `index.html?w=390|320`, NOT real viewport changes):**
- 390: single-column hero with copy and CTAs before the figure; envelope stack; execution path vertical in order Sensors → Input processing → Model → Action processing → Robot controller, with the "Convoy runtime boundary" label visible; email address and CTA intact; no elements overflow the 390 frame.
- 320: same; email CTA wraps to two lines (fixed this pass), address breaks with `overflow-wrap:anywhere`; 2 overflowing elements before the fix → 0 after. Text below 14px: eyebrows only (13px mono, per spec).

**Not verified here (tool limits) — verify in Chrome on the standalone or the live site:**
- True 1440 / 1024 desktop media-query layout (the preview cannot render a 1440px viewport; `Desktop-1440.html` embeds a real 1440px iframe for the user's browser but my capture tool cannot read iframe content). Chosen layout: 5/7 hero split with vertical composition above 1200px; single column at 1024 and 768 (landing.css stacks at ≤1200).
- Real 768 tablet viewport, mobile landscape, 200% text resize, 400% zoom (320 CSS px reflow is covered by the 320 simulation only).
- Hover states as pointer events (documented in CSS; rendered statically in the board).
Codex's Chrome checks on the live site (desktop, 390/320, Menu/Escape focus return, address wrapping) and the 16 build + 12 deployment gates already cover the production build; this record is for the design reference only.

## Suggested structure
