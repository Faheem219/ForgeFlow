/**
 * The ONE place ForgeFlow's colors are defined.
 *
 * Everything else derives from this module:
 *  - `tailwind.config.ts` generates the CSS custom properties for both themes from it
 *    (a Tailwind plugin `addBase`s them under `:root[data-theme=…]`), and binds the semantic
 *    color utilities (`bg-surface-raised`, `text-fg-muted`, …) to those variables.
 *  - Monaco and xterm render to canvas and cannot read CSS variables, so their theme objects
 *    are built from these hexes directly (see `features/ide/monacoSetup.ts`, `Terminal.tsx`).
 *  - The `<meta name="theme-color">` swap in `themeStore.applyTheme` reads `META_THEME_COLOR`.
 *  - The pre-paint script in `index.html` necessarily duplicates the two canvas hexes as string
 *    literals (it runs before any module loads); `htmlSync.test.ts` asserts they match.
 *  - `contrast.test.ts` asserts WCAG contrast pairs across BOTH themes, so a token that only
 *    works in one theme fails CI rather than review.
 *
 * Values are plain hex. `rgbTriplet()` converts to the `R G B` channel form Tailwind needs to
 * keep alpha utilities working (`bg-surface/40` → `rgb(var(--ff-surface) / 0.4)`).
 */

export interface ThemePalette {
  /** Page background — the darkest (dark) / lightest (light) layer. */
  canvas: string;
  /** Default panel/card background, one step off the canvas. */
  surface: string;
  /** Hover targets, menus, secondary wells — one step above surface. */
  raised: string;
  /** Modals, popovers, command palette — the topmost opaque layer. */
  overlay: string;
  /** Inset wells: code blocks, terminals, editors sit BELOW the surface. */
  sunken: string;

  /** Primary text. */
  fg: string;
  /** Secondary text — labels, descriptions. AA on canvas/surface/raised. */
  fgMuted: string;
  /** Tertiary text — metadata, timestamps. Still AA on surface. */
  fgSubtle: string;
  /** Decorative text — placeholders, disabled. ≥3:1 on surface, not for reading copy. */
  fgFaint: string;
  /** Text on brand-colored fills. */
  fgInverted: string;

  /** Default hairline border. */
  edge: string;
  /** Emphasised border — inputs, hovered outlines. */
  edgeStrong: string;

  /** Primary action color (indigo). */
  brand: string;
  /** Hover shift for brand fills. */
  brandHover: string;
  /** Brand-colored TEXT — tuned per theme so links/active labels stay AA on surfaces. */
  brandText: string;
  /** Violet secondary accent — used sparingly (gradients, highlights). */
  accent: string;

  success: string;
  warning: string;
  danger: string;
  info: string;
}

export const dark: ThemePalette = {
  // Warm-neutral ("mauve") greys rather than cool zinc: lower chroma reads calmer over long
  // sessions, and the faint violet cast sits naturally beside the indigo brand. The canvas is
  // lifted off pure black so panels, not the void around them, carry the contrast.
  canvas: '#111113',
  surface: '#18171b',
  raised: '#211f24',
  overlay: '#1d1c20',
  sunken: '#0c0b0d',

  fg: '#ecebef',
  fgMuted: '#b4b1bb',
  fgSubtle: '#908d98',
  fgFaint: '#6e6b76',
  fgInverted: '#ffffff',

  edge: '#312e35',
  edgeStrong: '#3e3b44',

  // Iris rather than electric indigo: still the brand hue, with less saturation to shout.
  brand: '#5b5bd6',
  brandHover: '#6966e0',
  brandText: '#b1a9ff',
  accent: '#8b72e0',

  success: '#4cc38a',
  warning: '#e5b450',
  danger: '#f2817f',
  info: '#6cb2f2',
};

export const light: ThemePalette = {
  canvas: '#f8f7f9',
  surface: '#ffffff',
  raised: '#f2f0f4',
  overlay: '#ffffff',
  sunken: '#f4f2f6',

  fg: '#1f1d24',
  fgMuted: '#55525d',
  fgSubtle: '#6b6874',
  fgFaint: '#94919d',
  fgInverted: '#ffffff',

  edge: '#dddae3',
  edgeStrong: '#cbc7d2',

  brand: '#5b5bd6',
  brandHover: '#4f4fc9',
  brandText: '#4c48c0',
  accent: '#6e56cf',

  success: '#1f7a52',
  warning: '#9a5b00',
  danger: '#c62a2f',
  info: '#0b6bc0',
};

export const PALETTES = { dark, light } as const;
export type ResolvedTheme = keyof typeof PALETTES;

/** `<meta name="theme-color">` per theme — must match the literals in index.html's FOUC script. */
export const META_THEME_COLOR: Record<ResolvedTheme, string> = {
  dark: dark.canvas,
  light: light.canvas,
};

/** Token key → CSS custom property name. The `ff-` prefix avoids colliding with 3rd-party vars. */
export const CSS_VAR: Record<keyof ThemePalette, string> = {
  canvas: '--ff-canvas',
  surface: '--ff-surface',
  raised: '--ff-raised',
  overlay: '--ff-overlay',
  sunken: '--ff-sunken',
  fg: '--ff-fg',
  fgMuted: '--ff-fg-muted',
  fgSubtle: '--ff-fg-subtle',
  fgFaint: '--ff-fg-faint',
  fgInverted: '--ff-fg-inverted',
  edge: '--ff-edge',
  edgeStrong: '--ff-edge-strong',
  brand: '--ff-brand',
  brandHover: '--ff-brand-hover',
  brandText: '--ff-brand-text',
  accent: '--ff-accent',
  success: '--ff-success',
  warning: '--ff-warning',
  danger: '--ff-danger',
  info: '--ff-info',
};

/** '#0f1014' → '15 16 20' (the channel form `rgb(var(--x) / <alpha-value>)` composes with). */
export function rgbTriplet(hex: string): string {
  const value = hex.replace('#', '');
  const n = parseInt(value, 16);
  return `${(n >> 16) & 0xff} ${(n >> 8) & 0xff} ${n & 0xff}`;
}

/** All CSS custom properties for one theme, ready for a Tailwind `addBase` block. */
export function cssVariables(palette: ThemePalette): Record<string, string> {
  const out: Record<string, string> = {};
  for (const key of Object.keys(CSS_VAR) as (keyof ThemePalette)[]) {
    out[CSS_VAR[key]] = rgbTriplet(palette[key]);
  }
  return out;
}
