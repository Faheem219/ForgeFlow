import type { InputHTMLAttributes } from 'react';

import { cn } from '../../lib/cn';
import { fieldClasses } from './fieldStyles';

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  hint?: string;
}

export function Input({ label, hint, className, id, ...props }: InputProps): JSX.Element {
  const field = <input id={id} className={cn(fieldClasses, className)} {...props} />;
  if (!label) {
    return field;
  }
  return (
    <label className="block text-sm" htmlFor={id}>
      <span className="mb-1.5 block text-[13px] font-medium text-fg-muted">{label}</span>
      {field}
      {hint ? <span className="mt-1.5 block text-xs text-fg-subtle">{hint}</span> : null}
    </label>
  );
}
