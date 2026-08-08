import type { ButtonHTMLAttributes } from "react";

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  hierarchy?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md" | "lg";
  loading?: boolean;
};

export function Button({
  "aria-busy": ariaBusy,
  children,
  className,
  disabled,
  hierarchy = "primary",
  loading = false,
  size = "md",
  type,
  ...props
}: ButtonProps) {
  const classes = [
    "button",
    `button--${hierarchy}`,
    `button--${size}`,
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <button
      {...props}
      type={type ?? "button"}
      className={classes}
      disabled={disabled || loading}
      aria-busy={loading ? true : ariaBusy}
      data-loading={loading || undefined}
    >
      {loading ? <span className="button__spinner" aria-hidden="true" /> : null}
      <span>{children}</span>
    </button>
  );
}
