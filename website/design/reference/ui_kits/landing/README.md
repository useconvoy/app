# Landing page UI kit — deployconvoy.com

Reference implementation of the Convoy landing page composed from design-system components.

- `index.html` — responsive reference (React + compiled bundle). Append `?w=768|390|320` to force breakpoint styles at any preview width (design-review simulation, not a real viewport).
- `Desktop-1440.html` / `Mobile-390.html` — the page inside a real 1440px / 390px iframe so media queries evaluate at that width; open in a browser to review full pages unscaled (desktop scales to fit narrower windows).
- `state-board.html` — paired desktop / mobile specimens for every responsive component with closed/open/focus/hover/pressed/current/long-label states.
- `LandingPage.jsx` — sections (`Hero`, `Gap`, `How`, `Path`, `Partners`, `Faq`, `Contact`) and `CONTACT_EMAIL`, the single place the receiver is set.
- `landing.css` — the only page-level layout CSS (`lp-*`).

Contact is a direct email panel (address + mailto to `CONTACT_EMAIL`). Hero uses the vertical `ReleaseComposition`; the horizontal variant is reserved for full-width figures.
