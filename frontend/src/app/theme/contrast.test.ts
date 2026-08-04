import { describe, expect, it } from 'vitest';

import { PALETTES, type ThemePalette } from './palette';

/**
 * WCAG contrast, enforced in CI for BOTH themes.
 *
 * A token that reads fine in dark and vanishes in light (or vice versa) is exactly the class of
 * regression a reviewer eyeballing one theme misses — so every readable-text pairing is asserted
 * here, per theme, with real WCAG math. Decorative text (`fgFaint`) is held to 3:1, the large-
 * text/UI threshold, because it is never used for copy the user must read.
 */

function channel(value: number): number {
  const c = value / 255;
  return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

function luminance(hex: string): number {
  const n = parseInt(hex.replace('#', ''), 16);
  const r = channel((n >> 16) & 0xff);
  const g = channel((n >> 8) & 0xff);
  const b = channel(n & 0xff);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrastRatio(a: string, b: string): number {
  const la = luminance(a);
  const lb = luminance(b);
  const [hi, lo] = la > lb ? [la, lb] : [lb, la];
  return (hi + 0.05) / (lo + 0.05);
}

/** [foreground, background, minimum ratio, what breaks if this fails] */
type Pair = [keyof ThemePalette, keyof ThemePalette, number, string];

const PAIRS: Pair[] = [
  // Reading copy — AA normal text.
  ['fg', 'canvas', 4.5, 'body text on the page'],
  ['fg', 'surface', 4.5, 'body text on cards'],
  ['fg', 'raised', 4.5, 'body text on menus/hover wells'],
  ['fg', 'overlay', 4.5, 'body text in modals'],
  ['fg', 'sunken', 4.5, 'body text in inset wells'],
  ['fgMuted', 'canvas', 4.5, 'secondary text on the page'],
  ['fgMuted', 'surface', 4.5, 'secondary text on cards'],
  ['fgMuted', 'raised', 4.5, 'secondary text on menus'],
  ['fgSubtle', 'surface', 4.5, 'metadata text on cards'],
  ['fgSubtle', 'canvas', 4.5, 'metadata text on the page'],
  // Decorative-only text — large/UI threshold.
  ['fgFaint', 'surface', 3, 'placeholders / disabled labels'],
  // Actions.
  ['fgInverted', 'brand', 4.5, 'label on primary buttons'],
  ['brand', 'surface', 3, 'brand outlines / focus ring vs card'],
  ['brandText', 'surface', 4.5, 'brand-colored links/labels on cards'],
  ['brandText', 'canvas', 4.5, 'brand-colored links/labels on the page'],
  // Status text (badges, inline notes) on the surfaces it appears on.
  ['success', 'surface', 4.5, 'success text on cards'],
  ['warning', 'surface', 4.5, 'warning text on cards'],
  ['danger', 'surface', 4.5, 'danger text on cards'],
  ['info', 'surface', 4.5, 'info text on cards'],
  ['success', 'canvas', 4.5, 'success text on the page'],
  ['warning', 'canvas', 4.5, 'warning text on the page'],
  ['danger', 'canvas', 4.5, 'danger text on the page'],
  ['info', 'canvas', 4.5, 'info text on the page'],
];

describe.each(Object.entries(PALETTES))('%s palette contrast', (_name, palette) => {
  it.each(PAIRS)('%s on %s ≥ %s:1 (%s)', (fgKey, bgKey, min) => {
    const ratio = contrastRatio(palette[fgKey], palette[bgKey]);
    expect(
      ratio,
      `${String(fgKey)} (${palette[fgKey]}) on ${String(bgKey)} (${palette[bgKey]}) = ${ratio.toFixed(2)}:1`,
    ).toBeGreaterThanOrEqual(min);
  });

  it('borders are perceptible against every surface they separate', () => {
    // Hairlines are not text: 1.2:1 is deliberately lenient, but catches a border that literally
    // disappears (ratio ~1.0) the way a dark-only value does on white.
    for (const bg of ['canvas', 'surface', 'raised'] as const) {
      expect(contrastRatio(palette.edge, palette[bg])).toBeGreaterThanOrEqual(1.2);
    }
  });
});
