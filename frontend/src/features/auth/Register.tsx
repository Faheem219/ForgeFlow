import { useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import { ApiError } from '../../lib/apiClient';
import { useAuthStore } from '../../lib/stores/authStore';
import { register } from './api';
import { AuthShell, Field } from './AuthShell';

export default function Register(): JSX.Element {
  const navigate = useNavigate();
  const setAuth = useAuthStore((s) => s.setAuth);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const res = await register(email, password);
      setAuth(res.access_token, res.user);
      navigate('/');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Registration failed');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthShell
      title="Create your account"
      footer={
        <>
          Already have an account?{' '}
          <Link className="text-brand-text hover:underline" to="/login">
            Sign in
          </Link>
        </>
      }
    >
      <form onSubmit={onSubmit} className="space-y-4">
        <Field
          label="Email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <Field
          label="Password (min 8 characters)"
          type="password"
          autoComplete="new-password"
          required
          minLength={8}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        {error ? (
          <p className="text-sm text-danger" role="alert">
            {error}
          </p>
        ) : null}
        <button
          type="submit"
          disabled={submitting}
          className="w-full rounded-lg bg-brand px-3 py-2 font-medium text-white hover:bg-brand disabled:opacity-50"
        >
          {submitting ? 'Creating…' : 'Create account'}
        </button>
      </form>
    </AuthShell>
  );
}
