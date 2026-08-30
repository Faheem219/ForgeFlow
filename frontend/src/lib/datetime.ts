/**
 * Timestamp formatting, pinned to IST.
 *
 * Two decisions, both deliberate:
 *
 * - **The zone is fixed, not the viewer's.** ForgeFlow's users and its runs are read against one
 *   clock, so a timestamp has to mean the same thing to everyone looking at it — a run that
 *   started at 14:05 did not start at 08:35 for a reader in London. `Intl` gets an explicit
 *   `timeZone`, so nothing depends on the machine the browser happens to run on.
 * - **The input must carry an offset.** These format an *instant*; they cannot recover one from an
 *   ambiguous string. An ISO date-time with no offset (`2026-08-30T07:34:18`) is parsed as local
 *   time by JS, which is why the API serializes UTC-aware datetimes (`…+00:00`) — see the
 *   `tz_aware` note in `backend/app/db/mongo.py`.
 */

export const IST_TIME_ZONE = 'Asia/Kolkata';

/** Appended where a bare time could be mistaken for the reader's own clock. */
export const IST_SUFFIX = 'IST';

const DATE: Intl.DateTimeFormatOptions = {
  timeZone: IST_TIME_ZONE,
  day: '2-digit',
  month: 'short',
  year: 'numeric',
};

const TIME: Intl.DateTimeFormatOptions = {
  timeZone: IST_TIME_ZONE,
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
};

function parse(value: string | number | Date): Date | null {
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** `30 Aug 2026` — for a row where the day is the useful part and the minute is noise. */
export function formatDateIST(value: string | number | Date, fallback = '—'): string {
  const date = parse(value);
  return date ? new Intl.DateTimeFormat('en-IN', DATE).format(date) : fallback;
}

/** `14:05 IST` — a bare clock time, labelled so it cannot be read as the viewer's own. */
export function formatTimeIST(value: string | number | Date, fallback = '—'): string {
  const date = parse(value);
  return date ? `${new Intl.DateTimeFormat('en-IN', TIME).format(date)} ${IST_SUFFIX}` : fallback;
}

/** `30 Aug 2026, 14:05 IST` — the full stamp, for audit rows and artifact versions. */
export function formatDateTimeIST(value: string | number | Date, fallback = '—'): string {
  const date = parse(value);
  if (!date) return fallback;
  const stamp = new Intl.DateTimeFormat('en-IN', { ...DATE, ...TIME }).format(date);
  return `${stamp} ${IST_SUFFIX}`;
}

/** The hour of day in IST (0–23) — for anything keyed on "what time is it there", like a greeting. */
export function hourIST(value: string | number | Date = new Date()): number {
  const date = parse(value) ?? new Date();
  const hour = new Intl.DateTimeFormat('en-GB', {
    timeZone: IST_TIME_ZONE,
    hour: '2-digit',
    hour12: false,
  }).format(date);
  return Number.parseInt(hour, 10) % 24;
}
