/**
 * The quiet layer behind a signed-out page: a hairline grid that fades out from the top.
 *
 * Static on purpose. This used to carry a particle canvas (packets riding the grid) plus a glow
 * that chased the pointer — and it ran behind *every* app screen, including Monaco and xterm, so
 * the main thread paid a per-frame gradient-and-composite bill just to sit there. Measured with the
 * pointer moving, the app spent roughly four times as long on main-thread work with it as without.
 * A grid that does not move is a complete composition; the screen's own content is what should
 * hold the eye. Pure CSS — no script, no animation frame, nothing for reduced motion to switch off.
 *
 * Give the parent `relative` and `overflow-hidden`.
 */
export function AmbientBackground({
  intensity = 'subtle',
}: {
  /** `subtle` for tool chrome, `bold` for the signed-out screens where atmosphere is allowed. */
  intensity?: 'subtle' | 'bold';
}): JSX.Element {
  const bold = intensity === 'bold';
  return (
    <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
      <div
        className="absolute inset-0"
        style={{
          opacity: bold ? 0.7 : 0.4,
          backgroundImage:
            'linear-gradient(rgb(var(--ff-edge) / 0.55) 1px, transparent 1px), linear-gradient(90deg, rgb(var(--ff-edge) / 0.55) 1px, transparent 1px)',
          backgroundSize: '56px 56px',
          backgroundPosition: 'center top',
          maskImage: 'radial-gradient(ellipse 70% 50% at 50% 0%, black 20%, transparent 100%)',
          WebkitMaskImage:
            'radial-gradient(ellipse 70% 50% at 50% 0%, black 20%, transparent 100%)',
        }}
      />
    </div>
  );
}
