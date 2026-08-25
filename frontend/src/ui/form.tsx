import { useId } from "react";
import type { ReactNode } from "react";

/** Form fields, with their labels actually associated.
 *
 *  Most fields in the app were `<label>Name</label><input …>` — adjacent, not
 *  associated. Clicking the label did nothing, and a screen reader announced an
 *  unlabelled text box. `useId` makes the connection automatic, so it cannot be
 *  forgotten the next time a field is added.
 */

export function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string;
  /** Guidance under the control. */
  hint?: ReactNode;
  error?: string | null;
  /** Receives the id to put on the control. */
  children: (id: string) => ReactNode;
}) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;

  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {children(id)}
      {hint && (
        <span className="field-hint" id={hintId}>
          {hint}
        </span>
      )}
      {error && (
        <span className="field-error" id={errorId} role="alert">
          {error}
        </span>
      )}
    </div>
  );
}

/** A labelled text input — the common case, without the callback. */
export function TextField({
  label,
  value,
  onChange,
  placeholder,
  hint,
  error,
  type = "text",
  disabled,
  ...rest
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  hint?: ReactNode;
  error?: string | null;
  type?: string;
  disabled?: boolean;
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "type">) {
  return (
    <Field label={label} hint={hint} error={error}>
      {(id) => (
        <input
          id={id}
          type={type}
          value={value}
          placeholder={placeholder}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
          {...rest}
        />
      )}
    </Field>
  );
}

export function SelectField<T extends string>({
  label,
  value,
  onChange,
  options,
  hint,
  disabled,
}: {
  label: string;
  value: T | "";
  onChange: (value: T) => void;
  options: readonly { value: T; label: string }[];
  hint?: ReactNode;
  disabled?: boolean;
}) {
  return (
    <Field label={label} hint={hint}>
      {(id) => (
        <select
          id={id}
          value={value}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value as T)}
        >
          {options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      )}
    </Field>
  );
}
