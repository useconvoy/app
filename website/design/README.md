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
| `ExecutionPath` | `src/components/ExecutionPath.tsx`, `src/components/ExecutionDiagram.tsx` | The three Convoy nodes sit in a `role="group"` labelled "Convoy runtime boundary" that stays visible in the vertical layout; sr-only relationship sentence; 16/14px node text; one caption; the row layout is a 56rem container query so the path stacks under text enlargement; static, see Motion below. |
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

The diagrams are static. The two figures (the release figure in the hero
and the execution path) render complete at first paint with every label,
release part, placement note and the controller and safety boundary
visible; nothing plays, sequences, traces or replays, and there is no
replay control. The earlier one-shot trace was removed at the founder's
direction.

The one motion on the page is the **etched field** behind the hero figure,
from Claude Design's approved exploration `hero-etched-ripple` (project
29acffdc, `explorations/hero-etched-ripple.html`): a faint grid on the
paper, and thin rings that expand and fade behind the still diagram.
`src/components/EtchedField.tsx` implements it:

- Idle waves start every 3–5 s at random exposed places, favoring the
  left and right margins so they show without running under the copy.
- A mouse entering the field starts a wave at the pointer; while hovering,
  movement of at least 24 px, at most every 650 ms, starts another at the
  current position. Rings keep their original centers. Leaving resumes
  random origins. After a resize the stale coordinates are dropped and the
  next move re-establishes the origin from the new bounds.
- On a phone a short single-finger tap (under 300 ms, under 10 px of
  movement at any point in the gesture) starts a wave at the tap. Scroll
  and pinch are never prevented (`touch-action: auto`); a drag, an
  out-and-back drag, a cancelled gesture or a second finger starts nothing.
- A wave is three rings 230 ms apart, 2.5–3.2 s each, at most two waves
  (six rings) at once; an intentional wave at the cap retires the oldest.
  Opacity and transform only; the rings are DOM nodes created after mount
  in an `aria-hidden` layer with no dependency, canvas or library.
- Nothing runs while the field is mostly offscreen, the tab is hidden, the
  visitor has paused motion, or the OS asks for reduced motion (checked at
  mount and on change). Unmount tears everything down.
- The footer carries one control (`MotionToggle.tsx`): an ordinary action
  button whose label names the next action, "Pause motion" while running
  and "Resume motion" while paused, with no pressed state. The choice is
  kept for the visit in sessionStorage with an in-memory fallback when
  storage is blocked. OS reduced motion outranks it.

Ordinary control feedback remains: link and button color on hover and
focus (160ms), the focus ring, the disclosure chevron and the mobile menu.
No element translates, scales or fades on interaction.

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

## Brand mark and share card

Finalized in Claude Design after the reference package: an open release
frame read as a bold geometric C, paper on oxide, `rx` 14 on a 64-unit
square, the same geometry at every size. The stroke's visible bounds run
roughly x 11.5–48.5 / y 11.5–52.5; the mark is not inset for maskable
icons and is not declared maskable. The 1200 × 630 link-preview card
(`SHARE_VERSION` 2026-09-2, chosen by Codex review from two candidates) is
an oxide color block: one flush-left column on an 80 px margin with the
lockup (mark 60 px, wordmark 48 px), the headline "Deploy AI models / to
real robots." in Plex Sans 500 at 86 px with normal tracking, and a
subordinate domain line, all in paper (5.77:1); the right third carries the
open release frame as a bold cream stroke at 24 % opacity, right-aligned on
the margin and clear of the words. No descriptor, rule, grid or diagram.
Sources: `src/assets/brand/`; renderer: `scripts/build-brand.mjs`.
