TextArea is the multi-line counterpart of TextField, used for the "deployment problem" field and any free-text answer.

```jsx
<TextArea label="What are you trying to deploy?" hint="Model, robot, and what 'done' looks like." rows={5} />
```

- Same `label`/`optional`/`hint`/`error` API as TextField; min height 144px, vertical resize only.
