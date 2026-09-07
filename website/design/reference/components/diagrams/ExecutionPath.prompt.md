ExecutionPath draws the robot's execution path and shows which nodes sit inside Convoy's runtime boundary without claiming the controller or safety system.

```jsx
<ExecutionPath />
<ExecutionPath traceable legend={false} />
```

- Order is fixed: Sensors → Input processing → Model → Action processing → Robot controller; vertical below 640px in the same order.
- The boundary is a real labelled group (`role="group"` + visible label) in both layouts; a visually hidden sentence lists the relationships.
- Dotted site labels = configuration-dependent placement. No rates, frequencies, or placement guarantees.
- `traceable` is optional; under reduced motion it toggles the complete highlighted state instead of animating.
