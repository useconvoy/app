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
