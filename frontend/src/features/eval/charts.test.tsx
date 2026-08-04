/**
 * Chart geometry (phase-45). The palette validator checks colour, not layout — so these assert the
 * things it cannot: that labels stay inside the viewBox, that marks stay inside the plot, and that
 * the longest real spec id still fits its gutter. Cheaper and stricter than eyeballing a render.
 */

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { BarChart, Dumbbell, Histogram, StatTile } from './charts';

/** The longest id in the phase-43 corpus — the label gutter has to survive it. */
const LONGEST_SPEC = 'bookmark-manager';

function viewBox(el: Element): { width: number; height: number } {
  const [, , width, height] = (el.getAttribute('viewBox') ?? '0 0 0 0').split(' ').map(Number);
  return { width, height };
}

/** Conservative monospace advance at font-size 10 (SVG text has no layout in jsdom). */
function textWidth(text: string, size = 10): number {
  return text.length * size * 0.62;
}

describe('Dumbbell geometry', () => {
  const data = [
    { id: LONGEST_SPEC, label: LONGEST_SPEC, before: 0.25, after: 1 },
    { id: 'todo-list', label: 'todo-list', before: 0, after: 0 },
  ];

  it('keeps the end label inside the viewBox at 100%', () => {
    render(<Dumbbell data={data} beforeLabel="First-pass" afterLabel="Post-repair" />);
    const svg = screen.getByTestId('dumbbell-chart');
    const { width } = viewBox(svg);

    // The rightmost ink is the end label of a 100% row.
    const labels = Array.from(svg.querySelectorAll('text')).filter((t) =>
      (t.textContent ?? '').endsWith('%'),
    );
    const rightmost = Math.max(
      ...labels.map((t) => Number(t.getAttribute('x')) + textWidth(t.textContent ?? '')),
    );
    expect(rightmost).toBeLessThanOrEqual(width);
  });

  it('leaves the longest spec id room in the label gutter', () => {
    render(<Dumbbell data={data} beforeLabel="First-pass" afterLabel="Post-repair" />);
    const svg = screen.getByTestId('dumbbell-chart');
    const label = Array.from(svg.querySelectorAll('text')).find(
      (t) => t.textContent === LONGEST_SPEC,
    );
    expect(label).toBeDefined();
    // The plot starts at x=132; the id must not run into it.
    expect(textWidth(LONGEST_SPEC)).toBeLessThan(132);
  });

  it('grows its height with the row count rather than overlapping rows', () => {
    const { rerender } = render(<Dumbbell data={data} beforeLabel="a" afterLabel="b" />);
    const short = viewBox(screen.getByTestId('dumbbell-chart')).height;

    rerender(
      <Dumbbell
        data={[...data, { id: 'x', label: 'x', before: 0.5, after: 0.9 }]}
        beforeLabel="a"
        afterLabel="b"
      />,
    );
    expect(viewBox(screen.getByTestId('dumbbell-chart')).height).toBeGreaterThan(short);
  });

  it('renders a zero-value row without a negative-width mark', () => {
    render(<Dumbbell data={data} beforeLabel="a" afterLabel="b" />);
    const circles = Array.from(screen.getByTestId('dumbbell-chart').querySelectorAll('circle'));
    expect(circles.length).toBeGreaterThan(0);
    for (const c of circles) expect(Number(c.getAttribute('r'))).toBeGreaterThan(0);
  });

  it('gives every end dot a surface ring so it stays legible where marks overlap', () => {
    render(<Dumbbell data={data} beforeLabel="a" afterLabel="b" />);
    for (const c of screen.getByTestId('dumbbell-chart').querySelectorAll('circle')) {
      expect(c.getAttribute('stroke-width')).toBe('2');
    }
  });
});

describe('BarChart geometry', () => {
  const data = [
    { id: LONGEST_SPEC, label: LONGEST_SPEC, value: 125000 },
    { id: 'zero', label: 'zero', value: 0 },
  ];

  it('keeps the value label inside the viewBox for the largest bar', () => {
    // The formatter is applied by the caller, so the expected string is derived from it rather
    // than hardcoded: `toLocaleString()` is locale-sensitive (en-IN groups 125000 as "1,25,000"),
    // and pinning one locale's output would make this assert the runner's environment instead of
    // the chart's geometry — which is the only thing it is here to check.
    const format = (v: number): string => v.toLocaleString();
    const expected = format(125000);

    render(<BarChart data={data} format={format} />);
    const svg = screen.getByTestId('bar-chart');
    const { width } = viewBox(svg);
    const label = Array.from(svg.querySelectorAll('text')).find((t) => t.textContent === expected);
    expect(label).toBeDefined();
    const right = Number(label?.getAttribute('x')) + textWidth(expected);
    expect(right).toBeLessThanOrEqual(width);
  });

  it('never draws a bar wider than the plot', () => {
    render(<BarChart data={data} />);
    const svg = screen.getByTestId('bar-chart');
    const { width: vbWidth } = viewBox(svg);
    for (const rect of svg.querySelectorAll('rect[fill="#6366f1"]')) {
      const x = Number(rect.getAttribute('x'));
      const w = Number(rect.getAttribute('width'));
      expect(w).toBeGreaterThanOrEqual(0);
      expect(x + w).toBeLessThanOrEqual(vbWidth);
    }
  });

  it('caps bar thickness rather than filling the row', () => {
    render(<BarChart data={data} />);
    for (const rect of screen.getByTestId('bar-chart').querySelectorAll('rect[fill="#6366f1"]')) {
      expect(Number(rect.getAttribute('height'))).toBeLessThanOrEqual(24);
    }
  });

  it('draws no bar at all for a zero value', () => {
    render(<BarChart data={[{ id: 'z', label: 'z', value: 0 }]} />);
    const bars = Array.from(
      screen.getByTestId('bar-chart').querySelectorAll('rect[fill="#6366f1"]'),
    ).filter((r) => Number(r.getAttribute('width')) > 0);
    expect(bars).toHaveLength(0);
  });
});

describe('Histogram', () => {
  it('says so plainly when every bucket is empty', () => {
    render(<Histogram buckets={[{ label: '0', count: 0 }]} />);
    expect(screen.getByText('No repair runs yet')).toBeInTheDocument();
    expect(screen.queryByTestId('histogram')).not.toBeInTheDocument();
  });

  it('labels each bucket with its count and its bin', () => {
    render(
      <Histogram
        buckets={[
          { label: '0', count: 1 },
          { label: '5+', count: 3 },
        ]}
      />,
    );
    expect(screen.getByTestId('histogram-bucket-5+')).toHaveTextContent('3');
    expect(screen.getByTestId('histogram-bucket-5+')).toHaveTextContent('5+');
  });
});

describe('StatTile', () => {
  it('renders the value and its supporting detail', () => {
    render(<StatTile label="Repair delta" value="+50%" detail="median +50%" hero />);
    expect(screen.getByTestId('stat-repair-delta')).toHaveTextContent('+50%');
    expect(screen.getByText('median +50%')).toBeInTheDocument();
  });
});
