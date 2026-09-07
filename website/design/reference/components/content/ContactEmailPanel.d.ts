/**
 * Direct-email contact panel: selectable address, mailto link with subject, "opens your email application" note, optional guidance list. Replaces the retired preview form. No backend, no sent/success state.
 */
export interface ContactEmailPanelProps {
  /** Receiver. Default "aws@deployconvoy.com" — change here (or pass a constant) to retarget the whole site. */
  email?: string;
  /** mailto subject. Default "Robot deployment — Convoy". */
  subject?: string;
  /** Default "Email us about your deployment". */
  linkLabel?: string;
  /** Default "Opens your email application." */
  note?: string;
  guideTitle?: string;
  /** Bulleted guidance; pass [] to hide. */
  guide?: string[];
  addressLabel?: string;
}
export declare function ContactEmailPanel(props: ContactEmailPanelProps): JSX.Element;
