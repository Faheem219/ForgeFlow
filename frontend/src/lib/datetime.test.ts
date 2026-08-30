/**
 * Timestamps are read against one clock (IST), whoever is looking and wherever from.
 *
 * The bug underneath these tests was not a formatting choice: Mongo handed back naive datetimes,
 * the API serialized them with no offset, and the browser then parsed `2026-08-30T07:34:18` as
 * *local* time — so every stamp in the UI sat 5h30m in the past. The backend now reads them
 * timezone-aware (`app/db/mongo.py`); these guard the display half.
 */
import { describe, expect, it } from 'vitest';

import { formatDateIST, formatDateTimeIST, formatTimeIST, hourIST } from './datetime';

// 08:04 UTC is 13:34 IST on the same day.
const UTC_MORNING = '2026-08-30T08:04:00+00:00';
// 20:30 UTC is 02:00 IST the *next* day — the case a naive +5:30 on the date alone gets wrong.
const UTC_LATE = '2026-08-30T20:30:00+00:00';

describe('IST formatting', () => {
  it('shifts an instant into IST rather than reporting UTC', () => {
    expect(formatTimeIST(UTC_MORNING)).toBe('13:34 IST');
  });

  it('rolls the date over when IST is already tomorrow', () => {
    expect(formatDateIST(UTC_LATE)).toBe('31 Aug 2026');
    expect(formatTimeIST(UTC_LATE)).toBe('02:00 IST');
  });

  it('labels the zone, so a bare clock time cannot be misread as the viewer’s own', () => {
    expect(formatDateTimeIST(UTC_MORNING)).toContain('IST');
    expect(formatDateTimeIST(UTC_MORNING)).toContain('30 Aug 2026');
  });

  it('reads an offset-carrying string as the instant it names, not as local time', () => {
    // Same moment, written three ways — all three must format identically.
    const asUtc = formatDateTimeIST('2026-08-30T08:04:00Z');
    const asOffset = formatDateTimeIST('2026-08-30T13:34:00+05:30');
    const asEpoch = formatDateTimeIST(new Date('2026-08-30T08:04:00Z').getTime());
    expect(asOffset).toBe(asUtc);
    expect(asEpoch).toBe(asUtc);
  });

  it('gives a fallback rather than "Invalid Date" for a missing stamp', () => {
    expect(formatDateIST('')).toBe('—');
    expect(formatTimeIST('not a date')).toBe('—');
    expect(formatDateTimeIST('', 'never')).toBe('never');
  });

  it('reports the IST hour, which is what a greeting is actually asking about', () => {
    expect(hourIST(UTC_MORNING)).toBe(13);
    expect(hourIST(UTC_LATE)).toBe(2);
    // Midnight IST must be 0, not 24 — the greeting compares with `< 5`.
    expect(hourIST('2026-08-30T18:30:00+00:00')).toBe(0);
  });
});
