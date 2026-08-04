/**
 * Chart primitives for the evaluation dashboard (phase-45).
 *
 * The palette below was **validated, not chosen by eye** — `scripts/validate_palette.js` from the
 * dataviz skill, run against ForgeFlow's own chart surface (`#0f172a`, the raised slate token) in
 * dark mode:
 *
 *   accent  `#6366f1`  PASS lightness band · PASS chroma floor · PASS contrast ≥ 3:1
 *   context `#64748b`  in-band, ≥ 3:1, and *deliberately* below the chroma floor — it is the
 *                      de-emphasis gray, which is supposed to read gray
 *
 * Two shades of one hue could not span the ≥15 normal-vision ΔE floor while staying inside the
 * dark lightness band (0.48–0.67) — that squeeze is structural for a single hue. So the paired
 * chart uses the skill's **emphasis** form instead: the after-value in the accent, the before-value
 * in the de-emphasis gray. That is also the more honest encoding here, because the post-repair
 * number *is* the story and the first-pass number is its context.
 *
 * Mark specs follow the skill: bars ≤24px with a 4px rounded data-end square at the baseline,
 * 2px connectors, ≥8px end dots carrying a 2px surface ring, hairline recessive gridlines, and a
 * 2px surface gap between adjacent marks. Every chart ships a hover/focus readout, and every value
 * is also reachable in the table below — tooltips enhance, they never gate.
 */

import { useId, useState } from 'react';

import { VIZ, pct, num } from './viz';

interface Tip {
  x: number;
  y: number;
  lines: { label: string; value: string; color?: string }[];
}

function Tooltip({ tip }: { tip: Tip | null }): JSX.Element | null {
  if (!tip) return null;
  return (
    <div
      role="status"
      data-testid="viz-tooltip"
      style={{ left: tip.x, top: tip.y }}
      className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full rounded-lg border border-edge-strong bg-surface-overlay/95 px-2 py-1.5 text-xs shadow-lg"
    >
      {tip.lines.map((line) => (
        <div key={line.label} className="flex items-center gap-1.5 whitespace-nowrap">
          {line.color ? (
            <span
              aria-hidden
              className="inline-block h-0.5 w-3 rounded-full"
              style={{ background: line.color }}
            />
          ) : null}
          {/* Values lead, labels follow — the reader has the series and wants the number. */}
          <span className="font-medium text-fg">{line.value}</span>
          <span className="text-fg-muted">{line.label}</span>
        </div>
      ))}
    </div>
  );
}

export function Legend({
  items,
}: {
  items: { label: string; color: string; shape?: 'dot' | 'bar' }[];
}): JSX.Element {
  return (
    <ul className="flex flex-wrap gap-3 text-xs text-fg-muted" data-testid="viz-legend">
      {items.map((item) => (
        <li key={item.label} className="flex items-center gap-1.5">
          <span
            aria-hidden
            className={item.shape === 'bar' ? 'h-2 w-3 rounded-sm' : 'h-2 w-2 rounded-full'}
            style={{ background: item.color }}
          />
          {item.label}
        </li>
      ))}
    </ul>
  );
}

export interface DumbbellDatum {
  id: string;
  label: string;
  before: number | null;
  after: number | null;
}

/**
 * Before → after per item (the skill's form for exactly this job). Each row is a connector from
 * the first-pass value to the post-repair value; the accent dot is where the run ended up.
 * Rows with no measurement render as an explicit "not measured" rather than a dot at zero.
 */
