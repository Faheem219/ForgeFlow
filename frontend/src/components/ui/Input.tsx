import { forwardRef, useId } from 'react';
import type { InputHTMLAttributes, ReactNode, TextareaHTMLAttributes } from 'react';

import { fieldClasses } from './fieldStyles';

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  hint?: ReactNode;
  error?: string;
  /** Rendered inside the field on the leading edge (search glyph, prefix). */
  leading?: ReactNode;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { label, hint, error, leading, className = '', id, ...props },
  ref,
) {
  const autoId = useId();
  const fieldId = id ?? autoId;
  const describedBy = error ? `${fieldId}-error` : hint ? `${fieldId}-hint` : undefined;

  const field = (
    <div className="relative">
      {leading ? (
        <span
          aria-hidden
          className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-faint"
        >
          {leading}
        </span>
      ) : null}
      <input
        ref={ref}
        id={fieldId}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        className={`${fieldClasses} h-8 text-sm ${leading ? 'pl-8 pr-2.5' : 'px-2.5'} ${
          error ? 'border-danger' : ''
        } ${className}`}
        {...props}
      />
    </div>
  );

  if (!label && !hint && !error) return field;

  return (
    <div className="space-y-1.5">
      {label ? (
        <label className="block text-xs font-medium text-secondary" htmlFor={fieldId}>
          {label}
        </label>
      ) : null}
      {field}
      {error ? (
        <p id={`${fieldId}-error`} className="text-xs text-danger">
          {error}
        </p>
      ) : hint ? (
        <p id={`${fieldId}-hint`} className="text-xs text-muted">
          {hint}
        </p>
      ) : null}
    </div>
  );
});

export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string;
  hint?: ReactNode;
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { label, hint, className = '', id, ...props },
  ref,
) {
  const autoId = useId();
  const fieldId = id ?? autoId;
  const field = (
    <textarea
      ref={ref}
      id={fieldId}
      className={`${fieldClasses} resize-none px-2.5 py-2 text-sm leading-relaxed ${className}`}
      {...props}
    />
  );
  if (!label && !hint) return field;
  return (
    <div className="space-y-1.5">
      {label ? (
        <label className="block text-xs font-medium text-secondary" htmlFor={fieldId}>
          {label}
        </label>
      ) : null}
      {field}
      {hint ? <p className="text-xs text-muted">{hint}</p> : null}
    </div>
  );
});
