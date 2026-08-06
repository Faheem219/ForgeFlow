/**
 * The ForgeFlow mark: a drawn glyph, not a gradient square.
 *
 * The path is the product — a run rising through the pipeline, node to node. Monoline on a 24px
 * grid, `currentColor` stroke on a flat brand tile, so it holds in both themes and at 16px. A
 * plain gradient rectangle is the fastest way for a logo to read as generated; a hand-set path
 * with a point of view is the fix.
 */
export function BrandMark({ size = 28 }: { size?: number }): JSX.Element {
  return (
    <span
      aria-hidden
      className="grid shrink-0 place-items-center rounded-lg bg-brand text-fg-inverted"
      style={{ width: size, height: size }}
    >
      <svg
        width={size * 0.62}
        height={size * 0.62}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={2.2}
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M4 17h5l3-5 3-5h5" />
        <circle cx="4" cy="17" r="1.6" fill="currentColor" stroke="none" />
        <circle cx="20" cy="7" r="1.6" fill="currentColor" stroke="none" />
      </svg>
    </span>
  );
}
