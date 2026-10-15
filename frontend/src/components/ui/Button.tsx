import type { ButtonHTMLAttributes, ReactNode } from 'react';

import { cn } from '../../lib/cn';
import { Spinner } from './Spinner';

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger';
type Size = 'xs' | 'sm' | 'md' | 'lg';

/*
 * Colour is reserved for the one action on a surface that moves work forward. Everything else is
 * neutral — a toolbar of six brand-filled buttons tells the eye nothing about which one matters.
 */
const variants: Record<Variant, string> = {
  // Disabled primary goes neutral rather than dim-indigo: a faded brand fill still reads as "the
  // thing to press", which is exactly what a disabled button is not.
  primary:
    'border border-transparent bg-brand text-fg-inverted shadow-raise hover:bg-brand-hover ' +
    'disabled:bg-fg/[0.07] disabled:text-fg-faint disabled:shadow-none disabled:opacity-100',
  secondary:
    'border border-edge-strong/80 bg-surface text-fg shadow-raise ' +
    'hover:border-edge-strong hover:bg-surface-raised disabled:hover:bg-surface',
  ghost:
    'border border-transparent text-fg-muted hover:bg-surface-raised hover:text-fg ' +
    'disabled:hover:bg-transparent',
  danger:
    'border border-danger/25 bg-danger/10 text-danger hover:border-danger/45 hover:bg-danger/15',
};

const sizes: Record<Size, string> = {
  xs: 'h-6 gap-1 rounded-md px-2 text-xs',
  sm: 'h-7 gap-1.5 rounded-md px-2.5 text-xs',
  md: 'h-8 gap-2 rounded-lg px-3 text-sm',
  lg: 'h-10 gap-2 rounded-lg px-4 text-sm',
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  children: ReactNode;
}

export function Button({
  variant = 'primary',
  size = 'md',
  loading = false,
  disabled,
  className,
  children,
  ...props
}: ButtonProps): JSX.Element {
  return (
    <button
      type="button"
      disabled={disabled ?? loading}
      className={cn(
        'inline-flex shrink-0 select-none items-center justify-center whitespace-nowrap font-medium',
        'transition-[background-color,border-color,color,box-shadow,transform] duration-150 ease-out',
        'active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-45 disabled:active:scale-100',
        '[&_svg]:shrink-0',
        variants[variant],
        sizes[size],
        className,
      )}
      {...props}
    >
      {loading ? <Spinner size="sm" /> : null}
      {children}
    </button>
  );
}
