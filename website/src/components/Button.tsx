import type { ButtonHTMLAttributes } from "react";

export type ButtonVariant = "primary" | "secondary" | "danger";

const variantClasses: Record<ButtonVariant, string> = {
  primary: "border-pine bg-pine text-card-alt hover:bg-pine-deep",
  secondary: "border-line bg-card text-ink hover:bg-field",
  danger: "border-fail bg-fail text-card-alt",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  /**
   * The action is in flight. Disables the button, marks it busy for assistive
   * technology, and shows a spinner. Callers that already track their own
   * pending flag should pass it here rather than to `disabled`, so the state
   * is announced as well as enforced.
   */
  pending?: boolean;
}

/**
 * Two details carry most of the feel here.
 *
 * The label stays in the DOM while the button is busy and is only hidden, so
 * the button keeps its width and the row it sits in never reflows at the
 * moment of the click.
 *
 * The spinner fades in 200ms after it mounts, so a fast round trip never
 * flashes one. That delay is an animation-delay rather than a timer, which
 * keeps this a server component: a setTimeout here would draw a client
 * boundary through every page that renders a button.
 */
export function Button({
  variant = "primary",
  className,
  type,
  pending = false,
  disabled,
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type ?? "button"}
      disabled={disabled || pending}
      aria-busy={pending || undefined}
      className={[
        "relative inline-flex items-center justify-center gap-1.5 rounded-md border px-3 py-1.5 text-sm font-medium",
        "disabled:cursor-not-allowed disabled:opacity-50",
        pending ? "cursor-progress disabled:opacity-100" : "",
        variantClasses[variant],
        className ?? "",
      ].join(" ")}
      {...rest}
    >
      <span className={pending ? "invisible" : undefined}>{children}</span>
      {pending ? (
        <span
          aria-hidden="true"
          className="spinner-delayed absolute h-3.5 w-3.5 rounded-full border-2 border-current border-t-transparent"
        />
      ) : null}
    </button>
  );
}
