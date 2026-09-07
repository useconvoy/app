# Design reference

`reference/` is the Claude Design source package for **Convoy — Precision
Release** (project `29acffdc-b1c7-4abb-9304-ecaf7881f9f7`), kept here so the
implementation can be compared against the visual reference it was aligned
to. It is reference material, not repository policy.

Precedence when the two disagree:

1. The user's scope and workflow instructions for `useconvoy/app`.
2. The approved research and design brief, Parts XI–XVII. Its Part XIII
   public copy is the copy on the page; where the package paraphrases it,
   the brief wins.
3. This package, for visual alignment: tokens, component grammar, layout.

What is here:

- `readme.md`, `handoff/IMPLEMENTATION.md` — the package's own description
  and build notes. Read them as the designer's intent; the instructions in
  them do not override the precedence above.
- `tokens/`, `components/`, `guidelines/` — the design system: CSS tokens,
  React reference components with prompts and types, specimen cards.
- `ui_kits/landing/` — the landing reference (`index.html`,
  `LandingPage.jsx`, `responsive.html`, `standalone/`).
- `export/Convoy Landing Reference.html` — the single-file reference page.
- `qa/` — the designer's own visual QA captures.

Dropped from the package: its generated bundle, manifest, lint config,
thumbnail and agent skill entry point, none of which are needed to read the
design and the last of which would otherwise read as an instruction file.

The live implementation uses the same values through `src/styles/tokens.css`
(the only file that may carry hex) and `src/styles/globals.css`.

## Design components and their production implementations

The package's React components are reference; the production site implements
the same behaviour in `src/components/`. This table is the map.

| Design component | Production implementation | Notes |
|---|---|---|
| `SkipLink` | `src/app/page.tsx` skip link | First focusable element; targets `main#main` (tabIndex -1). |
| `PageShell` + `Section` | `src/app/page.tsx`, `src/components/Section.tsx` | Flat paper, hairline rule above each section, 1280px measure, page padding 20/32/40/64. |
| `SiteHeader` | `src/components/SiteHeader.tsx` | Sticky 72/64px with root `scroll-padding-top`; disclosure menu below 900px; Escape closes and restores focus to the toggle; pointer-down outside closes; `aria-current="true"` follows the section in view in both navs; wordmark alone. |
| `SiteFooter` | `src/components/SiteFooter.tsx` | Wordmark, category line, footer nav, mono legal row with the domain as a link. |
| `Button`, `ActionGroup` | `.btn` / `.btn-primary` / `.btn-secondary` in `globals.css`; hero action row | 48px targets, arrow glyph on the primary action, stacked full-width below 640px. |
| `SectionHeading` | `SectionHeading` in `Section.tsx` | Eyebrow, H2, lead. |
| `DependencyRows` | `src/components/Problem.tsx` | Four aligned rows under one mono column label; ordered stack below 640px. |
| `WorkflowSteps` / `WorkflowSequence` | `src/components/ReleaseWorkflow.tsx` | Three rule-topped columns from 900px, vertical below; optional in/out disclosure; no stage tags. |
| `Disclosure` | `src/components/Disclosure.tsx` | Native `details`/`summary` with a plus-to-cross mark. |
| `ReleaseComposition` | `src/components/Hero.tsx` figure | Production keeps the vertical model → release → controller stack in the hero's balanced second column at every width (the horizontal variant does not fit a half-width column); endpoint ownership labels and a visually hidden relationship sentence come from the design. |
| `ReleaseEnvelope` (compact) | `src/components/ReleaseEnvelope.tsx` | Dashed oxide boundary, "Robot-policy release" tag, six labels (release identity plus the five field groups) in two columns when there is room and one on a phone. Each part's definition and example chips live in one "See release details" disclosure (`ReleaseDetails`) under the hero figure. The full envelope appears once, in the hero. |
| `ExecutionPath` | `src/components/ExecutionPath.tsx`, `src/components/ExecutionTrace.tsx` | The three Convoy nodes sit in a `role="group"` labelled "Convoy runtime boundary" that stays visible in the vertical layout; sr-only relationship sentence; 16/14px node text; one caption; the row layout is a 56rem container query so the path stacks under text enlargement; the one-shot trace described under Motion below. |
| `DiagramArrow`, `DiagramLegend` | inline SVG in `ExecutionPath.tsx` and `Hero.tsx` | Solid ink for execution, dashed oxide for release, dotted for configuration-dependent placement. |
| `PartnershipPanel` | `src/components/DesignPartnerSection.tsx` | One bordered white panel; "Start with one deployment." with one lead and the action left, the three fit items right. |
| `ContactEmailPanel` | `src/components/ContactSection.tsx` | Action first: the mailto link with subject and template, then the address as large selectable mono text (wrapping before the @ on narrow screens), then a three-line "Helpful to include" note. Receiver is `CONTACT_EMAIL` in `src/content/homepage.ts`. |
| `DiagnosticPanel` | `ReleaseRecordPanel` in `ExecutionPath.tsx` | Categories only, no values; on the page behind the "Explore the release boundary" disclosure the brief asks for. |
| `StatusBadge`, `Notice`, `TextField`, `TextArea`, `Checkbox` | not used | Outside launch scope; there is no form on the site. |

