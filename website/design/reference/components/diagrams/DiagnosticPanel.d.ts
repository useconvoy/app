import * as React from "react";

/**
 * The one permitted dark panel: a mono list of conceptual release-contract fields. Never a terminal, never a real API.
 * @startingPoint section="Diagrams" subtitle="Dark conceptual release-contract panel" viewport="700x360"
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
