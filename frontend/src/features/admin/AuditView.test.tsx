import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AuditView } from './AuditView';
import type { ConfigAudit } from './types';

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

const AUDIT: ConfigAudit[] = [
  {
    key: 'model_routing',
    action: 'update',
    before: 'claude-haiku-4-5-20251001',
    after: 'claude-sonnet-5',
    updated_by: '65abc',
    created_at: '2026-07-20T10:00:00Z',
  },
  {
    key: 'design_provider',
    action: 'delete',
    before: 'figma',
    after: null,
    updated_by: null,
    created_at: '2026-07-20T09:00:00Z',
  },
];

function installFetch(rows: ConfigAudit[]): void {
  vi.stubGlobal(
    'fetch',
    vi.fn<typeof fetch>((input) => {
      if (String(input).includes('/admin/config/audit')) return Promise.resolve(json(rows));
      return Promise.resolve(json({}));
    }),
  );
}

function renderView() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  render(<AuditView />, { wrapper });
}

afterEach(() => vi.unstubAllGlobals());
beforeEach(() => vi.unstubAllGlobals());

describe('AuditView', () => {
  it('renders the change history with before→after and actor', async () => {
    installFetch(AUDIT);
    renderView();

    await waitFor(() => expect(screen.getAllByTestId('audit-row')).toHaveLength(2));

    const update = screen.getByText('model_routing').closest('[data-testid="audit-row"]')!;
    expect(update).toHaveTextContent('claude-haiku-4-5-20251001');
    expect(update).toHaveTextContent('claude-sonnet-5');
    expect(update).toHaveTextContent('update');
    expect(update).toHaveTextContent('65abc');

    // A delete shows the revert to env/default and a system actor.
    const del = screen.getByText('design_provider').closest('[data-testid="audit-row"]')!;
    expect(del).toHaveTextContent('delete');
    expect(del).toHaveTextContent('figma');
    expect(del).toHaveTextContent('system');
  });

  it('shows an empty state before any change', async () => {
    installFetch([]);
    renderView();
    await waitFor(() => expect(screen.getByText('No changes yet')).toBeInTheDocument());
  });
});
