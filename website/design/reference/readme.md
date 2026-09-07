# Convoy Design System — "Precision Release"

Convoy is building deployment infrastructure for physical AI: the runtime and release workflow that connects trained models to robot sensors, compute, and controllers, starting with learned manipulation. The company is early-stage and in development. Every capability shown in this system is **proposed**; the design must never imply a shipped product, customers, metrics, or universal hardware support.

Public domain: **deployconvoy.com** (supersedes the older no-domain assumption). Product build lands in `useconvoy/app` on the Convoy cloud machine (AWS Lightsail / Route 53). Research and prompts are owned by Codex/Astra; this project owns design production.

## Sources

- Transcribed specification (September 2026), prepared by Codex/Astra from the user's `Convoy_Research_and_Design_Brief.md` and founder memo: tokens, type scale, layout, content sequence, boundaries and anti-patterns. This Design project received the transcription; the original documents are attached to the Claude Code session implementing `useconvoy/app`. Values are reproduced exactly. The founder memo's broad ambitions are roadmap, not claims.
- No Figma, GitHub repository, logo, or webfont binaries were supplied to this project. Fonts load from Google Fonts (IBM Plex Sans / Mono). No logo exists; the brand renders as a typographic wordmark.

## Design direction

A carefully edited engineering field manual. Warm paper surfaces, one action colour (oxide), hairline rules instead of card grids, IBM Plex Sans for reading and IBM Plex Mono for labels, tags and contract fields. The recurring brand device is the **versioned release envelope**: a dashed oxide boundary grouping Model assets / Input and action processing / Runtime / Target configuration / Qualification evidence. Robot controller and safety always sit outside it.

Forbidden: generic SaaS card grids, neon, gradients, fake terminal or API commands, stock robot heads, trucks/roads, fake customer logos, metrics, telemetry dashboards, fabricated product screenshots, docs/pricing/demo links, fake form success.

## Content fundamentals

- **Voice**: plain, declarative, engineer-to-engineer. Short sentences. Facts before framing. "Convoy is building…", "A trained model is not a robot deployment."
- **Person**: "we" for Convoy, "you/your" for the reader ("your robot's execution path"). Never "users".
- **Casing**: sentence case everywhere, including headings, buttons, nav, and eyebrows (eyebrows are rendered uppercase by CSS only). Product name "Convoy"; tagline "Precision Release" in title case as a tag.
- **Claims**: hedge structurally, not with adverbs. Mark the workflow "Proposed", the stage "In development", diagram labels "conceptual example". No "first/only platform", no "$10T", no "any robot, any model", no fleets, no uptime, no logos.
- **Boundaries as content**: the FAQ states what Convoy is not (robot maker, trainer, universal, cloud-only, controller/safety replacement, available today).
- **Buttons**: verb-first noun phrases ("Discuss your deployment", "Explore the workflow"). Never "Submit", "Get started", "Learn more →".
- **Numbers**: only structural (01/02/03 steps, release v0.3 example). No stats.
- **Emoji**: never. Unicode: middle dot `·` as separator in mono labels, em dash in prose (sparingly), typographic apostrophes.
- **Examples**:
  - Eyebrow: "Deployment infrastructure for physical AI"
  - H1: "Deploy AI models to real robots."
  - Stage: "Stage: In development. We're inviting robotics teams to help shape a focused deployment workflow."
  - Dependency rows (Part XIII, verbatim): "Inputs — Images, sensor data, and robot state prepared the way the model expects." Inputs are what the model expects, never what it "gives".
  - Caption: "Labels are conceptual examples. Robot controller and safety remain outside Convoy ownership."
  - Preview notice: "Preview: this form is not connected. Nothing you type is sent or stored."

## Visual foundations

