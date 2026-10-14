import type { Config } from 'tailwindcss';
import defaultTheme from 'tailwindcss/defaultTheme';
import plugin from 'tailwindcss/plugin';

import { CSS_VAR, cssVariables, dark, light, type ThemePalette } from './src/app/theme/palette';

/** Bind a palette token to a Tailwind color, preserving alpha utilities (`bg-surface/40`). */
const token = (key: keyof ThemePalette): string => `rgb(var(${CSS_VAR[key]}) / <alpha-value>)`;

// ForgeFlow semantic theme, indigo/violet accents (IMPLEMENTATION_PLAN.md §4).
// Colors are NAMESPACED (surface-*, fg-*, edge-*) on purpose: registering bare names like
// `primary` or `subtle` lets `bg-primary` / `text-subtle` compile into nonsense silently.
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  // `.dark` kept alongside data-theme for third-party CSS that keys on the class.
  darkMode: ['class', '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        surface: {
          DEFAULT: token('surface'),
          canvas: token('canvas'),
          raised: token('raised'),
          overlay: token('overlay'),
          sunken: token('sunken'),
        },
        fg: {
          DEFAULT: token('fg'),
          muted: token('fgMuted'),
          subtle: token('fgSubtle'),
          faint: token('fgFaint'),
          inverted: token('fgInverted'),
        },
        edge: {
          DEFAULT: token('edge'),
          strong: token('edgeStrong'),
        },
        brand: {
          DEFAULT: token('brand'),
          hover: token('brandHover'),
          text: token('brandText'),
        },
        accent: token('accent'),
        success: token('success'),
        warning: token('warning'),
        danger: token('danger'),
        info: token('info'),
      },
      fontFamily: {
        // `Inter Variable` (with the space) is the family @fontsource-variable registers. The
        // config previously asked for `InterVariable`, which matched nothing, so every screen
        // silently fell through to the OS system font.
        sans: ['"Inter Variable"', 'Inter', ...defaultTheme.fontFamily.sans],
        mono: ['"JetBrains Mono"', ...defaultTheme.fontFamily.mono],
      },
      // Workbench density: 13px is the reading size of the tool surfaces (the IDE, rails, chat),
      // with a true micro size for metadata instead of scattered `text-[11px]` literals.
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
        xs: ['0.75rem', { lineHeight: '1.05rem' }],
        sm: ['0.8125rem', { lineHeight: '1.25rem' }],
      },
      // One notch tighter at the large end: soft 12–16px corners on small cards read as
      // templated; tools want crisper geometry.
      borderRadius: {
        xl: '0.625rem',
        '2xl': '0.875rem',
      },
      boxShadow: {
        // Elevation is theme-aware: the same shadow that separates a menu on white is invisible on
        // near-black, so the strength comes from a per-theme variable (see the plugin below).
        pop: '0 0 0 1px rgb(var(--ff-edge-strong) / 0.6), 0 12px 28px -10px rgb(0 0 0 / var(--ff-shadow-a)), 0 4px 10px -4px rgb(0 0 0 / var(--ff-shadow-b))',
        raise: 'inset 0 1px 0 rgb(255 255 255 / var(--ff-highlight-a)), 0 1px 2px rgb(0 0 0 / var(--ff-shadow-b))',
      },
      keyframes: {
        'ff-fade': { from: { opacity: '0' }, to: { opacity: '1' } },
        'ff-pop': {
          from: { opacity: '0', transform: 'translateY(-3px) scale(0.98)' },
          to: { opacity: '1', transform: 'none' },
        },
        // An indeterminate run bar: a short segment travelling the track, for work with no %.
        'ff-sweep': {
          from: { transform: 'translateX(-100%)' },
          to: { transform: 'translateX(400%)' },
        },
        'ff-breathe': { '0%, 100%': { opacity: '1' }, '50%': { opacity: '0.45' } },
        'ff-rise': {
          from: { opacity: '0', transform: 'translateY(6px)' },
          to: { opacity: '1', transform: 'none' },
        },
        'ff-flash': {
          from: { backgroundColor: 'rgb(var(--ff-brand) / 0.16)' },
          to: { backgroundColor: 'transparent' },
        },
      },
      animation: {
        'ff-fade': 'ff-fade 160ms ease-out',
        'ff-pop': 'ff-pop 140ms cubic-bezier(0.16, 1, 0.3, 1)',
        'ff-sweep': 'ff-sweep 1.6s cubic-bezier(0.45, 0, 0.55, 1) infinite',
        'ff-breathe': 'ff-breathe 2.4s ease-in-out infinite',
        // `both`: an item with a delay stays hidden until its turn instead of flashing in first.
        'ff-rise': 'ff-rise 420ms cubic-bezier(0.16, 1, 0.3, 1) both',
        'ff-flash': 'ff-flash 900ms ease-out',
        // `backwards`, not `both`: holds the start state through a stagger delay, then lets go —
        // a held end-state transform would override framer-motion's inline layout transforms.
        'ff-rise-in': 'ff-rise 380ms cubic-bezier(0.16, 1, 0.3, 1) backwards',
      },
    },
  },
  plugins: [
    // Emit the theme variables from palette.ts — tokens have exactly one source of truth.
    // Dark doubles as the `:root` fallback so anything outside the themed tree stays legible.
    plugin(({ addBase }) => {
      addBase({
        ':root, :root[data-theme="dark"]': {
          ...cssVariables(dark),
          '--ff-shadow-a': '0.55',
          '--ff-shadow-b': '0.35',
          '--ff-highlight-a': '0.04',
        },
        ':root[data-theme="light"]': {
          ...cssVariables(light),
          '--ff-shadow-a': '0.14',
          '--ff-shadow-b': '0.06',
          '--ff-highlight-a': '0.7',
        },
      });
    }),
  ],
} satisfies Config;
