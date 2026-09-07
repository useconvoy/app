import * as React from "react";

/**
 * Numbered aligned rows: a term column and one description column (`body`), or two (`model` / `deployment`). Stacks below 900px.
 * @startingPoint section="Content" subtitle="Numbered aligned dependency rows" viewport="700x360"
 */
export interface DependencyRowsProps {
  /** Header labels, first usually empty. Omit for no header; for single-column rows pass ["", "<label>"] to show one. */
  columns?: string[];
  rows: { term: string; body?: React.ReactNode; model?: React.ReactNode; deployment?: React.ReactNode }[];
}
export declare function DependencyRows(props: DependencyRowsProps): JSX.Element;
