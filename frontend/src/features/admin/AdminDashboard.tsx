import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { EmptyState, Spinner } from '../../components/ui';
import { listConfig } from './api';
import { AuditView } from './AuditView';
import { Observability } from './Observability';
import { SettingsForm } from './SettingsForm';
import { ADMIN_SECTIONS, CONFIG_SECTIONS, type AdminSection } from './types';

function isSection(value: string | undefined): value is AdminSection {
  return ADMIN_SECTIONS.some((s) => s.id === value);
}

/**
 * The admin-only control plane (phase-52): view + edit every platform setting from phase-51, with
 * each value's source made visible, plus global observability and the config audit log. Guarded by
 * `RequireAdmin` at the route; the API enforces `require_admin` too.
 */
export function AdminDashboard(): JSX.Element {
  const { section } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const active: AdminSection = isSection(section) ? section : 'models';

  const configQuery = useQuery({ queryKey: ['admin-config'], queryFn: listConfig });

  const forSection = useMemo(
    () => (configQuery.data ?? []).filter((s) => s.category === active),
    [configQuery.data, active],
  );

  const refetchConfig = () =>
    void queryClient.invalidateQueries({ queryKey: ['admin-config'] });

  return (
    <div className="flex h-[calc(100vh-7rem)] gap-6">
      <nav className="w-48 shrink-0 space-y-1" aria-label="Admin sections">
        {ADMIN_SECTIONS.map((s) => (
          <button
            key={s.id}
            type="button"
            data-testid={`admin-nav-${s.id}`}
            aria-current={s.id === active}
            onClick={() => navigate(`/admin/${s.id}`)}
            className={`block w-full rounded-lg px-3 py-2 text-left text-sm ${
              s.id === active
                ? 'bg-brand-600/20 text-brand-200'
                : 'text-slate-300 hover:bg-slate-800'
            }`}
          >
            {s.label}
          </button>
        ))}
      </nav>

      <section className="min-w-0 flex-1 overflow-auto">
        <h1 className="mb-1 text-xl font-semibold text-slate-100">
          {ADMIN_SECTIONS.find((s) => s.id === active)?.label}
        </h1>
        <p className="mb-4 text-sm text-slate-500">
          Precedence is <span className="font-mono">admin &gt; env &gt; default</span>. Changes take
          effect immediately unless a key is marked <em>restart required</em>.
        </p>

        {active === 'observability' ? (
          <Observability />
        ) : active === 'audit' ? (
          <AuditView />
        ) : configQuery.isLoading ? (
          <div className="flex justify-center py-8">
            <Spinner />
          </div>
        ) : configQuery.isError ? (
          <EmptyState
            title="Could not load settings"
            description="The config API is unavailable or you are not an admin."
          />
        ) : CONFIG_SECTIONS.includes(active) ? (
          <SettingsForm settings={forSection} onChanged={refetchConfig} />
        ) : null}
      </section>
    </div>
  );
}

export default AdminDashboard;
