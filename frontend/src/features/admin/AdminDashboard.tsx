import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { Badge, EmptyState, Spinner } from '../../components/ui';
import { listConfig } from './api';
import { AuditView } from './AuditView';
import { Observability } from './Observability';
import { SettingsForm } from './SettingsForm';
import {
  ADMIN_GROUPS,
  ADMIN_SECTIONS,
  CONFIG_SECTIONS,
  type AdminSection,
  type SettingView,
} from './types';

function isSection(value: string | undefined): value is AdminSection {
  return ADMIN_SECTIONS.some((s) => s.id === value);
}

const SECTION_LABELS: Record<string, string> = Object.fromEntries(
  ADMIN_SECTIONS.map((s) => [s.id, s.label]),
);

/** Free-text match over everything an operator might type: label, key, env var, description. */
function matches(setting: SettingView, query: string): boolean {
  const haystack = [setting.key, setting.label, setting.env_var, setting.description]
    .join(' ')
    .toLowerCase();
  return query
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((term) => haystack.includes(term));
}

/**
 * The admin-only control plane (phase-52): view + edit **every** platform setting from phase-51,
 * with each value's source made visible, plus global observability and the config audit log.
 * Guarded by `RequireAdmin` at the route; the API enforces `require_admin` too.
 *
 * With the whole environment editable here (~110 keys), navigation carries as much weight as the
 * editors: sections are grouped, each shows how many overrides it holds, and search spans every
 * section so a key can be found without knowing which one it lives in.
 */
export function AdminDashboard(): JSX.Element {
  const { section } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const active: AdminSection = isSection(section) ? section : 'models';

  const [query, setQuery] = useState('');
  const [overriddenOnly, setOverriddenOnly] = useState(false);

  const configQuery = useQuery({ queryKey: ['admin-config'], queryFn: listConfig });
  const all = useMemo(() => configQuery.data ?? [], [configQuery.data]);

  const overridesBySection = useMemo(() => {
    const counts: Record<string, number> = {};
    all.forEach((s) => {
      if (s.source === 'db') counts[s.category] = (counts[s.category] ?? 0) + 1;
    });
    return counts;
  }, [all]);

  const searching = query.trim().length > 0;
  const visible = useMemo(() => {
    const scoped = searching ? all : all.filter((s) => s.category === active);
    const filtered = searching ? scoped.filter((s) => matches(s, query)) : scoped;
    return overriddenOnly ? filtered.filter((s) => s.source === 'db') : filtered;
  }, [all, active, query, searching, overriddenOnly]);

  const meta = ADMIN_SECTIONS.find((s) => s.id === active);
  const totalOverrides = all.filter((s) => s.source === 'db').length;
  const refetchConfig = (): void => {
    void queryClient.invalidateQueries({ queryKey: ['admin-config'] });
  };

  const showsCatalog = searching || CONFIG_SECTIONS.includes(active);

  return (
    <div className="flex h-[calc(100vh-6.5rem)] gap-6">
      <nav className="w-56 shrink-0 space-y-4 overflow-y-auto pr-1" aria-label="Admin sections">
        {ADMIN_GROUPS.map((group) => (
          <div key={group}>
            <p className="mb-1 px-3 text-[11px] font-semibold uppercase tracking-wider text-slate-600">
              {group}
            </p>
            {ADMIN_SECTIONS.filter((s) => s.group === group).map((s) => (
              <button
                key={s.id}
                type="button"
                data-testid={`admin-nav-${s.id}`}
                aria-current={s.id === active}
                onClick={() => navigate(`/admin/${s.id}`)}
                className={`flex w-full items-center justify-between gap-2 rounded-lg px-3 py-1.5 text-left text-sm ${
                  s.id === active
                    ? 'bg-brand-600/20 text-brand-200'
                    : 'text-slate-300 hover:bg-slate-800'
                }`}
              >
                <span className="truncate">{s.label}</span>
                {overridesBySection[s.id] ? (
                  <span
                    title={`${overridesBySection[s.id]} setting(s) overridden here`}
                    className="rounded-full bg-brand-600/30 px-1.5 text-[10px] text-brand-200"
                  >
                    {overridesBySection[s.id]}
                  </span>
                ) : null}
              </button>
            ))}
          </div>
        ))}
      </nav>

      <section className="min-w-0 flex-1 overflow-auto pr-1">
        <header className="mb-4">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-xl font-semibold text-slate-100">
              {searching ? 'Search results' : meta?.label}
            </h1>
            {totalOverrides > 0 ? (
              <Badge tone="brand">
                {totalOverrides} admin override{totalOverrides === 1 ? '' : 's'}
              </Badge>
            ) : null}
          </div>
          <p className="mt-1 max-w-3xl text-sm text-slate-500">
            {searching
              ? `${visible.length} setting${visible.length === 1 ? '' : 's'} match “${query.trim()}”.`
              : meta?.blurb}
          </p>
          <p className="mt-1 text-xs text-slate-600">
            Precedence is{' '}
            <span className="font-mono text-slate-500">admin &gt; env &gt; default</span>. Changes
            apply immediately unless a key is marked <em>restart required</em>.
          </p>
        </header>

        {showsCatalog ? (
          <div className="mb-3 flex flex-wrap items-center gap-3">
            <input
              type="search"
              value={query}
              data-testid="config-search"
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search all settings (name, env var, description)…"
              className="w-80 rounded-lg border border-slate-700 bg-slate-950 px-3 py-1.5 text-sm text-slate-100 outline-none focus:border-brand-500"
            />
            <label className="flex items-center gap-2 text-xs text-slate-400">
              <input
                type="checkbox"
                data-testid="filter-overridden"
                className="h-3.5 w-3.5 accent-brand-500"
                checked={overriddenOnly}
                onChange={(e) => setOverriddenOnly(e.target.checked)}
              />
              Overridden only
            </label>
          </div>
        ) : null}

        {active === 'observability' && !searching ? (
          <Observability />
        ) : active === 'audit' && !searching ? (
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
        ) : (
          <SettingsForm
            settings={visible}
            onChanged={refetchConfig}
            categoryLabels={searching ? SECTION_LABELS : undefined}
          />
        )}
      </section>
    </div>
  );
}

export default AdminDashboard;
