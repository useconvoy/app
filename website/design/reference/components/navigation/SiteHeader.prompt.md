SiteHeader is the single top navigation for Convoy pages: plain wordmark (no logo exists yet), three anchor links, one primary CTA, and a native-button mobile menu below 900px.

```jsx
<SiteHeader
  links={[{label:"How it works",href:"#how"},{label:"Design partnership",href:"#partners"},{label:"Contact",href:"#contact"}]}
  cta={{label:"Discuss your deployment",href:"#contact"}} />
```

- Never add links to docs, pricing or demos that don't exist.
- Mobile menu is a real `<nav hidden>` toggled by a 48px button with `aria-expanded`; Escape closes it.
