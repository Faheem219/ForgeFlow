import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';

import { ErrorBoundary } from '../components/ErrorBoundary';
import { Toaster } from '../components/ui';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false },
  },
});

export function Providers({ children }: { children: ReactNode }): JSX.Element {
  return (
    <QueryClientProvider client={queryClient}>
      {/* App-wide safety net: a render crash shows a recoverable screen, not a white page. */}
      <ErrorBoundary>{children}</ErrorBoundary>
      <Toaster />
    </QueryClientProvider>
  );
}
