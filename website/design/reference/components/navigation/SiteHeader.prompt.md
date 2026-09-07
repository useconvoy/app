SiteHeader is the single top navigation: Convoy wordmark (no logo exists), three anchor links, one primary CTA, and a disclosure-style mobile menu below 900px.

```jsx
<SiteHeader
  links={[{label:"How it works",href:"#how"},{label:"Design partnership",href:"#partners"},{label:"Contact",href:"#contact"}]}
  cta={{label:"Discuss your deployment",href:"#contact"}} current="#how" />
```

- Links are ordinary `<a href="#…">`; no ARIA menu roles. Toggle is a 48px `<button aria-expanded aria-controls>`.
- Escape closes and restores focus to the toggle; clicking a link closes; pointer-down outside closes.
- Sticky by default; `[id]{scroll-margin-top}` in base.css keeps anchor targets and focused elements clear of it.
- Never add links to docs, pricing or demos that don't exist. No internal tags like "Precision Release" in public UI.
