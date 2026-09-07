ReleaseComposition is the complete hero figure: what enters the release (trained model), the release itself (ReleaseEnvelope), and what stays outside (robot, controller, safety).

```jsx
<ReleaseComposition />                         // vertical (default) — use inside a column such as the 7/12 hero split
<ReleaseComposition orientation="horizontal" /> // full-width figures only (≥900px; stacks below)
```

- Horizontal needs roughly ≥820px of container width so the envelope's two internal columns stay readable; never place it inside a split.
- Dashed oxide arrow into the envelope (release), solid arrow out (execution). Ownership labels sit beside the outside nodes in the vertical variant.
- Ships a visually hidden relationship description for screen readers; the caption is the single conceptual-architecture sentence.
