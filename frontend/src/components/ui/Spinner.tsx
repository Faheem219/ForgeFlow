/**
 * Indeterminate progress.
 *
 * Marked `.motion-essential` so the reduced-motion reset in tokens.css does NOT freeze it — a
 * stopped spinner reads as "hung", which is a worse outcome for that user than the rotation.
 */
export function Spinner({
  size = 'md',
  className = '',
}: {
  size?: 'sm' | 'md' | 'lg';
  className?: string;
}): JSX.Element {
  const dim = size === 'sm' ? 'h-3.5 w-3.5' : size === 'lg' ? 'h-7 w-7' : 'h-4 w-4';
  return (
    <svg
      className={`motion-essential shrink-0 animate-spin ${dim} text-current ${className}`}
      viewBox="0 0 24 24"
      fill="none"
      role="status"
      aria-label="Loading"
    >
      <circle
        className="opacity-20"
        cx="12"
        cy="12"
        r="9"
        stroke="currentColor"
        strokeWidth="2.5"
      />
      <path
        className="opacity-90"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
        d="M21 12a9 9 0 0 0-9-9"
      />
    </svg>
  );
}
