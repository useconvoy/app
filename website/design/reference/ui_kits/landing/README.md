# Landing page UI kit — deployconvoy.com

Reference implementation of the Convoy landing page composed entirely from design-system components.

- `index.html` — the page (React + bundle). Sections: header, hero + release envelope, dependency rows, proposed workflow + diagnostic panel, execution path, design partnership, boundaries FAQ, contact form (preview only), footer.
- `LandingPage.jsx` — section components (`Hero`, `Gap`, `How`, `Path`, `Partners`, `Boundaries`, `Contact`) and the client-side validation of the preview form.
- `responsive.html` — the same page framed at 1440, 768, 390 and 320px.

Page-level layout classes (`lp-*`) live in `index.html`; they are the only CSS not in `styles.css`. Copy them into the production stylesheet or convert to the app's layout primitives.

Content rules: hero, lead, CTA labels and stage line are verbatim from the brief. Everything about the workflow is marked proposed. The contact form validates but sends nothing; the notice says so.
