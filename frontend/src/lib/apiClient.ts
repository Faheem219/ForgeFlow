// Typed fetch wrapper for the ForgeFlow control-plane API.
// Base URL comes from VITE_API_BASE_URL (falls back to localhost for dev).
// The bearer token (if any) is attached from the auth store; a 401 clears the session.

import { useAuthStore } from './stores/authStore';

export const apiBaseUrl: string = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';

/**
 * Deadline for calls that drive external teardown work (e.g. project delete). Generous, but
 * finite.
 *
 * Deadlines are strictly OPT-IN. Most non-build stage intents run their agent inline in the HTTP
 * request and legitimately take minutes — a previous revision applied a 30s default to every call
 * and aborted real agent runs client-side while the backend kept working. Task-level busy state is
 * cleared by the realtime `run.finished` event (see useTaskStream), not by racing the request.
 */
export const LONG_TASK_TIMEOUT_MS = 15 * 60_000;

export interface ErrorEnvelope {
  error: { type: string; message: string; detail?: unknown; fallback_hint?: string };
}

/** Error carrying the server's structured envelope (see backend app/core/errors.py). */
export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly type: string = 'system_error',
    public readonly detail?: unknown,
    /** Provider errors carry an actionable fallback (e.g. "switch to figma") — phase-17/19. */
    public readonly fallbackHint?: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

/**
 * Raised when a request is aborted — by an opt-in deadline, or by its caller unmounting.
 *
 * Extends ApiError so the many `err instanceof ApiError` toast paths report the abort's actual
 * message instead of a generic "Action failed".
 */
export class ApiAbortError extends ApiError {
  constructor(
    message: string,
    /** True when the caller cancelled (unmount/navigation) rather than the deadline expiring. */
    public readonly cancelled: boolean,
  ) {
    super(0, message, 'aborted');
    this.name = 'ApiAbortError';
  }
}

export interface ApiFetchOptions extends RequestInit {
  /** Request deadline in ms. Unset (or `0`) means NO deadline — the default, see above. */
  timeoutMs?: number;
}

export async function apiFetch<T>(path: string, init?: ApiFetchOptions): Promise<T> {
  const { token } = useAuthStore.getState();
  const { timeoutMs = 0, signal: callerSignal, ...rest } = init ?? {};

  // Two abort sources — the caller's (unmount) and ours (deadline). `AbortSignal.any` would be
  // tidier but is too new to rely on, so they are bridged manually.
  const controller = new AbortController();
  const onCallerAbort = (): void => controller.abort();
  callerSignal?.addEventListener('abort', onCallerAbort, { once: true });
  if (callerSignal?.aborted) controller.abort();

  let timedOut = false;
  const timer =
    timeoutMs > 0
      ? setTimeout(() => {
          timedOut = true;
          controller.abort();
        }, timeoutMs)
      : null;

  try {
    const res = await fetch(`${apiBaseUrl}${path}`, {
      ...rest,
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(rest.headers as Record<string, string> | undefined),
      },
    });

    if (res.status === 401) {
      useAuthStore.getState().clear();
    }

    if (!res.ok) {
      let type = 'system_error';
      let message = `Request failed with status ${res.status}`;
      let detail: unknown;
      let fallbackHint: string | undefined;
      try {
        const body = (await res.json()) as Partial<ErrorEnvelope>;
        if (body.error) {
          type = body.error.type ?? type;
          message = body.error.message ?? message;
          detail = body.error.detail;
          fallbackHint = body.error.fallback_hint;
        }
      } catch {
        // Non-JSON error body — keep the status-derived defaults.
      }
      throw new ApiError(res.status, message, type, detail, fallbackHint);
    }

    // 204 (and any empty body) would explode in `res.json()`. Delete returns one.
    if (res.status === 204 || res.headers.get('content-length') === '0') {
      return undefined as T;
    }

    return (await res.json()) as T;
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw timedOut
        ? new ApiAbortError(`Request timed out after ${Math.round(timeoutMs / 1000)}s`, false)
        : new ApiAbortError('Request cancelled', true);
    }
    throw err;
  } finally {
    if (timer) clearTimeout(timer);
    callerSignal?.removeEventListener('abort', onCallerAbort);
  }
}

export interface HealthResponse {
  status: string;
  env: string;
  version: string;
}

export const getHealth = (): Promise<HealthResponse> => apiFetch<HealthResponse>('/health');
