ReleaseEnvelope is Convoy's signature diagram: one dashed envelope grouping Release identity, Model assets, Input and action processing, Runtime, Target configuration and Evaluation evidence.

```jsx
<ReleaseEnvelope title="The robot-policy release" />
<ReleaseEnvelope caption="" />   // when the caption is provided by the surrounding composition
```

- No version numbers, test counts, rates, pass results or API-like fields. Item labels are conceptual categories only.
- The robot controller and safety system are never drawn inside the envelope (see ReleaseComposition for the outside context).
- Labels 16px, metadata 14px; on mobile the grid stacks, labels never shrink.
