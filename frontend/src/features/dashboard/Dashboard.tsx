import { HealthBadge } from '../../components/HealthBadge';
import { Button, EmptyState, Panel } from '../../components/ui';
import { useAuthStore } from '../../lib/stores/authStore';

export default function Dashboard(): JSX.Element {
  const user = useAuthStore((s) => s.user);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-slate-100">Welcome back</h1>
        <p className="text-sm text-slate-400">{user?.email}</p>
      </div>

      <HealthBadge />

      <Panel title="Projects">
        <EmptyState
          title="No projects yet"
          description="Project creation lands with the orchestration core (phase 06+)."
          action={<Button disabled>New project</Button>}
        />
      </Panel>
    </div>
  );
}
