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
| `ReleaseComposition` | `src/components/Hero.tsx` figure | Production keeps the vertical model → release → controller stack inside the 7-column hero at every width (the horizontal variant does not fit that column); endpoint ownership labels and a visually hidden relationship sentence come from the design. |
| `ReleaseEnvelope` | `src/components/ReleaseEnvelope.tsx` | Dashed oxide boundary, "Robot-policy release" tag, release-identity group, five field groups with 16px labels and 14px mono metadata; the compact strip carries "Release identity". |
| `ExecutionPath` | `src/components/ExecutionPath.tsx`, `src/components/ExecutionTrace.tsx` | The three Convoy nodes sit in a `role="group"` labelled "Convoy runtime boundary" that stays visible in the vertical layout; sr-only relationship sentence; 16/14px node text; placement caption; the one-shot trace described under Motion below. |
| `DiagramArrow`, `DiagramLegend` | inline SVG in `ExecutionPath.tsx` and `Hero.tsx` | Solid ink for execution, dashed oxide for release, dotted for configuration-dependent placement. |
| `PartnershipPanel` | `src/components/DesignPartnerSection.tsx` | One bordered white panel; heading, lead and action left, fit list right. |
| `ContactEmailPanel` | `src/components/ContactSection.tsx` | Mailto link with subject and template, address as large selectable mono text, guidance list. Receiver is `CONTACT_EMAIL` in `src/content/homepage.ts`. |
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

Rules the implementation follows (`ExecutionTrace.tsx`, `globals.css`):

- The complete static diagram (labels, connectors, boundary) is rendered at
  first paint. The trace is an overlay on each connector drawn by CSS
  keyframes, about 2s end to end, staggered across the four connectors with
  a brief border emphasis on each node as it is reached; then it lets go.
- It plays once when at least 60% of the figure is in view, unless the
  visitor prefers reduced motion or has Save-Data on. A 48px "Trace the
  path" button replays it by click, tap, Enter or Space. Pointer movement
  never starts it; hover only darkens a node border.
- Leaving the viewport, hiding the document, or unmounting cancels a run.
  One timeout marks the end; there is no requestAnimationFrame loop and no
  idle timer.
- Under reduced motion the button applies a quiet static emphasis to the
  path for about a second instead of animating it.
- Primary actions move their arrow 2px on hover or focus; colour and border
  transitions are 160ms. No parallax, tilt, cursor tracking, scroll
  hijacking, loops, or layout shift.
- The trace is the reading order of a conceptual diagram. Nothing about it
  suggests live robot execution or guaranteed safety.