export function Dumbbell({
  data,
  beforeLabel,
  afterLabel,
  height = 22,
}: {
  data: DumbbellDatum[];
  beforeLabel: string;
  afterLabel: string;
  height?: number;
}): JSX.Element {
  const [tip, setTip] = useState<Tip | null>(null);
  const labelWidth = 132;
  const plotWidth = 320;
  const total = labelWidth + plotWidth + 44;
  const chartHeight = Math.max(1, data.length) * height + 24;
  const x = (v: number): number => labelWidth + v * plotWidth;

  return (
    <div className="relative">
      <svg
        role="img"
        aria-label={`${beforeLabel} versus ${afterLabel} per spec`}
        viewBox={`0 0 ${total} ${chartHeight}`}
        className="w-full"
        data-testid="dumbbell-chart"
      >
        {[0, 0.25, 0.5, 0.75, 1].map((tick) => (
          <g key={tick}>
            {/* Hairline, solid, recessive — never dashed. */}
            <line
              x1={x(tick)}
              x2={x(tick)}
              y1={8}
              y2={chartHeight - 16}
              stroke={VIZ.grid}
              strokeWidth={1}
            />
            <text x={x(tick)} y={chartHeight - 4} textAnchor="middle" fontSize={9} fill={VIZ.muted}>
              {tick * 100}%
            </text>
          </g>
        ))}

        {data.map((d, i) => {
          const y = 8 + i * height + height / 2;
          const measured = d.before !== null && d.after !== null;
          return (
            <g
              key={d.id}
              data-testid={`dumbbell-row-${d.id}`}
              tabIndex={0}
              role="listitem"
              aria-label={`${d.label}: ${beforeLabel} ${pct(d.before)}, ${afterLabel} ${pct(d.after)}`}
              onMouseEnter={(e) =>
                setTip({
                  x: e.nativeEvent.offsetX,
                  y: e.nativeEvent.offsetY,
                  lines: [
                    { label: afterLabel, value: pct(d.after), color: VIZ.accent },
                    { label: beforeLabel, value: pct(d.before), color: VIZ.context },
                  ],
                })
              }
              onFocus={() =>
                setTip({
                  x: labelWidth,
                  y,
                  lines: [
                    { label: afterLabel, value: pct(d.after), color: VIZ.accent },
                    { label: beforeLabel, value: pct(d.before), color: VIZ.context },
                  ],
                })
              }
              onMouseLeave={() => setTip(null)}
              onBlur={() => setTip(null)}
              className="focus:outline-none"
            >
              {/* A generous, invisible hit area — never only the painted pixels. */}
              <rect x={0} y={y - height / 2} width={total} height={height} fill="transparent" />
              <text x={0} y={y + 3} fontSize={10} fill={VIZ.muted} className="font-mono">
                {d.label}
              </text>

              {measured ? (
                <>
                  <line
                    x1={x(d.before as number)}
                    x2={x(d.after as number)}
                    y1={y}
                    y2={y}
                    stroke={VIZ.context}
                    strokeWidth={2}
                    strokeLinecap="round"
                  />
                  <circle
                    cx={x(d.before as number)}
                    cy={y}
                    r={4}
                    fill={VIZ.context}
                    stroke={VIZ.surface}
                    strokeWidth={2}
                  />
                  <circle
                    cx={x(d.after as number)}
                    cy={y}
                    r={5}
                    fill={VIZ.accent}
                    stroke={VIZ.surface}
                    strokeWidth={2}
                  />
                  {/* Label the endpoint only — a number on every dot is chaos. */}
                  <text
                    x={x(d.after as number) + 10}
                    y={y + 3}
                    fontSize={10}
                    fill={VIZ.muted}
                    className="tabular-nums"
                  >
                    {pct(d.after)}
                  </text>
                </>
              ) : (
                <text x={labelWidth} y={y + 3} fontSize={10} fill={VIZ.muted}>
                  not measured
                </text>
              )}
            </g>
          );
        })}
      </svg>
      <Tooltip tip={tip} />
    </div>
  );
}

export interface BarDatum {
  id: string;
  label: string;
  value: number;
}

/**
 * A single-series magnitude chart — one hue for every bar (a value-ramp across nominal categories
 * would double-encode length as color). No legend: with one series, the title already names it.
 */