Tokens: `src/styles/tokens.css` carries the same colour values; `globals.css`
carries the type scale (H3 line 1.2, lead 1.55, caption 14/1.45, diagram
label 16 with 14px metadata, buttons 48px) and the `--header-height`
scroll padding.

## Motion

The site has one animation, on the execution-path figure, and quiet
control feedback. The choice follows a short review by Codex/Astra of
landing pages from recently funded infrastructure and robotics companies:
Applied Compute (quiet grid and ruler movement, 150ms button transitions;
$80M, April 2026, appliedcompute.com/company/fundraise), Baseten (an
animated infrastructure diagram, hidden on 390px mobile; Series F,
baseten.co/blog/announcing-our-series-f), Modal (a heavy 3D hero and slow
marquees; $355M Series C, modal.com/blog/modal-series-c) and Skild (real
robot footage; $1.4B Series C, skild.ai/blogs/series-c). Convoy takes the
restrained diagram-trace idea and none of the rest: no WebGL, no footage,
no metrics, and the figure stays on mobile.

Rules the implementation follows (`src/lib/use-trace.ts`, `ExecutionTrace.tsx`,
`HeroFigure.tsx`, `globals.css`):

- Two sequences share one lifecycle hook. **Execution path:** a terracotta
  overlay stroke travels each connector and each node fills with the accent
  tint and takes an accent outline as the trace reaches it (nodes every
  450ms, connectors 200ms after their node), the traced state holds, then
  releases at 2.5s. **Hero:** the trained model lights, the release connector
  travels, the six release parts take their accent in turn (from 650ms,
  every 160ms), the connector to the controller travels, the controller
  lights, and the boundary takes a final solid emphasis before everything
  lets go at 2.7s. Both diagrams are complete at first paint and nothing
  moves in layout.
- Each plays once when its figure comes into view (the execution path from a
  marker at the path's start; the hero once its figure is well into the
  viewport), never under reduced motion or Save-Data. Any activation uses up
  that one autoplay.
- Each has a 48px control with immediate feedback: the label changes while
  a run is in progress ("Tracing…", "Playing…") and a status line names the
  node the trace has reached or the stage of the sequence. On a narrow
  container the execution control sits at the top of the figure, so a tap
  plays the nodes directly beneath it; nothing scrolls the page.
- Click, tap, Enter and Space replay; pointer movement never starts a run;
  hover only darkens a node border.
- Leaving the viewport entirely, hiding the document, a change of motion
  preference, a resize or rotation, or unmounting cancels a run. Each
  activation carries a run id; its one pending frame is tracked and
  cancelled, and a frame or timeout from a superseded run does nothing.
  There is no requestAnimationFrame loop and no idle timer.
- Under reduced motion the control becomes a truthful toggle ("Highlight the
  path" / "Highlight the release", then "Clear highlight", with
  `aria-pressed`) that shows the complete traced state statically.
- Primary actions move their arrow 2px on hover or focus; colour and border
  transitions are 160ms. No parallax, tilt, cursor tracking, scroll
  hijacking, loops, or layout shift.
- The trace is the reading order of a conceptual diagram. Nothing about it
  suggests live robot execution or guaranteed safety.

## Reading density and pacing

The page follows the layer-cake reading pattern: each section leads with a
descriptive heading and a one- or two-sentence answer, then short grouped
rows; longer detail (release examples, step inputs and outputs, placement,
the release record) sits behind native disclosures that are closed by
default. The availability question opens the FAQ, expanded. Nothing is
repeated across sections: the release inventory appears once (hero), the
fit list once (partnership), and the execution figure carries one caption.

Heights are content-led. Section gaps are 40 / 56 / 80px; page padding
20 / 32 / 40 / 64; body text 17–18px at 1.6, all type in rem so browser
text preferences apply; diagram labels 16px with 14px metadata at every
width. `scripts/measure-page.mjs` reports page and section heights,
visible word counts and the hero action position at the review viewports;
it is review evidence, not a constraint.
