import * as React from "react";

/** Versioned-looking but value-free release envelope: identity group + five conceptual groups. Desktop 2-column grid; single column ≤640px. */
export interface ReleaseEnvelopeProps {
  /** Envelope tag. Default "Robot-policy release". */
  tag?: string;
  /** Identity group title. Default "Release identity"; "" hides the group. */
  identity?: string;
  identityNote?: string;
  groups?: { key?: string; title: string; note?: string; items?: string[]; wide?: boolean; evidence?: boolean }[];
  /** Figure caption (also the accessible description). Default is the approved conceptual-architecture sentence. */
  caption?: string;
  /** Small mono label above the figure, e.g. "The robot-policy release". */
  title?: string;
  describedBy?: string;
}
export declare function ReleaseEnvelope(props: ReleaseEnvelopeProps): JSX.Element;
export declare const RELEASE_ENVELOPE_CAPTION: string;
