import React from "react";
import { Button } from "../actions/Button.jsx";

/** Receiver is configured in ONE place: the `email` prop (landing page passes CONTACT_EMAIL). No form, no backend, no sent state. */
export function ContactEmailPanel({
  email = "aws@deployconvoy.com",
  subject = "Robot deployment — Convoy",
  linkLabel = "Email us about your deployment",
  note = "Opens your email application.",
  guideTitle = "Helpful to include",
  guide = ["The model you want to deploy", "Your robot configuration", "The deployment challenge you’re working through"],
  addressLabel = "Email",
}) {
  const href = `mailto:${email}${subject ? `?subject=${encodeURIComponent(subject)}` : ""}`;
  return (
    <div className="cv-panel cv-email">
      <div className="cv-email__address">
        <span className="cv-email__label">{addressLabel}</span>
        <span className="cv-email__value"><a href={href}>{email}</a></span>
      </div>
      <div>
        <Button href={href} arrow>{linkLabel}</Button>
        <p className="cv-email__note" style={{ marginTop: "var(--space-8)" }}>{note}</p>
      </div>
      {guide && guide.length > 0 && (
        <div>
          <p className="cv-email__guide-title">{guideTitle}</p>
          <ul className="cv-email__guide">{guide.map((g) => <li key={g}>{g}</li>)}</ul>
        </div>
      )}
    </div>
  );
}
