Button is Convoy's single action primitive: oxide primary (one per view), outlined secondary, low-emphasis text, and an inverse variant for dark panels.

```jsx
<Button variant="primary" arrow href="#contact">Discuss your deployment</Button>
<Button variant="secondary" href="#how">Explore the workflow</Button>
<Button variant="text" size="sm">Reset</Button>
```

- `variant`: primary | secondary | text | inverse. Never put two primaries side by side.
- `size`: md (48px, default) | sm (40px, dense UI only).
- `arrow`: trailing arrow, reserved for the page's main CTA.
- `href` renders an anchor with identical styling; `disabled` drops opacity to .45 and blocks the pointer.
- Copy: sentence case, verb first ("Discuss your deployment"), never "Submit" or "Click here".
