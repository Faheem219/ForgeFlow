import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

/**
 * Contrast audit of the token set.
 *
 * Parsed out of `tokens.css` rather than duplicated here, so this measures what actually ships. It
 * exists because a two-theme token set has a specific, easy failure mode: a colour picked against a
 * near-black canvas (a bright `emerald-300`, say) is illegible the moment the same token is used on
 * white. That is invisible in review and obvious to a user — exactly the kind of thing a test
 * should hold.
 *
 * Thresholds are WCAG 2.1 AA: 4.5:1 for body text, 3:1 for large text and UI boundaries.
 */

type Rgb = [number, number, number];

// Resolved from the vitest root (the `frontend` package) — `import.meta.url` is not a file URL
// under the jsdom environment.
const tokensCss = readFileSync(resolve(process.cwd(), 'src/app/theme/tokens.css'), 'utf8');

/** Read the declaration block that follows `marker`. */
function parseBlock(marker: string): Record<string, Rgb> {
  const at = tokensCss.indexOf(marker);
  if (at === -1) throw new Error(`selector not found: ${marker}`);
  const open = tokensCss.indexOf('{', at);
  const close = tokensCss.indexOf('}', open);
  const vars: Record<string, Rgb> = {};
  for (const line of tokensCss.slice(open + 1, close).split('\n')) {
    const m = /--([\w-]+):\s*(\d+)\s+(\d+)\s+(\d+)\s*;/.exec(line);
    if (m) vars[m[1]] = [Number(m[2]), Number(m[3]), Number(m[4])];
  }
  return vars;
}

function luminance([r, g, b]: Rgb): number {
  const channel = (c: number): number => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

export function contrastRatio(a: Rgb, b: Rgb): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

const THEMES = {
  light: parseBlock(":root[data-theme='light']"),
  dark: parseBlock(":root[data-theme='dark']"),
};

/** [foreground token, background token, minimum ratio, description] */
const PAIRS: Array<[string, string, number, string]> = [
  ['text-primary', 'canvas', 4.5, 'primary text on the page'],
  ['text-primary', 'surface', 4.5, 'primary text on a panel'],
  ['text-primary', 'elevated', 4.5, 'primary text on a hovered row'],
  ['text-secondary', 'surface', 4.5, 'secondary text on a panel'],
  ['text-muted', 'surface', 4.5, 'muted text on a panel'],
  ['text-muted', 'canvas', 4.5, 'muted text on the page'],
  ['text-faint', 'surface', 3.0, 'faint text (labels/eyebrows)'],
  ['brand-text', 'surface', 4.5, 'brand text on a panel'],
  ['brand-text', 'brand-bg', 4.5, 'brand text on a brand chip'],
  ['brand', 'canvas', 3.0, 'brand fill against the page'],
  ['success', 'success-bg', 4.5, 'success badge'],
  ['warning', 'warning-bg', 4.5, 'warning badge'],
  ['danger', 'danger-bg', 4.5, 'danger badge'],
  ['info', 'info-bg', 4.5, 'info badge'],
  ['success', 'surface', 4.5, 'success text on a panel'],
  ['warning', 'surface', 4.5, 'warning text on a panel'],
  ['danger', 'surface', 4.5, 'danger text on a panel'],
];

describe.each(Object.keys(THEMES) as Array<keyof typeof THEMES>)('%s theme contrast', (theme) => {
  const vars = THEMES[theme];

  it.each(PAIRS)('%s on %s meets %s:1 (%s)', (fg, bg, min) => {
    expect(vars[fg], `missing token --${fg}`).toBeDefined();
    expect(vars[bg], `missing token --${bg}`).toBeDefined();
    expect(contrastRatio(vars[fg], vars[bg])).toBeGreaterThanOrEqual(min);
  });

  it('keeps borders visible against their panel without becoming text', () => {
    // A hairline needs to be *seen*, not read — but an invisible one makes every panel edge vanish.
    expect(contrastRatio(vars['border-strong'], vars.surface)).toBeGreaterThanOrEqual(1.4);
    expect(contrastRatio(vars['border-subtle'], vars.surface)).toBeGreaterThan(1.0);
  });

  it('separates the surface ladder so elevation is perceptible', () => {
    // canvas → surface → elevated must be three distinguishable steps; if any two collapse, the
    // whole "elevation by background step, not shadow" approach silently stops working.
    expect(luminance(vars.canvas)).not.toBeCloseTo(luminance(vars.surface), 3);
    expect(luminance(vars.surface)).not.toBeCloseTo(luminance(vars.elevated), 3);
  });
});

describe('theme parity', () => {
  it('defines the same token names in both themes', () => {
    // A token present in one theme and missing in the other resolves to an inherited value and
    // produces a colour that belongs to neither — the hardest theming bug to spot by eye.
    expect(Object.keys(THEMES.dark).sort()).toEqual(Object.keys(THEMES.light).sort());
  });
});
