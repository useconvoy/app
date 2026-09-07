import * as React from "react";

/**
 * Numbered three-up workflow (Package / Qualify / Release). Stacks vertically below 900px.
 * @startingPoint section="Content" subtitle="Numbered steps with title, body and dash list" viewport="700x320"
 */
export interface WorkflowStepsProps {
  steps: { title: string; body: React.ReactNode; items?: string[]; /** Small mono suffix after the number, e.g. "proposed". */ tag?: string }[];
}
export declare function WorkflowSteps(props: WorkflowStepsProps): JSX.Element;
