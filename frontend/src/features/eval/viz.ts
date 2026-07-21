/**
 * Chart palette + formatters for the evaluation dashboard (phase-45).
 *
 * These values were **validated, not chosen by eye** — `scripts/validate_palette.js` from the
 * dataviz skill, run against ForgeFlow's own chart surface (`#0f172a`, the raised slate token) in
 * dark mode:
 *
 *   accent  `#6366f1`  PASS lightness band · PASS chroma floor · PASS contrast >= 3:1
 *   context `#64748b`  in-band, >= 3:1, and *deliberately* below the chroma floor — it is the
 *                      de-emphasis gray, which is supposed to read gray
 *
 * Two shades of one hue could not span the >=15 normal-vision deltaE floor while staying inside the
 * dark lightness band (0.48–0.67) — that squeeze is structural for a single hue. So the paired
 * chart uses the skill's **emphasis** form instead: the after-value in the accent, the before-value
 * in the de-emphasis gray. That is also the more honest encoding here, because the post-repair
 * number *is* the story and the first-pass number is its context.
 */

export const VIZ = {
  surface: '#0f172a',
  accent: '#6366f1', // post-repair / the single-series hue
  context: '#64748b', // first-pass / de-emphasis
  grid: '#1e293b',
  axis: '#334155',
  muted: '#94a3b8',
} as const;

/** Status colors are reserved and never reused for a series; always shipped with a label. */
export const STATUS = {
  delivered: '#0ca30c',
  escalated: '#fab219',
  failed: '#d03b3b',
} as const;

export function pct(value: number | null | undefined, signed = false): string {
  if (value === null || value === undefined) return '—';
  return signed
    ? `${value >= 0 ? '+' : ''}${Math.round(value * 100)}%`
    : `${Math.round(value * 100)}%`;
}

export function num(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}
