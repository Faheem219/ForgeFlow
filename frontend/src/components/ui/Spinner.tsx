export function Spinner({ size = 'md' }: { size?: 'sm' | 'md' | 'lg' }): JSX.Element {
  const dim = size === 'sm' ? 'h-3.5 w-3.5' : size === 'lg' ? 'h-7 w-7' : 'h-4 w-4';
  return (
    <svg
      className={`animate-spin ${dim} shrink-0 text-current`}
      style={{ animationDuration: '0.8s' }}
      viewBox="0 0 24 24"
      fill="none"
      role="status"
      aria-label="Loading"
    >
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="2.5" opacity="0.18" />
      <path
        d="M21 12a9 9 0 0 0-9-9"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
        opacity="0.9"
      />
    </svg>
  );
}
