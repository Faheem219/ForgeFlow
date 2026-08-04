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
          // Legacy numbered scale — still referenced by unswept components; the sweep
          // (slate-* → tokens) deletes these.
          50: '#eef2ff',
          100: '#e0e7ff',
          200: '#c7d2fe',
          300: '#a5b4fc',
          400: '#818cf8',
          500: '#6366f1',
          600: '#4f46e5',
          700: '#4338ca',
          800: '#3730a3',
          900: '#312e81',
        },
        accent: {
          DEFAULT: token('accent'),
          // Legacy numbered scale — same story as brand.
          400: '#a78bfa',
          500: '#8b5cf6',
          600: '#7c3aed',
        },
        success: token('success'),
        warning: token('warning'),
        danger: token('danger'),
        info: token('info'),
      },
      fontFamily: {
        sans: ['InterVariable', 'Inter', ...defaultTheme.fontFamily.sans],
        mono: ['"JetBrains Mono"', ...defaultTheme.fontFamily.mono],
      },
    },
  },
  plugins: [
    // Emit the theme variables from palette.ts — tokens have exactly one source of truth.
    // Dark doubles as the `:root` fallback so anything outside the themed tree stays legible.
    plugin(({ addBase }) => {
      addBase({
        ':root, :root[data-theme="dark"]': cssVariables(dark),
        ':root[data-theme="light"]': cssVariables(light),
      });
    }),
  ],
} satisfies Config;
