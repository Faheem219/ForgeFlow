import type { InputHTMLAttributes } from 'react';

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
}

export function Input({ label, className = '', id, ...props }: InputProps): JSX.Element {
  const field = (
    <input
      id={id}
      className={`w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-brand-500 ${className}`}
      {...props}
    />
  );
  if (!label) {
    return field;
  }
  return (
    <label className="block text-sm" htmlFor={id}>
      <span className="mb-1 block text-slate-300">{label}</span>
      {field}
    </label>
  );
}