- **Colour**: background paper `#F6F4EF`, white surface, linen subtle surface `#ECE9E1`; ink ladder `#18221F / #45534D / #647168`; borders hairline `#D3D8D1` and meaningful `#78867D`. One accent: oxide `#A63D22`, hover `#852F19`, tint `#F9E9E1`. Status pairs (deep ink on pale tint) for success/warning/error/info. Inverse slate `#16201C` with `#F3F5F0 / #BAC6BC` text and three code hues (`#F4B897 / #B7D8B8 / #ABCDED`) used **only** in the single dark diagnostic panel.
- **Colour usage**: oxide is for actions, links, focus, release/evidence lines and the envelope. It is never a background except the tint. Status colours label facts. Large areas are paper or white; the page is light with one dark panel at most.
- **Type**: IBM Plex Sans 400/500/600; IBM Plex Mono 400/500. H1 72/44 · 500 · 1.04/1.08 · -0.035em; H2 48/32 · 1.12; H3 28/24; lead 20/18; body 18/17 at 1.6; small 14; buttons 16; eyebrow 13 mono uppercase 0.08em. Prose measure 66ch. Headings `text-wrap: balance`, paragraphs `pretty`.
- **Spacing**: 4 · 8 · 12 · 16 · 24 · 32 · 48 · 64 · 80 · 112 · 144. Section gaps 112/72/56. Page padding 64 (>1200) / 40 / 32 / 20. Gutters 32/24/16. Max width 1280. Columns 12/8/4. Breakpoints 1200 / 900 / 640. Hero is a 5/7 split at natural height (never 100vh).
- **Radii**: 2 (badges), 4 default (buttons, inputs, nodes), 6 (envelope, panels). Controls ≥48px.
- **Backgrounds**: flat paper. No imagery, no photography, no textures, no gradients. Sections are separated by hairline rules, not colour bands. The partnership block and the form sit on white with a hairline border.
- **Borders and shadows**: hairline `--color-border` for separation, meaningful `--color-border-strong` for controls and diagram nodes. Shadows essentially absent (`--shadow-panel` is a 1px hairline; the mobile menu gets a soft drop for layering only). Depth comes from surface value (paper → white → linen), never from elevation.
- **Cards**: avoided. Content uses rule-topped columns and aligned rows. Where a container is needed (form, partnership block, diagnostic panel) it is a 1px bordered, 6px-radius surface with generous padding and no shadow.
- **Diagram grammar**: solid ink line = execution; dashed oxide = release/configuration/evidence; dotted muted = optional, configuration-dependent placement. Nodes are linen with a meaningful border; the model node is white with an ink border; anything outside Convoy (sensors, robot controller, safety) is a dashed grey node. Left-to-right, stacking vertically below 640px. Labels are conceptual and say so.
- **Motion**: 120ms colour changes (links, nav, press), 180ms button/input/disclosure transitions, 240ms diagram highlight. Easing `cubic-bezier(0.2,0,0,1)` (`--ease`) for state feedback; `cubic-bezier(0.16,1,0.3,1)` (`--ease-diagram`) for longer diagram transitions. No entrance animations, parallax, autoplay or loops. The only sequence is the user-triggered "Trace execution" on the execution path, and it degrades to a static highlight under `prefers-reduced-motion`.
- **Hover**: primary darkens to oxide-deep; secondary gains linen fill and ink border; text buttons gain tint; links darken. **Press**: 1px translateY. **Focus**: 2px oxide outline, 3px offset (inputs: oxide border + 3px tint ring). **Disabled**: opacity .45, not-allowed cursor; inputs go linen.
- **Layout rules**: nothing is fixed or sticky. The header is in flow. No transparency or blur anywhere. Iconography is nearly absent (see below).

## Iconography

- No icon set is used. The system relies on typography, rules, and line grammar instead of icons.
- Permitted glyphs, all built into components: a trailing arrow on the primary CTA (`Button arrow`), the plus/minus disclosure indicator (CSS), a three-line menu burger (CSS), the 8px status dot, and the diagram arrowheads (`DiagramArrow`). These are the only SVG/CSS glyphs; do not add others.
- No icon font, no emoji, no illustration set. If icons become necessary, add a 1.5px-stroke line set matching the arrow weight and document it here first.
- **Logo**: none was supplied and none was drawn. The wordmark is "Convoy" in Plex Sans 600 with an optional mono "Precision Release" tag (`guidelines/wordmark.html`).

## Components

Group `components/actions/`: **Button**.
Group `components/forms/`: **TextField**, **TextArea**, **Checkbox** (plus the shared `Field` wrapper exported from TextField.jsx).
Group `components/navigation/`: **SiteHeader** (with accessible mobile menu), **SiteFooter**.
Group `components/content/`: **StatusBadge**, **SectionHeading**, **DependencyRows**, **WorkflowSteps**, **Disclosure**, **Notice**.
Group `components/diagrams/`: **ReleaseEnvelope**, **ExecutionPath**, **DiagnosticPanel**, **DiagramArrow**, **DiagramLegend**.

Intentional additions beyond the brief: `Notice` (needed to state the form's preview status without faking success) and `DiagramArrow`/`DiagramLegend` (the arrow grammar had to live somewhere reusable).

Component CSS is class-based (`.cv-*`) in `components/components.css`, shipped through `styles.css`, so the same styles work in React and in plain HTML.

## Index

- `styles.css` — entry; imports everything below.
- `tokens/fonts.css`, `tokens/colors.css`, `tokens/typography.css`, `tokens/spacing.css`, `tokens/base.css` — tokens (base `--cv-*` and semantic aliases), responsive type/layout overrides, body reset, focus ring, reduced motion, utility classes (`cv-container`, `cv-section`, `cv-prose`, `cv-h1…`, `cv-eyebrow`).
- `components/` — five groups listed above; each has `.jsx`, `.d.ts`, `.prompt.md` and one `*.card.html`.
- `guidelines/` — 16 specimen cards: colours (surfaces, text & borders, oxide accent, status, inverse & code), type (headings, body & lead, mono & eyebrow, responsive scale), spacing (scale, layout grid, radii/controls/borders), brand (motion, diagram grammar, voice, wordmark).
- `ui_kits/landing/` — landing page reference (`index.html`, `LandingPage.jsx`, `responsive.html`, README) and `standalone/` (bundle-free copy: `convoy.css` + component sources + page, for review outside this tool).
- `export/Convoy Landing Reference.html` — single-file, offline-capable landing reference.
- `qa/` — screenshots from the first visual pass.
- `handoff/IMPLEMENTATION.md` — build notes for Claude Code (`useconvoy/app`).
- `thumbnail.html` — project tile. `SKILL.md` — agent skill entry point.
