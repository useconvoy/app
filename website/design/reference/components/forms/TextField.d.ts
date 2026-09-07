import * as React from "react";

/**
 * Single-line text input with label, optional marker, hint and inline error. Required by default; pass `optional` to relax.
 * @startingPoint section="Forms" subtitle="Labelled input with hint, error, disabled and focus states" viewport="700x340"
 */
export interface TextFieldProps extends React.InputHTMLAttributes<HTMLInputElement> {
  id?: string;
  label: string;
  /** Shows an "Optional" marker and drops the required attribute. */
  optional?: boolean;
  hint?: string;
  /** Inline error text; also sets aria-invalid and swaps the border to the error colour. */
  error?: string;
}
export declare function TextField(props: TextFieldProps): JSX.Element;
