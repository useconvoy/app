PageShell wraps a page (header / main / footer); Section is the only section container — 1280px max, page padding, section gap, optional rule and 5/7 grid.

```jsx
<PageShell header={<SiteHeader … />} footer={<SiteFooter … />}>
  <Section id="how" labelledBy="how-title" grid="split">…two children…</Section>
</PageShell>
```

- `grid="split"` puts heading left, content right; stacks below 900px. `rule={false}` for the hero.
