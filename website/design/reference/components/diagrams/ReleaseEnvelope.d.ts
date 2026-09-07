import * as React from "react";

/**
 * The recurring brand device: a versioned, dashed-oxide envelope grouping the five parts of a release.
 * @startingPoint section="Diagrams" subtitle="Versioned release envelope with the five default groups" viewport="700x420"
 */
export interface ReleaseEnvelopeProps {
  /** Mono tag text, e.g. "release v0.3". */
  version?: string;
  /** Muted suffix after the version. Default "example" — keep it unless the data is real. */
  label?: string;
  groups?: { key?: string; title: string; note?: string; items?: string[]; wide?: boolean; evidence?: boolean }[];
  legend?: boolean;
  caption?: string;
}
export declare function ReleaseEnvelope(props: ReleaseEnvelopeProps): JSX.Element;
