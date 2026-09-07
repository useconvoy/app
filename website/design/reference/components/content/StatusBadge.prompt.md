StatusBadge is a small mono chip that states a fact about status or scope (stage, proposed, preview, example).

```jsx
<StatusBadge tone="info">Stage: In development</StatusBadge>
<StatusBadge tone="warning">Proposed workflow</StatusBadge>
<StatusBadge tone="neutral" dot={false}>Conceptual example</StatusBadge>
```

- Tones map to the semantic status colours; `accent` is for release/evidence tags; `inverse` inside the dark diagnostic panel.
- Copy is a short noun phrase; never "New!" or promotional.
