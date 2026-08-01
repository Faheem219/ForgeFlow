import type { Config } from 'tailwindcss';

/**
 * ForgeFlow theme. Colours are bound to the CSS custom properties in `src/app/theme/tokens.css`
 * via `rgb(var(--x) / <alpha-value>)`, which is what lets a single class (`bg-surface`) resolve
 * per-theme AND still support Tailwind's alpha syntax (`bg-surface/40`).
 *
 * Consequence: feature code should use SEMANTIC names (`bg-surface`, `text-muted`, `border-subtle`)
 * and never raw palette names (`bg-slate-900`). A raw palette colour cannot follow the theme.
 */

const withAlpha = (variable: string) => `rgb(var(${variable}) / <alpha-value>)`;

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  // The pre-paint script in index.html stamps `data-theme` on <html>; `.dark` is kept so any
  // third-party CSS keyed to it (Monaco/xterm wrappers) still matches.
  darkMode: ['class', '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        canvas: withAlpha('--canvas'),
        surface: withAlpha('--surface'),
        elevated: withAlpha('--elevated'),
        overlay: withAlpha('--overlay'),
        inset: withAlpha('--inset'),

        subtle: withAlpha('--border-subtle'),
        line: withAlpha('--border-default'),
        emphasis: withAlpha('--border-strong'),

        primary: withAlpha('--text-primary'),
        secondary: withAlpha('--text-secondary'),
        muted: withAlpha('--text-muted'),
        faint: withAlpha('--text-faint'),
        inverted: withAlpha('--text-inverted'),

        brand: {
          DEFAULT: withAlpha('--brand'),
          hover: withAlpha('--brand-hover'),
          fg: withAlpha('--brand-fg'),
          text: withAlpha('--brand-text'),
          bg: withAlpha('--brand-bg'),
          border: withAlpha('--brand-border'),
        },
        accent: withAlpha('--accent'),

        success: {
          DEFAULT: withAlpha('--success'),
          bg: withAlpha('--success-bg'),
          border: withAlpha('--success-border'),
        },
        warning: {
          DEFAULT: withAlpha('--warning'),
          bg: withAlpha('--warning-bg'),
          border: withAlpha('--warning-border'),
        },
        danger: {
          DEFAULT: withAlpha('--danger'),
          bg: withAlpha('--danger-bg'),
          border: withAlpha('--danger-border'),
        },
        info: {
          DEFAULT: withAlpha('--info'),
          bg: withAlpha('--info-bg'),
          border: withAlpha('--info-border'),
        },

        ring: withAlpha('--ring'),
        scrim: withAlpha('--scrim'),
      },
      borderColor: {
        // Panels seam against each other constantly — make the hairline the default.
        DEFAULT: withAlpha('--border-subtle'),
      },
      fontFamily: {
        sans: ['var(--font-sans)'],
        mono: ['var(--font-mono)'],
      },
      fontSize: {
        // A denser bottom end than Tailwind's default: tool chrome lives at 11–13px.
        '2xs': ['0.6875rem', { lineHeight: '1rem', letterSpacing: '0.01em' }],
        xs: ['0.75rem', { lineHeight: '1.125rem' }],
        sm: ['0.8125rem', { lineHeight: '1.25rem' }],
        base: ['0.875rem', { lineHeight: '1.375rem' }],
        lg: ['1rem', { lineHeight: '1.5rem' }],
        xl: ['1.25rem', { lineHeight: '1.75rem', letterSpacing: '-0.011em' }],
        '2xl': ['1.5rem', { lineHeight: '2rem', letterSpacing: '-0.018em' }],
        '3xl': ['1.875rem', { lineHeight: '2.25rem', letterSpacing: '-0.022em' }],
      },
      borderRadius: {
        sm: '0.25rem',
        DEFAULT: '0.375rem',
        md: '0.5rem',
        lg: '0.625rem',
        xl: '0.75rem',
        '2xl': '1rem',
      },
      boxShadow: {
        // Shadow is reserved for things that genuinely float. Panels use borders instead.
        pop: '0 1px 2px rgb(var(--shadow-color) / calc(var(--shadow-strength) * 0.7)), 0 4px 12px -2px rgb(var(--shadow-color) / var(--shadow-strength))',
        overlay:
          '0 2px 4px rgb(var(--shadow-color) / calc(var(--shadow-strength) * 0.6)), 0 12px 32px -8px rgb(var(--shadow-color) / calc(var(--shadow-strength) * 1.6))',
      },
      transitionTimingFunction: {
        out: 'var(--ease-out)',
        smooth: 'var(--ease-in-out)',
        spring: 'var(--ease-spring)',
      },
      transitionDuration: {
        instant: 'var(--dur-instant)',
        fast: 'var(--dur-fast)',
        base: 'var(--dur-base)',
        slow: 'var(--dur-slow)',
      },
      keyframes: {
        'fade-in': {
          from: { opacity: '0' },
          to: { opacity: '1' },
        },
        'rise-in': {
          from: { opacity: '0', transform: 'translateY(6px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        'scale-in': {
          from: { opacity: '0', transform: 'scale(0.97)' },
          to: { opacity: '1', transform: 'scale(1)' },
        },
        'slide-up': {
          from: { opacity: '0', transform: 'translateY(10px) scale(0.985)' },
          to: { opacity: '1', transform: 'translateY(0) scale(1)' },
        },
        'slide-in-right': {
          from: { opacity: '0', transform: 'translateX(12px)' },
          to: { opacity: '1', transform: 'translateX(0)' },
        },
        /* A file landing from the agent flashes once, then settles — the "something arrived" cue. */
        'flash-in': {
          '0%': { backgroundColor: 'rgb(var(--brand) / 0.18)' },
          '100%': { backgroundColor: 'rgb(var(--brand) / 0)' },
        },
        /* Indeterminate progress for streams whose total is genuinely unknown. */
        shimmer: {
          '100%': { transform: 'translateX(100%)' },
        },
        'bar-drift': {
          '0%': { transform: 'translateX(-100%)' },
          '100%': { transform: 'translateX(400%)' },
        },
        'pulse-dot': {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.35' },
        },
      },
      animation: {
        'fade-in': 'fade-in var(--dur-base) var(--ease-out) both',
        'rise-in': 'rise-in var(--dur-base) var(--ease-out) both',
        'scale-in': 'scale-in var(--dur-fast) var(--ease-out) both',
        'slide-up': 'slide-up var(--dur-base) var(--ease-out) both',
        'slide-in-right': 'slide-in-right var(--dur-base) var(--ease-out) both',
        'flash-in': 'flash-in 900ms var(--ease-out) both',
        shimmer: 'shimmer 1.6s infinite',
        'bar-drift': 'bar-drift 1.4s var(--ease-in-out) infinite',
        'pulse-dot': 'pulse-dot 1.8s var(--ease-in-out) infinite',
      },
    },
  },
  plugins: [],
} satisfies Config;
