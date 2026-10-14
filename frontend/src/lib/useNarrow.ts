import { useEffect, useRef, useState } from 'react';

/**
 * Whether an element is narrower than `threshold` pixels — a container query for the panes that
 * are resized by dragging rather than by the viewport (the preview, the IDE).
 *
 * Re-renders only when the answer flips, not on every pixel of a drag. Where `ResizeObserver` is
 * missing (or never fires, as in jsdom) it stays `false`: the full layout is the safe default.
 */
export function useNarrow<T extends HTMLElement>(threshold: number): [React.RefObject<T>, boolean] {
  const ref = useRef<T>(null);
  const [narrow, setNarrow] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(([entry]) => {
      const width = entry?.contentRect.width ?? el.getBoundingClientRect().width;
      // A zero width means hidden/unmounting — keep the last answer rather than flicker.
      if (width > 0) setNarrow(width < threshold);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [threshold]);

  return [ref, narrow];
}

/**
 * An element's rendered width, rounded to whole pixels, or `fallback` until it has been measured.
 *
 * For SVG charts: drawing into a viewBox the size of the box itself keeps 10px labels at 10px.
 * A fixed viewBox stretched with `width: 100%` scales the type with the container — on a wide
 * screen the axis labels came out more than twice their intended size.
 */
export function useMeasuredWidth<T extends HTMLElement>(
  fallback: number,
): [React.RefObject<T>, number] {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(fallback);

  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(([entry]) => {
      const next = Math.round(entry?.contentRect.width ?? 0);
      if (next > 0) setWidth(next);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return [ref, width];
}

/** Whether a media query matches, tracked live. `false` where `matchMedia` is unavailable. */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() =>
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia(query).matches
      : false,
  );
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;
    const mql = window.matchMedia(query);
    const onChange = (): void => setMatches(mql.matches);
    onChange();
    mql.addEventListener?.('change', onChange);
    return () => mql.removeEventListener?.('change', onChange);
  }, [query]);
  return matches;
}
