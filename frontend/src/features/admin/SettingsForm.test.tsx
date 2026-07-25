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
  const merged: SettingView = {
    key: 'model_routing',
    label: 'Model routing',
    env_var: 'MODEL_ROUTING',
    category: 'models',
    type: 'str',
    description: 'Cheap model for routing.',
    sensitive: false,
    restart_required: false,
    locked: false,
    locked_reason: '',
    choices: [],
    value: 'claude-haiku-4-5-20251001',
    default: 'claude-haiku-4-5-20251001',
    source: 'env',
    ...over,
  };
  // Label + env var follow the key unless a test states them, as the API does.
  return {
    ...merged,
    label: over.label ?? merged.key,
    env_var: over.env_var ?? merged.key.toUpperCase(),
  };
}

interface MockState {
  puts: Array<{ key: string; value: unknown }>;
  bulk: Array<Record<string, unknown>>;
  deletes: string[];
  putStatus: number;
  bulkErrors: Record<string, string>;
}

function installFetch(state: MockState): void {
  vi.stubGlobal(
    'fetch',
    vi.fn<typeof fetch>((input, init) => {
      const url = String(input);
      const key = url.split('/admin/config/')[1] ?? '';
      if (init?.method === 'PUT' && key === '') {
        const updates = JSON.parse(String(init.body)).updates as Record<string, unknown>;
        state.bulk.push(updates);
        return Promise.resolve(
          json({
            updated: Object.keys(updates)
              .filter((k) => !(k in state.bulkErrors))
              .map((k) => setting({ key: k, source: 'db' })),
            errors: state.bulkErrors,
          }),
        );
      }
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
  state = { puts: [], bulk: [], deletes: [], putStatus: 200, bulkErrors: {} };
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
    // The env var a key falls back to is shown, so an operator can map panel ↔ .env.
    expect(screen.getByText('MODEL_ROUTING')).toBeInTheDocument();
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
      setting({
        key: 'repair_max_iterations',
        category: 'repair',
        type: 'int',
        value: 5,
        default: 5,
      }),
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
      setting({
        key: 'repair_max_iterations',
        category: 'repair',
        type: 'int',
        value: 5,
        default: 5,
      }),
    ]);

    fireEvent.change(screen.getByTestId('input-repair_max_iterations'), {
      target: { value: '999' },
    });
    fireEvent.click(screen.getByTestId('save-repair_max_iterations'));

    await waitFor(() =>
      expect(screen.getByTestId('error-repair_max_iterations')).toHaveTextContent('must be ≤ 20'),
    );
    expect((screen.getByTestId('input-repair_max_iterations') as HTMLInputElement).value).toBe(
      '999',
    );
  });

  it('saves several edits in one bulk request', async () => {
    installFetch(state);
    renderForm([
      setting(),
      setting({ key: 'model_codegen', value: 'claude-sonnet-5', default: 'claude-sonnet-5' }),
    ]);

    fireEvent.change(screen.getByTestId('input-model_routing'), { target: { value: 'haiku-x' } });
    fireEvent.change(screen.getByTestId('input-model_codegen'), { target: { value: 'sonnet-x' } });

    expect(screen.getByTestId('bulk-bar')).toHaveTextContent('2 unsaved changes');
    fireEvent.click(screen.getByTestId('save-all'));

    await waitFor(() =>
      expect(state.bulk.at(-1)).toEqual({ model_routing: 'haiku-x', model_codegen: 'sonnet-x' }),
    );
  });

  it('keeps a rejected key editable when a bulk save partially fails', async () => {
    state.bulkErrors = { model_codegen: 'not a known model' };
    installFetch(state);
    renderForm([
      setting(),
      setting({ key: 'model_codegen', value: 'claude-sonnet-5', default: 'claude-sonnet-5' }),
    ]);

    fireEvent.change(screen.getByTestId('input-model_routing'), { target: { value: 'haiku-x' } });
    fireEvent.change(screen.getByTestId('input-model_codegen'), { target: { value: 'nope' } });
    fireEvent.click(screen.getByTestId('save-all'));

    await waitFor(() =>
      expect(screen.getByTestId('error-model_codegen')).toHaveTextContent('not a known model'),
    );
    // The rejected edit survives for the operator to fix; the accepted one is cleared.
    expect(screen.getByTestId('bulk-bar')).toHaveTextContent('1 unsaved change');
  });

  it('discards all pending edits', () => {
    installFetch(state);
    renderForm([setting()]);

    fireEvent.change(screen.getByTestId('input-model_routing'), { target: { value: 'haiku-x' } });
    expect(screen.getByTestId('bulk-bar')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('discard-all'));

    expect(screen.queryByTestId('bulk-bar')).not.toBeInTheDocument();
    expect((screen.getByTestId('input-model_routing') as HTMLInputElement).value).toBe(
      'claude-haiku-4-5-20251001',
    );
  });

  it('makes a secret write-only: no value shown, and a new one is PUT', async () => {
    installFetch(state);
    renderForm([
      setting({
        key: 'openai_api_key',
        label: 'OpenAI API key',
        env_var: 'OPENAI_API_KEY',
        sensitive: true,
        value: '••••••',
        default: '',
        source: 'db',
      }),
    ]);

    // The stored value is never rendered — only that one exists.
    expect(screen.getByTestId('sensitive-status')).toHaveTextContent('••••••');
    expect(screen.queryByTestId('input-openai_api_key')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('sensitive-edit'));
    fireEvent.change(screen.getByTestId('sensitive-input'), { target: { value: 'sk-new-key' } });
    fireEvent.click(screen.getByTestId('sensitive-save'));

    await waitFor(() =>
      expect(state.puts.at(-1)).toEqual({ key: 'openai_api_key', value: 'sk-new-key' }),
    );
    // ...and it is not left rendered anywhere after submitting.
    expect(screen.queryByText('sk-new-key')).not.toBeInTheDocument();
  });

  it('renders a locked key read-only, with the reason it cannot be set here', () => {
    installFetch(state);
    renderForm([
      setting({
        key: 'fernet_key',
        label: 'Fernet key',
        env_var: 'FERNET_KEY',
        category: 'core',
        sensitive: true,
        locked: true,
        locked_reason: 'Encrypts every other secret stored here.',
        value: '••••••',
        default: '',
      }),
    ]);

    expect(screen.getByText('Encrypts every other secret stored here.')).toBeInTheDocument();
    expect(screen.queryByTestId('input-fernet_key')).not.toBeInTheDocument();
    expect(screen.queryByTestId('sensitive-edit')).not.toBeInTheDocument();
    expect(screen.queryByTestId('save-fernet_key')).not.toBeInTheDocument();
  });
});
