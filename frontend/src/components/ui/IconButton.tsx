import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react';

import { cn } from '../../lib/cn';
import { Tooltip } from './Misc';

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** The accessible name and the tooltip — an icon-only control must always carry both. */
  label: string;
  children: ReactNode;
  size?: 'xs' | 'sm' | 'md';
  /** Pressed/selected state (toggles): fills the button quietly instead of colouring it. */
  active?: boolean;
  tooltipSide?: 'top' | 'bottom' | 'right';
  tooltipAlign?: 'start' | 'center' | 'end';
  /** Suppress the tooltip where a visible label already says the same thing. */
  noTooltip?: boolean;
}

const sizes = {
  xs: 'h-6 w-6 rounded-md [&_svg]:h-3.5 [&_svg]:w-3.5',
  sm: 'h-7 w-7 rounded-md [&_svg]:h-4 [&_svg]:w-4',
  md: 'h-8 w-8 rounded-lg [&_svg]:h-[18px] [&_svg]:w-[18px]',
} as const;

/** A square, icon-only button for toolbars and pane headers. */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  {
    label,
    children,
    size = 'sm',
    active = false,
    tooltipSide = 'bottom',
    tooltipAlign = 'center',
    noTooltip = false,
    className,
    ...props
  },
  ref,
) {
  const button = (
    <button
      ref={ref}
      type="button"
      aria-label={label}
      className={cn(
        'inline-flex shrink-0 items-center justify-center text-fg-subtle transition-colors duration-150',
        'hover:bg-surface-raised hover:text-fg disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent',
        active && 'bg-surface-raised text-fg',
        sizes[size],
        className,
      )}
      {...props}
    >
      {children}
    </button>
  );
  if (noTooltip) return button;
  return (
    <Tooltip label={label} side={tooltipSide} align={tooltipAlign}>
      {button}
    </Tooltip>
  );
});
