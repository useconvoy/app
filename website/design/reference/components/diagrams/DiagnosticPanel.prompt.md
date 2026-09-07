DiagnosticPanel is the single dark surface allowed on a Convoy page: a conceptual list of what a release must carry, in mono, with keyword/string/value colours.

```jsx
<DiagnosticPanel />
<DiagnosticPanel title="Target configuration · conceptual" rows={[{group:"target"},{k:"robot",v:"arm-a",t:"s"}]} />
```

- Use at most once per page. Keep the "not an API" status and the footer disclaimer unless the data is real.
- No prompts, no `$`, no commands, no fake responses.
