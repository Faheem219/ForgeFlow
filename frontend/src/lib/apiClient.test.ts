import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiAbortError, ApiError, apiBaseUrl, apiFetch, getHealth } from './apiClient';

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

  it('returns undefined for a 204 instead of choking on an empty body', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(new Response(null, { status: 204 }))),
    );

    await expect(apiFetch('/projects/abc')).resolves.toBeUndefined();
  });

  it('applies no deadline unless the caller opts in', async () => {
    // Non-build stage intents run their agent inline in the HTTP request and can take minutes.
    // A previous revision defaulted every call to a 30s deadline and aborted real agent runs
    // client-side — this pins the contract that a plain apiFetch never arms a timer at all.
    vi.useFakeTimers();
    try {
      let settled = false;
      const fetchMock = vi.fn(
        (_url: string, init?: RequestInit) =>
          new Promise<Response>((resolve, reject) => {
            init?.signal?.addEventListener('abort', () =>
              reject(new DOMException('aborted', 'AbortError')),
            );
            setTimeout(() => resolve(jsonResponse({ ok: true })), 10 * 60_000);
          }),
      );
      vi.stubGlobal('fetch', fetchMock);

      const promise = apiFetch('/projects/abc/intent', { method: 'POST' }).then(
        (body) => {
          settled = true;
          return body;
        },
        (err: unknown) => {
          settled = true;
          throw err;
        },
      );

      // Well past the old 30s default: still pending, not aborted.
      await vi.advanceTimersByTimeAsync(5 * 60_000);
      expect(settled).toBe(false);

      await vi.advanceTimersByTimeAsync(5 * 60_000);
      await expect(promise).resolves.toEqual({ ok: true });
    } finally {
      vi.useRealTimers();
    }
  });

  it('aborts a request that outlives an opt-in deadline', async () => {
    // A request that never settles is exactly the case that used to strand a spinner forever.
    const fetchMock = vi.fn(
      (_url: string, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () =>
            reject(new DOMException('aborted', 'AbortError')),
          );
        }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const error = (await apiFetch('/slow', { timeoutMs: 10 }).catch(
      (e: unknown) => e,
    )) as ApiAbortError;

    expect(error).toBeInstanceOf(ApiAbortError);
    // The subclass matters: every toast path types on `instanceof ApiError`.
    expect(error).toBeInstanceOf(ApiError);
    expect(error.cancelled).toBe(false);
    expect(error.message).toMatch(/timed out/i);
  });

  it('reports a caller-cancelled request as cancelled, not timed out', async () => {
    const controller = new AbortController();
    const fetchMock = vi.fn(
      (_url: string, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () =>
            reject(new DOMException('aborted', 'AbortError')),
          );
        }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const promise = apiFetch('/slow', { signal: controller.signal, timeoutMs: 5000 });
    controller.abort();

    const error = (await promise.catch((e: unknown) => e)) as ApiAbortError;
    expect(error).toBeInstanceOf(ApiAbortError);
    expect(error.cancelled).toBe(true);
  });
});
