import { type InputHTMLAttributes, useId } from "react";

export type TextFieldProps = InputHTMLAttributes<HTMLInputElement> & {
  label: string;
  helperText?: string;
  error?: string;
};

export function TextField({
  "aria-describedby": ariaDescribedBy,
  "aria-invalid": ariaInvalid,
  className,
  error,
  helperText,
  id,
  label,
  ...props
}: TextFieldProps) {
  const generatedId = useId();
  const inputId = id ?? `text-field-${generatedId}`;
  const helperId = helperText ? `${inputId}-help` : undefined;
  const errorId = error ? `${inputId}-error` : undefined;
  const describedBy = [ariaDescribedBy, helperId, errorId].filter(Boolean).join(" ");

  return (
    <div className="text-field">
      <label htmlFor={inputId}>{label}</label>
      <input
        {...props}
        id={inputId}
        className={["text-field__input", className].filter(Boolean).join(" ")}
        aria-invalid={error ? true : ariaInvalid}
        aria-describedby={describedBy || undefined}
      />
      {helperText ? (
        <p id={helperId} className="text-field__helper">
          {helperText}
        </p>
      ) : null}
      {error ? (
        <p id={errorId} className="text-field__error" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
