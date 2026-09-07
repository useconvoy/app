DependencyRows lays out the "a trained model is not a robot deployment" argument as four numbered, aligned rows.

```jsx
<DependencyRows rows={[
  {term:"Inputs", body:"Images, sensor data, and robot state prepared the way the model expects."},
  {term:"Actions", body:"Model outputs translated into the units, coordinates, and commands the controller understands."},
  {term:"Execution", body:"The runtime, dependencies, hardware settings, and timing required by the deployment."},
  {term:"Release evidence", body:"A record of the configuration, conditions, and checks used to evaluate the system."},
]} />
```

- Approved landing copy uses the single `body` column above (Part XIII). A two-column form (`model` / `deployment`) exists for internal comparisons; never describe inputs as something the model "gives".
- Terms are single nouns; cells one plain sentence. Hairline rows, strong top rule, no card boxes.
