import * as React from "react";

/** Inline status message (preview notice, form-level error). Not a toast; it stays in flow. */
export interface NoticeProps {
  tone?: "neutral" | "info" | "warning" | "error" | "success";
  children: React.ReactNode;
  role?: string;
}
export declare function Notice(props: NoticeProps): JSX.Element;
