WorkflowSteps (alias WorkflowSequence) renders the Package → Qualify → Release sequence as numbered, rule-topped columns that stack on mobile.

```jsx
<WorkflowSteps steps={[
  {title:"Package", body:"Bring the model, input and action processing, runtime dependencies, and target configuration together."},
  {title:"Qualify", body:"Check that release against defined task and runtime criteria. Keep the conditions and results connected to the configuration that was tested."},
  {title:"Release", body:"Carry the identified configuration into deployment, with a clear basis for observing behavior and managing subsequent changes."},
]} />
```

- Approved launch copy above. No "proposed" tags; stage is communicated once, in the availability FAQ.
- `detail` adds a compact Disclosure per step for secondary input/output detail; unused on the launch page.
