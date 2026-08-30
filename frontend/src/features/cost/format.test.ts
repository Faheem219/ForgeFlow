/**
 * The cost tiles live in a resizable pane, so their width is a hard constraint rather than a
 * layout preference: a seven-figure token count rendered as grouped digits was clipped mid-number
 * (`2,625,4…`), which is worse than imprecise — it is a *different, smaller* number, read
 * confidently.
 */
import { describe, expect, it } from 'vitest';

import { compactCount, exactCount, inr } from './format';

describe('compactCount', () => {
  it('leaves counts that already fit alone', () => {
    expect(compactCount(0)).toBe('0');
    expect(compactCount(847)).toBe('847');
    expect(compactCount(999)).toBe('999');
  });

  it('compacts at each magnitude', () => {
    expect(compactCount(1_000)).toBe('1K');
    expect(compactCount(15_000)).toBe('15K');
    expect(compactCount(2_625_431)).toBe('2.6M+');
    expect(compactCount(4_000_000_000)).toBe('4B');
  });

  it('marks a truncated figure and only a truncated one', () => {
    // The `+` has to mean something, or it stops being read at all.
    expect(compactCount(15_000)).toBe('15K');
    expect(compactCount(15_001)).toBe('15K+');
    expect(compactCount(2_000_000)).toBe('2M');
  });

  it('never rounds up, so a compacted spend cannot overstate itself', () => {
    // 2.69M would round to 2.7M; floored it stays 2.6M+, which is true either way.
    expect(compactCount(2_699_999)).toBe('2.6M+');
  });

  it('drops the decimal once it only costs width', () => {
    expect(compactCount(120_000)).toBe('120K');
    expect(compactCount(999_999)).toBe('999K+');
  });

  it('survives the values an in-flight run can produce', () => {
    expect(compactCount(Number.NaN)).toBe('0');
    expect(compactCount(-5)).toBe('0');
    expect(compactCount(1234.7)).toBe('1.2K+');
  });

  it('keeps the exact figure available for the tooltip', () => {
    // Lakh grouping, matching the ₹ the panel is denominated in — and matching what this panel
    // already rendered, since the old bare `toLocaleString()` picked up an Indian browser locale.
    expect(exactCount(2_625_431)).toBe('26,25,431 tokens');
  });
});

describe('inr', () => {
  it('keeps two decimals so a small spend is not rounded away to zero', () => {
    expect(inr(0.004)).toBe('₹0.00');
    expect(inr(12.5)).toBe('₹12.50');
  });
});
