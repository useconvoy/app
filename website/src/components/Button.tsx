import type { ButtonHTMLAttributes } from "react";

export type ButtonVariant = "primary" | "secondary" | "danger";

const variantClasses: Record<ButtonVariant, string> = {
  primary: "border-pine bg-pine text-card hover:bg-pine-deep",
  secondary: "border-line bg-card text-ink hover:bg-field",
  danger: "border-fail bg-fail text-card",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
}

export function Button({ variant = "primary", className, type, ...rest }: ButtonProps) {
  return (
    <button
      type={type ?? "button"}
      className={[
        "inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm font-medium",
        "disabled:cursor-not-allowed disabled:opacity-50",
        variantClasses[variant],
        className ?? "",
      ].join(" ")}
      {...rest}
    />
  );
}
