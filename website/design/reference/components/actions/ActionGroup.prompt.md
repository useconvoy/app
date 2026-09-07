ActionGroup arranges the CTA pair: inline on desktop, stacked full-width on mobile.

```jsx
<ActionGroup>
  <Button arrow href="#contact">Discuss your deployment</Button>
  <Button variant="secondary" href="#how">Explore the workflow</Button>
</ActionGroup>
```

- One primary per view. Buttons trigger actions; destinations (including mailto:) are `<Button href>` rendered as `<a>`.
