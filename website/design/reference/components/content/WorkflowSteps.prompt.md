WorkflowSteps renders the proposed Package → Qualify → Release sequence as numbered, rule-topped columns.

```jsx
<WorkflowSteps steps={[
  {title:"Package", tag:"proposed", body:"Group model assets, processing, runtime and target configuration in one versioned envelope.", items:["Model assets","Input and action processing"]},
  {title:"Qualify", tag:"proposed", body:"Run the envelope against the target configuration and record evidence."},
  {title:"Release", tag:"proposed", body:"Promote a qualified envelope; roll back to a previous one."},
]} />
```

- Always mark steps that are not shipped with `tag:"proposed"`.
