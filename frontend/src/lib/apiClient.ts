// Typed fetch wrapper for the ForgeFlow control-plane API.
// Base URL comes from VITE_API_BASE_URL (falls back to localhost for dev).
// The bearer token (if any) is attached from the auth store; a 401 clears the session.

import { useAuthStore } from './stores/authStore';

export const apiBaseUrl: string = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';

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

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const { token } = useAuthStore.getState();
  const res = await fetch(`${apiBaseUrl}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.headers as Record<string, string> | undefined),
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

  return (await res.json()) as T;
}

export interface HealthResponse {
  status: string;
  env: string;
  version: string;
}

export const getHealth = (): Promise<HealthResponse> => apiFetch<HealthResponse>('/health');
