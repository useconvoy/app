import * as React from "react";

/** Numbered three-up workflow. Stacks vertically below 900px in order. `WorkflowSequence` is an alias. */
export interface WorkflowStepsProps {
  steps: { title: string; body: React.ReactNode; items?: string[]; /** Optional small mono suffix after the number. */ tag?: string; /** Optional secondary detail rendered as a compact Disclosure. */ detail?: { summary: string; body: React.ReactNode } }[];
}
export declare function WorkflowSteps(props: WorkflowStepsProps): JSX.Element;
export declare const WorkflowSequence: typeof WorkflowSteps;
