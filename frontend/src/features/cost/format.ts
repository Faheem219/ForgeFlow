/** Currency + count formatting for the cost panel (phase-46). */

/** Always two decimals, so a small spend is visible rather than rounded away to "₹0". */
export function inr(value: number): string {
  return `₹${value.toFixed(2)}`;
}

const UNITS: { limit: number; suffix: string }[] = [
  { limit: 1e9, suffix: 'B' },
  { limit: 1e6, suffix: 'M' },
  { limit: 1e3, suffix: 'K' },
];

/**
 * A token count short enough to survive a narrow stat tile: `2.6M+`, `15K`, `847`.
 *
 * Grouped digits are the honest format and the wrong one here — the panel lives in a resizable
 * pane roughly 90px wide per tile, and a seven-figure count (`2,625,431`) simply clipped, so the
 * number a user actually read was a truncated prefix of the real one. Compacting is the only
 * rendering that stays *correct* at that width.
 *
 * The `+` is load-bearing rather than decorative: the value is floored, never rounded, so `2.6M+`
 * means "at least 2.6M" and can never overstate spend. It is omitted when the short form is exact
 * (`15K` really is 15,000), which keeps the marker meaningful instead of ambient. Callers pair
 * this with {@link exactCount} in a `title` for the full figure on hover.
 */
export function compactCount(value: number): string {
  if (!Number.isFinite(value)) return '0';
  const n = Math.max(0, Math.trunc(value));
  if (n < 1000) return String(n);
  const unit = UNITS.find((u) => n >= u.limit);
  if (!unit) return String(n);
  const scaled = n / unit.limit;
  // One decimal while it buys precision; whole units past 100, where it only costs width.
  const shown = scaled >= 100 ? Math.floor(scaled) : Math.floor(scaled * 10) / 10;
  return `${shown}${unit.suffix}${shown * unit.limit === n ? '' : '+'}`;
}

/**
 * The full grouped figure, for the `title` behind a compacted one.
 *
 * Lakh grouping (`26,25,431`) to match the ₹ the panel is denominated in — and to match what this
 * panel already showed, since the bare `toLocaleString()` it replaces took the browser's locale.
 */
export function exactCount(value: number, unit = 'tokens'): string {
  return `${Math.max(0, Math.trunc(value)).toLocaleString('en-IN')} ${unit}`;
}
