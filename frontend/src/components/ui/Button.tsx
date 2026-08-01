import { forwardRef } from 'react';
import type { ButtonHTMLAttributes, ReactNode } from 'react';

import { Spinner } from './Spinner';

/**
 * The one button.
 *
 * Two deliberate choices:
 *  - **Active state is a scale-down, not a colour swap.** Physical feedback reads as responsive at
 *    any latency; a colour change reads as a state change and is easily confused with "selected".
 *  - **`loading` keeps the label.** Swapping the label for a lone spinner destroys the button's
 *    width and the user's read of what they pressed; the spinner joins the label instead.
 */

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'subtle';
type Size = 'sm' | 'md' | 'icon';

const variants: Record<Variant, string> = {
  primary:
    'border border-transparent bg-brand text-brand-fg shadow-pop hover:bg-brand-hover active:shadow-none',
  secondary:
    'border border-line bg-surface text-primary hover:border-emphasis hover:bg-elevated active:bg-elevated',
  ghost: 'border border-transparent text-secondary hover:bg-elevated hover:text-primary',
  danger: 'border border-transparent bg-danger text-white shadow-pop hover:opacity-90',
  subtle: 'border border-transparent bg-elevated text-secondary hover:text-primary',
};

const sizes: Record<Size, string> = {
  sm: 'h-7 gap-1.5 px-2.5 text-xs',
  md: 'h-8 gap-2 px-3 text-sm',
  icon: 'h-8 w-8 justify-center',
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  children: ReactNode;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = 'primary',
    size = 'md',
    loading = false,
    disabled,
    className = '',
    children,
    ...props
  },
  ref,
) {
  return (
    <button
      ref={ref}
      type="button"
      disabled={disabled ?? loading}
      data-loading={loading || undefined}
      className={[
        'inline-flex select-none items-center whitespace-nowrap rounded-md font-medium',
        'transition-[background-color,border-color,color,transform,box-shadow,opacity] duration-fast ease-out',
        'active:scale-[0.98]',
        'disabled:pointer-events-none disabled:opacity-45 disabled:active:scale-100',
        variants[variant],
        sizes[size],
        className,
      ].join(' ')}
      {...props}
    >
      {loading ? <Spinner size="sm" /> : null}
      {children}
    </button>
  );
});
