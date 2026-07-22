import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SettingsForm } from './SettingsForm';
import type { SettingView } from './types';

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function setting(over: Partial<SettingView> = {}): SettingView {
  return {
    key: 'model_routing',
    category: 'models',
    type: 'str',
    description: 'Cheap model for routing.',
    sensitive: false,
    restart_required: false,
    choices: [],
    value: 'claude-haiku-4-5-20251001',
    default: 'claude-haiku-4-5-20251001',
    source: 'env',
    ...over,
  };
}

interface MockState {
  puts: Array<{ key: string; value: unknown }>;
  deletes: string[];
  putStatus: number;
}

function installFetch(state: MockState): void {
  vi.stubGlobal(
    'fetch',
    vi.fn<typeof fetch>((input, init) => {
      const url = String(input);
      const key = url.split('/admin/config/')[1] ?? '';
      if (init?.method === 'PUT') {
        state.puts.push({ key, value: JSON.parse(String(init.body)).value });
        if (state.putStatus >= 400) {
          return Promise.resolve(
            json(
              { error: { type: 'user_error', message: "'repair_max_iterations' must be ≤ 20" } },
              state.putStatus,
            ),
          );
        }
        return Promise.resolve(json(setting({ key, source: 'db' })));
      }
      if (init?.method === 'DELETE') {
        state.deletes.push(key);
        return Promise.resolve(json({ reverted: setting({ key, source: 'default' }) }));
      }
      return Promise.resolve(json({}));
    }),
  );
}

function renderForm(settings: SettingView[], onChanged = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>
  );
  render(<SettingsForm settings={settings} onChanged={onChanged} />, { wrapper });
  return onChanged;
}

let state: MockState;

beforeEach(() => {
  state = { puts: [], deletes: [], putStatus: 200 };
});
afterEach(() => vi.unstubAllGlobals());

describe('SettingsForm', () => {
  it('renders each setting with its effective value and source badge', () => {
    installFetch(state);
    renderForm([
      setting(),
      setting({ key: 'model_codegen', value: 'claude-sonnet-5', source: 'db' }),
    ]);

    expect(screen.getByTestId('setting-model_routing')).toBeInTheDocument();
    // Source is made visible per the `admin > env > default` requirement.
    expect(screen.getByTestId('setting-model_routing')).toContainElement(
      screen.getByTestId('source-env'),
    );
    expect(screen.getByTestId('source-db')).toBeInTheDocument();
    expect((screen.getByTestId('input-model_routing') as HTMLInputElement).value).toBe(
      'claude-haiku-4-5-20251001',
    );
  });

  it('saves an edit via PUT', async () => {
    installFetch(state);
    const onChanged = renderForm([setting()]);

    fireEvent.change(screen.getByTestId('input-model_routing'), {
      target: { value: 'claude-sonnet-5' },
    });
    fireEvent.click(screen.getByTestId('save-model_routing'));

    await waitFor(() =>
      expect(state.puts.at(-1)).toEqual({ key: 'model_routing', value: 'claude-sonnet-5' }),
    );
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it('coerces numeric edits to numbers', async () => {
    installFetch(state);
    renderForm([
      setting({ key: 'repair_max_iterations', category: 'repair', type: 'int', value: 5, default: 5 }),
    ]);

    fireEvent.change(screen.getByTestId('input-repair_max_iterations'), { target: { value: '8' } });
    fireEvent.click(screen.getByTestId('save-repair_max_iterations'));

    await waitFor(() =>
      expect(state.puts.at(-1)).toEqual({ key: 'repair_max_iterations', value: 8 }),
    );
  });

  it('resets an overridden key via DELETE', async () => {
    installFetch(state);
    renderForm([setting({ source: 'db' })]);

    fireEvent.click(screen.getByTestId('reset-model_routing'));
    await waitFor(() => expect(state.deletes).toEqual(['model_routing']));
  });

  it('disables reset for a key that is not overridden', () => {
    installFetch(state);
    renderForm([setting({ source: 'env' })]);
    expect(screen.getByTestId('reset-model_routing')).toBeDisabled();
  });

  it('surfaces a validation error inline without losing the edit', async () => {
    state.putStatus = 400;
    installFetch(state);
    renderForm([
      setting({ key: 'repair_max_iterations', category: 'repair', type: 'int', value: 5, default: 5 }),
    ]);

    fireEvent.change(screen.getByTestId('input-repair_max_iterations'), {
      target: { value: '999' },
    });
    fireEvent.click(screen.getByTestId('save-repair_max_iterations'));

    await waitFor(() =>
      expect(screen.getByTestId('error-repair_max_iterations')).toHaveTextContent('must be ≤ 20'),
    );
  });

  it('renders sensitive keys masked and read-only, never the value', () => {
    installFetch(state);
    renderForm([
      setting({ key: 'anthropic_api_key', sensitive: true, value: '••••••', default: '' }),
    ]);

    expect(screen.getByTestId('sensitive-status')).toHaveTextContent('••••••');
    // No editor / save control for a secret — it routes to the vault, not config.
    expect(screen.queryByTestId('input-anthropic_api_key')).not.toBeInTheDocument();
    expect(screen.queryByTestId('save-anthropic_api_key')).not.toBeInTheDocument();
  });
});
