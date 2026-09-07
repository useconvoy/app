import * as React from "react";

/** Multi-line text entry with the same label/hint/error contract as TextField. */
export interface TextAreaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  id?: string;
  label: string;
  optional?: boolean;
  hint?: string;
  error?: string;
  rows?: number;
}
export declare function TextArea(props: TextAreaProps): JSX.Element;
