TextField is a labelled single-line input; use it for every short text/email entry in Convoy forms.

```jsx
<TextField label="Work email" type="email" placeholder="you@company.com" hint="We reply from a named engineer." />
<TextField label="Name" optional />
<TextField label="Work email" type="email" defaultValue="me@gmail" error="Use a work email address." />
```

- Required by default (label without marker). `optional` adds the muted "Optional" marker.
- `error` replaces the hint, sets `aria-invalid`, and uses `role="alert"`.
- Inputs are 48px tall, 16px text, 1px meaningful border; focus = oxide border + 3px tint ring.
