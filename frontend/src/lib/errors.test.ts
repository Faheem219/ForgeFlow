import { describe, expect, it } from 'vitest';

import { ApiError } from './apiClient';
import { errorToastDescription, errorVariant, toActionableError } from './errors';

describe('toActionableError', () => {
  it('maps a UserError to a non-retryable "check your input" message', () => {
    const result = toActionableError(new ApiError(400, 'Feature name is required', 'user_error'));
    expect(result.type).toBe('user_error');
    expect(result.title).toBe('Check your input');
    expect(result.description).toBe('Feature name is required');
    expect(result.retryable).toBe(false);
  });

  it('surfaces a ProviderError fallback hint as the next step and marks it retryable', () => {
    const err = new ApiError(
      502,
      "Stitch's quota is exhausted",
      'provider_error',
      undefined,
      'Switch to figma, or continue without a design.',
    );
    const result = toActionableError(err);

    expect(result.title).toBe('A provider is unavailable');
    expect(result.hint).toBe('Switch to figma, or continue without a design.');
    expect(result.retryable).toBe(true);
  });

  it('treats a rate-limit as retryable with a wait-and-retry hint', () => {
    const result = toActionableError(new ApiError(429, 'Too many requests', 'rate_limited'));
    expect(result.retryable).toBe(true);
    expect(result.hint).toMatch(/wait/i);
  });

  it('maps an auth error to a sign-in prompt', () => {
    const result = toActionableError(new ApiError(401, 'Not authenticated', 'auth_error'));
    expect(result.title).toMatch(/sign in/i);
    expect(result.retryable).toBe(false);
  });

  it('classifies an unknown envelope type as a system error', () => {
    const result = toActionableError(new ApiError(500, 'boom', 'something_new'));
    expect(result.title).toBe('Something went wrong');
    expect(result.retryable).toBe(true);
  });

  it('recognises a fetch/network TypeError as a connectivity problem', () => {
    const result = toActionableError(new TypeError('Failed to fetch'));
    expect(result.type).toBe('network_error');
    expect(result.title).toMatch(/reach the server/i);
    expect(result.retryable).toBe(true);
  });

  it('falls back gracefully for a plain thrown Error (a render crash)', () => {
    const result = toActionableError(new Error('Cannot read properties of undefined'));
    expect(result.type).toBe('system_error');
    expect(result.description).toBe('Cannot read properties of undefined');
    expect(result.retryable).toBe(true);
  });

  it('never throws on a non-error value', () => {
    expect(() => toActionableError('a string')).not.toThrow();
    expect(() => toActionableError(null)).not.toThrow();
    expect(toActionableError(undefined).title).toBe('Something went wrong');
  });
});

describe('errorVariant', () => {
  it('is "error" for provider/system failures and "warning" for user-correctable ones', () => {
    expect(errorVariant(new ApiError(502, 'x', 'provider_error'))).toBe('error');
    expect(errorVariant(new ApiError(500, 'x', 'system_error'))).toBe('error');
    expect(errorVariant(new ApiError(400, 'x', 'user_error'))).toBe('warning');
    expect(errorVariant(new ApiError(429, 'x', 'rate_limited'))).toBe('warning');
  });
});

describe('errorToastDescription', () => {
  it('joins the message and a distinct hint', () => {
    const err = new ApiError(502, 'Stitch down', 'provider_error', undefined, 'Try figma.');
    expect(errorToastDescription(err)).toBe('Stitch down — Try figma.');
  });

  it('does not duplicate when message and hint coincide', () => {
    const err = new ApiError(400, 'Check your input', 'user_error');
    expect(errorToastDescription(err)).toBe('Check your input');
  });
});
