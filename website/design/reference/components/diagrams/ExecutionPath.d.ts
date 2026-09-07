import * as React from "react";

/**
 * Sensors → Input processing → Model → Action processing → Robot controller, with placement labels and a dashed runtime-scope bracket. Vertical below 640px.
 * @startingPoint section="Diagrams" subtitle="Five-node execution path with placement labels and optional trace" viewport="700x300"
 */
export interface ExecutionPathProps {
  nodes?: { title: string; sub?: string; site?: string; external?: boolean; optional?: boolean; model?: boolean }[];
  /** Label of the dashed bracket under the middle nodes; "" hides it. */
  scope?: string;
  legend?: boolean;
  /** Adds a user-triggered "Trace execution" button that highlights nodes in order (static under reduced motion). */
  traceable?: boolean;
  caption?: string;
}
export declare function ExecutionPath(props: ExecutionPathProps): JSX.Element;
