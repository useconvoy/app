import * as React from "react";

/**
 * Convoy action button. Primary = oxide fill, one per view. Secondary = outlined. Text = inline, low emphasis. Inverse for the dark diagnostic panel only.
 * @startingPoint section="Actions" subtitle="Primary, secondary, text and inverse buttons with focus, hover and disabled states" viewport="700x260"
 */
export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  /** Visual emphasis. Default "primary". */
  variant?: "primary" | "secondary" | "text" | "inverse";
  /** md = 48px control height (default); sm = 40px. */
  size?: "md" | "sm";
  /** Full-width. */
  block?: boolean;
  /** Append a trailing arrow glyph (use on the primary CTA only). */
  arrow?: boolean;
  /** Renders an <a> instead of <button>. */
  href?: string;
  disabled?: boolean;
  children: React.ReactNode;
}
export declare function Button(props: ButtonProps): JSX.Element;
