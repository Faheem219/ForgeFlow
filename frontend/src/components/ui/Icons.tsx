import type { SVGProps } from 'react';

/**
 * Inline icon set.
 *
 * Hand-drawn on a 16px grid at a single 1.35 stroke weight rather than pulled from a library: an
 * icon pack is a dependency (the frontend stack is fixed) and, more practically, mixing weights is
 * the fastest way to make chrome look assembled instead of designed. All icons inherit
 * `currentColor` so they follow the theme without any per-icon wiring.
 */

type IconProps = SVGProps<SVGSVGElement> & { size?: number };

function Icon({ size = 16, children, ...props }: IconProps): JSX.Element {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.35}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      {...props}
    >
      {children}
    </svg>
  );
}

export const IconDashboard = (p: IconProps): JSX.Element => (
  <Icon {...p}>
    <rect x="2" y="2" width="5" height="5" rx="1" />
    <rect x="9" y="2" width="5" height="5" rx="1" />
    <rect x="2" y="9" width="5" height="5" rx="1" />
    <rect x="9" y="9" width="5" height="5" rx="1" />
  </Icon>
);

export const IconEval = (p: IconProps): JSX.Element => (
  <Icon {...p}>
    <path d="M2 13.5V7M6 13.5V3M10 13.5V9M14 13.5V5" />
  </Icon>
);

export const IconSettings = (p: IconProps): JSX.Element => (
  <Icon {...p}>
    <circle cx="8" cy="8" r="2.25" />
    <path d="M8 1.5v1.6M8 12.9v1.6M14.5 8h-1.6M3.1 8H1.5M12.6 3.4l-1.1 1.1M4.5 11.5l-1.1 1.1M12.6 12.6l-1.1-1.1M4.5 4.5L3.4 3.4" />
  </Icon>
);

export const IconAdmin = (p: IconProps): JSX.Element => (
  <Icon {...p}>
    <path d="M8 1.75l5 2v4c0 3-2.1 5.5-5 6.5-2.9-1-5-3.5-5-6.5v-4l5-2Z" />
    <path d="M6 8l1.5 1.5L10.5 6.5" />
  </Icon>
);

export const IconSun = (p: IconProps): JSX.Element => (
  <Icon {...p}>
    <circle cx="8" cy="8" r="3" />
    <path d="M8 1v1.5M8 13.5V15M15 8h-1.5M2.5 8H1M12.95 3.05l-1.06 1.06M4.11 11.89l-1.06 1.06M12.95 12.95l-1.06-1.06M4.11 4.11L3.05 3.05" />
  </Icon>
);

export const IconMoon = (p: IconProps): JSX.Element => (
  <Icon {...p}>
    <path d="M13.5 9.4A5.75 5.75 0 0 1 6.6 2.5a5.75 5.75 0 1 0 6.9 6.9Z" />
  </Icon>
);

export const IconMonitor = (p: IconProps): JSX.Element => (
  <Icon {...p}>
    <rect x="1.75" y="2.75" width="12.5" height="8.5" rx="1.25" />
    <path d="M5.5 14h5M8 11.25V14" />
  </Icon>
);

export const IconSidebar = (p: IconProps): JSX.Element => (
  <Icon {...p}>
    <rect x="1.75" y="2.75" width="12.5" height="10.5" rx="1.5" />
    <path d="M6.25 2.75v10.5" />
  </Icon>
);

export const IconTerminal = (p: IconProps): JSX.Element => (
  <Icon {...p}>
    <rect x="1.75" y="2.75" width="12.5" height="10.5" rx="1.5" />
    <path d="M4.5 6.5L6.5 8l-2 1.5M8.5 10h3" />
  </Icon>
);

export const IconDatabase = (p: IconProps): JSX.Element => (
  <Icon {...p}>
    <ellipse cx="8" cy="3.75" rx="5.25" ry="2" />
    <path d="M2.75 3.75v8.5c0 1.1 2.35 2 5.25 2s5.25-.9 5.25-2v-8.5" />
    <path d="M13.25 8c0 1.1-2.35 2-5.25 2s-5.25-.9-5.25-2" />
  </Icon>
);

export const IconSearch = (p: IconProps): JSX.Element => (
  <Icon {...p}>
    <circle cx="7" cy="7" r="4.25" />
    <path d="M10.2 10.2L14 14" />
  </Icon>
);

export const IconChevronRight = (p: IconProps): JSX.Element => (
  <Icon {...p}>
    <path d="M6 3.5L10.5 8 6 12.5" />
  </Icon>
);

export const IconLogout = (p: IconProps): JSX.Element => (
  <Icon {...p}>
    <path d="M6 14H3.25A1.25 1.25 0 0 1 2 12.75v-9.5A1.25 1.25 0 0 1 3.25 2H6" />
    <path d="M10.5 11L13.5 8l-3-3M13.5 8H6" />
  </Icon>
);

export const IconPlay = (p: IconProps): JSX.Element => (
  <Icon {...p}>
    <path d="M4.5 2.75l8 5.25-8 5.25V2.75Z" />
  </Icon>
);

export const IconRefresh = (p: IconProps): JSX.Element => (
  <Icon {...p}>
    <path d="M13.5 8a5.5 5.5 0 1 1-1.7-3.97" />
    <path d="M13.75 2v3.25H10.5" />
  </Icon>
);
