import * as React from "react";

/**
 * Dark conceptual release-contents panel (categories only). Documented; not used on the launch landing page.
 */
export interface DiagnosticPanelProps {
  title?: string;
  /** Right-hand head label. Default "not an API". */
  status?: string;
  /** Group rows ({group}) and field rows ({k, v, t}); t = s (string, green) | n (value, blue) | m (muted). */
  rows?: ({ group: string } | { k: string; v: React.ReactNode; t?: "s" | "n" | "m" })[];
  footer?: string;
}
export declare function DiagnosticPanel(props: DiagnosticPanelProps): JSX.Element;
