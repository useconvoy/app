ReleaseEnvelope is Convoy's signature diagram: one versioned dashed envelope grouping Model assets, Input and action processing, Runtime, Target configuration and Qualification evidence.

```jsx
<ReleaseEnvelope />
<ReleaseEnvelope version="release v1.2" label="example" legend={false} />
```

- Defaults carry the canonical five groups with conceptual item labels; override `groups` only to change wording, never to add products.
- Keep the `label` suffix ("example") whenever contents are illustrative.
- The controller and safety system are never drawn inside the envelope.