export function BarChart({
  data,
  format = num,
  unit = '',
}: {
  data: BarDatum[];
  format?: (v: number) => string;
  unit?: string;
}): JSX.Element {
  const [tip, setTip] = useState<Tip | null>(null);
  const clipId = useId();
  const labelWidth = 132;
  const plotWidth = 300;
  const rowHeight = 20;
  const total = labelWidth + plotWidth + 56;
  const max = Math.max(1, ...data.map((d) => d.value));
  const height = Math.max(1, data.length) * rowHeight + 8;

  return (
    <div className="relative">
      <svg
        role="img"
        aria-label={`Values per spec${unit ? ` in ${unit}` : ''}`}
        viewBox={`0 0 ${total} ${height}`}
        className="w-full"
        data-testid="bar-chart"
      >
        <defs>
          <clipPath id={clipId}>
            <rect x={0} y={0} width={total} height={height} />
          </clipPath>
        </defs>
        {data.map((d, i) => {
          // 2px surface gap between adjacent bars; bar thickness capped well under the band.
          const barHeight = Math.min(14, rowHeight - 6);
          const y = 4 + i * rowHeight + (rowHeight - barHeight) / 2;
          const width = Math.max(d.value > 0 ? 3 : 0, (d.value / max) * plotWidth);
          return (
            <g
              key={d.id}
              data-testid={`bar-row-${d.id}`}
              tabIndex={0}
              aria-label={`${d.label}: ${format(d.value)}${unit ? ` ${unit}` : ''}`}
              onMouseEnter={(e) =>
                setTip({
                  x: e.nativeEvent.offsetX,
                  y: e.nativeEvent.offsetY,
                  lines: [
                    {
                      label: `${d.label}${unit ? ` (${unit})` : ''}`,
                      value: format(d.value),
                      color: VIZ.accent,
                    },
                  ],
                })
              }
              onMouseLeave={() => setTip(null)}
              onBlur={() => setTip(null)}
              className="focus:outline-none"
            >
              <rect x={0} y={y - 3} width={total} height={rowHeight} fill="transparent" />
              <text
                x={0}
                y={y + barHeight / 2 + 3}
                fontSize={10}
                fill={VIZ.muted}
                className="font-mono"
              >
                {d.label}
              </text>
              <rect
                x={labelWidth}
                y={y}
                width={width}
                height={barHeight}
                rx={4}
                fill={VIZ.accent}
                clipPath={`url(#${clipId})`}
              />
              {/* Square at the baseline: cover the rounding on the left edge only. */}
              <rect
                x={labelWidth}
                y={y}
                width={Math.min(4, width)}
                height={barHeight}
                fill={VIZ.accent}
              />
              <text
                x={labelWidth + width + 6}
                y={y + barHeight / 2 + 3}
                fontSize={10}
                fill={VIZ.muted}
                className="tabular-nums"
              >
                {format(d.value)}
              </text>
            </g>
          );
        })}
      </svg>
      <Tooltip tip={tip} />
    </div>
  );
}

/** Counts per bucket — the shape of how hard the repair loop had to work. */
export function Histogram({
  buckets,
  emptyLabel = 'No repair runs yet',
}: {
  buckets: { label: string; count: number }[];
  emptyLabel?: string;
}): JSX.Element {
  const [tip, setTip] = useState<Tip | null>(null);
  const max = Math.max(1, ...buckets.map((b) => b.count));
  if (buckets.every((b) => b.count === 0)) {
    return <p className="py-4 text-center text-xs text-fg-subtle">{emptyLabel}</p>;
  }

  return (
    <div className="relative">
      <div className="flex items-end gap-1" style={{ height: 96 }} data-testid="histogram">
        {buckets.map((b) => (
          <div
            key={b.label}
            className="flex flex-1 flex-col items-center gap-1 focus:outline-none"
            tabIndex={0}
            data-testid={`histogram-bucket-${b.label}`}
            aria-label={`${b.count} spec${b.count === 1 ? '' : 's'} with ${b.label} iterations`}
            onMouseEnter={(e) =>
              setTip({
                x: e.currentTarget.offsetLeft + e.currentTarget.offsetWidth / 2,
                y: e.currentTarget.offsetTop,
                lines: [
                  {
                    label: `spec${b.count === 1 ? '' : 's'} · ${b.label} iterations`,
                    value: String(b.count),
                    color: VIZ.accent,
                  },
                ],
              })
            }
            onMouseLeave={() => setTip(null)}
            onBlur={() => setTip(null)}
          >
            <span className="text-[10px] tabular-nums text-fg-muted">{b.count || ''}</span>
            <div
              className="w-full rounded-t"
              style={{
                height: `${(b.count / max) * 64}px`,
                minHeight: b.count > 0 ? 3 : 0,
                background: VIZ.accent,
              }}
            />
            <span className="text-[10px] tabular-nums text-fg-subtle">{b.label}</span>
          </div>
        ))}
      </div>
      <Tooltip tip={tip} />
    </div>
  );
}

/** A headline number. Exactly one hero per view; the rest are ordinary stat tiles. */
export function StatTile({
  label,
  value,
  detail,
  hero = false,
  tone,
}: {
  label: string;
  value: string;
  detail?: string;
  hero?: boolean;
  tone?: 'good' | 'muted';
}): JSX.Element {
  return (
    <div className="rounded-xl border border-edge bg-surface px-3 py-2">
      <p className="text-[11px] uppercase tracking-wide text-fg-subtle">{label}</p>
      <p
        className={`${hero ? 'text-4xl' : 'text-xl'} font-semibold ${
          tone === 'good' ? 'text-success' : 'text-fg'
        }`}
        data-testid={`stat-${label.toLowerCase().replace(/[^a-z]+/g, '-')}`}
      >
        {value}
      </p>
      {detail ? <p className="mt-0.5 text-[11px] text-fg-subtle">{detail}</p> : null}
    </div>
  );
}
