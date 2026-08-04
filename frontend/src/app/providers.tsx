import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MotionConfig } from 'framer-motion';
import { useEffect, type ReactNode } from 'react';

import { ErrorBoundary } from '../components/ErrorBoundary';
import { Toaster } from '../components/ui';
import { watchSystemTheme } from '../lib/stores/themeStore';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false },
  },
});

export function Providers({ children }: { children: ReactNode }): JSX.Element {
  // Re-apply the stored theme (the pre-paint script already stamped it; this makes React the
  // owner from here on) and follow OS scheme changes while in `system` mode.
  useEffect(() => watchSystemTheme(), []);

  return (
    <QueryClientProvider client={queryClient}>
      {/* `reducedMotion="user"` disables transform/layout animation app-wide for users who ask
          the OS for less motion — spinners and opacity fades remain. */}
      <MotionConfig reducedMotion="user">
        {/* App-wide safety net: a render crash shows a recoverable screen, not a white page. */}
        <ErrorBoundary>{children}</ErrorBoundary>
        <Toaster />
      </MotionConfig>
    </QueryClientProvider>
  );
}
