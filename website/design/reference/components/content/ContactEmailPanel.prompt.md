ContactEmailPanel is the launch contact surface: a visible, selectable address and a mailto link. Nothing is sent from the site.

```jsx
<ContactEmailPanel email={CONTACT_EMAIL} />
```

- The address is both text (user-select: all) and a mailto link; the primary button uses the same href with a subject.
- Keep the "Opens your email application." note. Never add a success/sent state, a provider, or a form.
- Change the receiver via the `email` prop only.
