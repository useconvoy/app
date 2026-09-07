Notice is an in-flow status line for preview disclaimers and form-level messages.

```jsx
<Notice tone="info">Preview: this form is not connected. Nothing is sent.</Notice>
<Notice tone="error">Fix the three highlighted fields.</Notice>
```

- Never fake success. A success Notice is only rendered by a real backend response.
