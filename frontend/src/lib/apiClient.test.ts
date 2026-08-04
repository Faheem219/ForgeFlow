import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError, apiBaseUrl, apiFetch, getHealth } from './apiClient';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('apiClient', () => {
  it('prefixes the API base URL and parses a JSON response', async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(jsonResponse({ status: 'ok', env: 'test', version: '1.2.3' })),
    );
    vi.stubGlobal('fetch', fetchMock);

    const body = await getHealth();

    expect(fetchMock).toHaveBeenCalledWith(`${apiBaseUrl}/health`, expect.any(Object));
    expect(body).toEqual({ status: 'ok', env: 'test', version: '1.2.3' });
  });

  it('maps a server error envelope to an ApiError', async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        jsonResponse(
          { error: { type: 'user_error', message: 'nope', detail: { field: 'x' } } },
          400,
        ),
      ),
    );
    vi.stubGlobal('fetch', fetchMock);

    const error = await apiFetch('/whatever').catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 400, type: 'user_error', message: 'nope' });
    expect((error as ApiError).detail).toEqual({ field: 'x' });
  });

  it('falls back to a status-derived message when the body is not JSON', async () => {
    const fetchMock = vi.fn(() => Promise.resolve(new Response('gateway boom', { status: 502 })));
    vi.stubGlobal('fetch', fetchMock);

    const error = (await apiFetch('/x').catch((e: unknown) => e)) as ApiError;

    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(502);
    expect(error.message).toContain('502');
  });
});
