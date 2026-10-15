/**
 * The "the agent is working" indicator.
 *
 * Why this and not the bare `Spinner`: a spinner says "something is happening", the right amount of
 * information for a 200ms save. Codegen, design generation and live validation take *minutes*, so
 * this pairs one slow, steady mark with a sentence saying what is going on — the words carry the
 * information, the mark only says the work has not stopped.
 *
 * It used to be a five-scene GSAP timeline (inputs scattering, masks opening, a bouncing rocket) on
 * an endless loop. Up close that read as a loading screen performing for the user rather than a tool
 * reporting status, and it put GSAP in the workspace bundle for one animation. This is CSS only: a
 * ring that turns at an unhurried pace around a still core, and nothing for reduced motion to fight.
 *
 * `compact` is the inline form for a status row (the row already shows the label, so the caption is
 * kept for screen readers only); the default is the block form for an otherwise empty panel.
 */
export function AgentWorking({
  label = 'Working',
  size,
  compact = false,
  className = '',
}: {
  /** Said out loud to screen readers, and shown beneath the mark in the block form. */
  label?: string;
  size?: number;
  compact?: boolean;
  className?: string;
}): JSX.Element {
  const markSize = size ?? (compact ? 28 : 40);

  return (
    <div
      className={`flex shrink-0 ${compact ? 'items-center' : 'flex-col items-center gap-3'} ${className}`}
      role="status"
      aria-live="polite"
    >
      <span
        aria-hidden
        className="relative flex items-center justify-center"
        style={{ width: markSize, height: markSize }}
      >
        <span className="absolute inset-0 rounded-full border-2 border-brand-text/15" />
        <span
          className="absolute inset-0 rounded-full border-2 border-transparent border-t-brand-text motion-safe:animate-spin"
          style={{ animationDuration: '1.4s' }}
        />
        <span className="h-1.5 w-1.5 rounded-full bg-brand-text" />
      </span>
      <p className={compact ? 'sr-only' : 'max-w-xs text-center text-[13px] text-fg-muted'}>
        {label}…
      </p>
    </div>
  );
}
