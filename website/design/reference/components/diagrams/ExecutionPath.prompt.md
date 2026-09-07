ExecutionPath draws the robot's execution path and shows where Convoy's runtime sits without claiming the controller or safety system.

```jsx
<ExecutionPath traceable />
<ExecutionPath scope="" legend={false} />
```

- Defaults are canonical; external nodes (Sensors, Robot controller) render dashed and stay outside the scope bracket.
- Dotted site labels mark configuration-dependent placement (local · site · cloud).
- `traceable` is the only motion in the system beyond hover: user-triggered, 520ms per node, static toggle under reduced motion.
