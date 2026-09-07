import * as React from "react";

/** Single consent/option checkbox with inline label. */
export interface CheckboxProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label: React.ReactNode;
}
export declare function Checkbox(props: CheckboxProps): JSX.Element;
