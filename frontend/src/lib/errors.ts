// Map the backend error taxonomy (§7) to actionable, user-facing UI copy (phase-48).
//
// The control plane classifies every failure as UserError / ProviderError / SystemError / … and
// returns it in a consistent envelope (see apiClient.ts). Turning that classification into "what
// happened + what to do + can I retry" in one place keeps every panel's error handling consistent
// and honest — a ProviderError surfaces its fallback_hint, a rate limit invites a retry, a bad
// input does not.

import { ApiError } from './apiClient';
import { toast, type ToastVariant } from './stores/toastStore';

export interface ActionableError {
  /** Short headline — what happened, in plain language. */
  title: string;
  /** The specifics: the server's message, or a safe generic for opaque failures. */
  description: string;
  /** The next step, when there is a meaningful one (e.g. a provider fallback hint). */
  hint?: string;
  /** Whether offering a "retry" affordance makes sense for this class of error. */
  retryable: boolean;
  /** The taxonomy key this was classified as (useful for tests + analytics). */
  type: string;
}

interface Rule {
  title: string;
  retryable: boolean;
  /** A default next-step when the server didn't supply a more specific one. */
  hint?: string;
  variant: ToastVariant;
}

// Keyed by the `type` in the error envelope. Anything unknown falls through to `system_error`.
const RULES: Record<string, Rule> = {
  user_error: { title: 'Check your input', retryable: false, variant: 'warning' },
  validation_error: { title: 'Check your input', retryable: false, variant: 'warning' },
  not_found: { title: 'Not found', retryable: false, variant: 'warning' },
  auth_error: {
    title: 'Please sign in again',
    retryable: false,
    hint: 'Your session expired. Sign in to continue.',
    variant: 'warning',
  },
  forbidden: { title: "You don't have access", retryable: false, variant: 'warning' },
  conflict: { title: 'That already exists', retryable: false, variant: 'warning' },
  rate_limited: {
    title: 'Slow down a moment',
    retryable: true,
    hint: 'Too many requests — wait a few seconds and try again.',
    variant: 'warning',
  },
  provider_error: {
    title: 'A provider is unavailable',
    retryable: true,
    hint: 'This is usually temporary — retry, or switch providers in settings.',
    variant: 'error',
  },
  system_error: {
    title: 'Something went wrong',
    retryable: true,
    hint: 'This is on our side. Retrying often works.',
    variant: 'error',
  },
};

const FALLBACK = RULES.system_error;

/** Classify any thrown value into an actionable error. Never throws. */
export function toActionableError(error: unknown): ActionableError {
  if (error instanceof ApiError) {
    const rule = RULES[error.type] ?? FALLBACK;
    return {
      type: error.type,
      title: rule.title,
      description: error.message,
      // A server-supplied fallback hint (provider errors carry one) always wins over the default.
      hint: error.fallbackHint ?? rule.hint,
      retryable: rule.retryable,
    };
  }

  // A non-API error: a network failure, or a render/runtime crash caught by the boundary.
  const isNetwork = error instanceof TypeError && /fetch|network/i.test(error.message ?? '');
  return {
    type: isNetwork ? 'network_error' : 'system_error',
    title: isNetwork ? "Can't reach the server" : FALLBACK.title,
    description: isNetwork
      ? 'Check your connection and try again.'
      : error instanceof Error && error.message
        ? error.message
        : 'An unexpected error occurred.',
    hint: isNetwork ? 'ForgeFlow needs a connection for live data.' : FALLBACK.hint,
    retryable: true,
  };
}

/** The toast variant appropriate to an error's severity. */
export function errorVariant(error: unknown): ToastVariant {
  if (error instanceof ApiError) return (RULES[error.type] ?? FALLBACK).variant;
  return 'error';
}

/** One-liner for a toast description: message plus hint, when distinct. */
export function errorToastDescription(error: unknown): string {
  const { description, hint } = toActionableError(error);
  if (hint && hint !== description) return `${description} — ${hint}`;
  return description;
}

/**
 * Raise a taxonomy-aware toast for a caught error — the single call panels use in `onError`, so
 * error UX stays consistent (right title, right severity, message + actionable hint) everywhere.
 */
export function toastError(error: unknown, title?: string): void {
  const actionable = toActionableError(error);
  toast({
    title: title ?? actionable.title,
    description: errorToastDescription(error),
    variant: errorVariant(error),
  });
}
