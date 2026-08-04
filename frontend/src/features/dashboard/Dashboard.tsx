import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { Badge, Button, ConfirmDialog, EmptyState, Input, Modal, Spinner } from '../../components/ui';
import { ApiError } from '../../lib/apiClient';
import { toast } from '../../lib/stores/toastStore';
import { STAGE_LABELS } from '../workspace/stageMeta';
import { createProject, listProjects } from './api';
import { useDeleteProject } from './useDeleteProject';

const MINUTE = 60_000;
const UNITS: Array<[limit: number, ms: number, unit: Intl.RelativeTimeFormatUnit]> = [
  [60 * MINUTE, MINUTE, 'minute'],
  [24 * 60 * MINUTE, 60 * MINUTE, 'hour'],
  [30 * 24 * 60 * MINUTE, 24 * 60 * MINUTE, 'day'],
  [Number.POSITIVE_INFINITY, 30 * 24 * 60 * MINUTE, 'month'],
];

/** "5 minutes ago" — so a card says when it was last touched without a full timestamp. */
function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return 'recently';
  const elapsed = Date.now() - then;
  if (elapsed < MINUTE) return 'just now';
  const format = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
  const [, ms, unit] = UNITS.find(([limit]) => elapsed < limit) as [number, number, Intl.RelativeTimeFormatUnit];
  return format.format(-Math.round(elapsed / ms), unit);
}

export default function Dashboard(): JSX.Element {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [modalOpen, setModalOpen] = useState(false);
  const [name, setName] = useState('');
  /** Project pending deletion — holds the ConfirmDialog's subject; null closes it. */
  const [doomed, setDoomed] = useState<{ id: string; name: string } | null>(null);

  const projectsQuery = useQuery({ queryKey: ['projects'], queryFn: listProjects });
  const { remove, isDeleting } = useDeleteProject();

  const create = useMutation({
    mutationFn: (projectName: string) => createProject(projectName),
    onSuccess: (project) => {
      setModalOpen(false);
      setName('');
      void queryClient.invalidateQueries({ queryKey: ['projects'] });
      navigate(`/projects/${project.id}`);
    },
    onError: (err) => {
      const message = err instanceof ApiError ? err.message : 'Could not create project';
      toast({ title: 'Create failed', description: message, variant: 'error' });
    },
  });

  function onCreate(event: React.FormEvent): void {
    event.preventDefault();
    const trimmed = name.trim();
    if (trimmed) {
      create.mutate(trimmed);
    }
  }

  const projects = projectsQuery.data ?? [];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Projects</h1>
          <p className="text-sm text-slate-400">Take an idea from design to a deployed URL.</p>
        </div>
        <Button onClick={() => setModalOpen(true)}>New project</Button>
      </div>

      {projectsQuery.isLoading ? (
        <div className="flex justify-center py-16">
          <Spinner />
        </div>
      ) : projects.length === 0 ? (
        <EmptyState
          title="No projects yet"
          description="Create your first project to open a workspace."
          action={<Button onClick={() => setModalOpen(true)}>New project</Button>}
        />
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" data-testid="project-list">
          {projects.map((project) => (
            // The open-project control and the delete control are SIBLINGS (a button cannot nest a
            // button), with delete floated over the card's corner and revealed on hover/focus.
            <li key={project.id} className="group relative">
              <button
                type="button"
                data-testid={`project-${project.id}`}
                onClick={() => navigate(`/projects/${project.id}`)}
                className="flex h-full w-full flex-col gap-3 rounded-xl border border-slate-800 bg-slate-900/40 p-4 text-left transition hover:border-slate-700 hover:bg-slate-800/40"
              >
                <span className="flex items-start justify-between gap-2">
                  <span className="truncate font-medium text-slate-100">{project.name}</span>
                  <Badge tone="brand">{STAGE_LABELS[project.current_stage]}</Badge>
                </span>
                <span className="mt-auto flex items-center gap-2 text-xs text-slate-500">
                  <span>{project.stack}</span>
                  <span aria-hidden>·</span>
                  <span>Updated {relativeTime(project.updated_at)}</span>
                </span>
              </button>
              <button
                type="button"
                aria-label={`Delete ${project.name}`}
                data-testid={`delete-project-${project.id}`}
                onClick={() => setDoomed({ id: project.id, name: project.name })}
                className="absolute bottom-3 right-3 rounded-md p-1.5 text-slate-500 opacity-0 transition hover:bg-red-500/10 hover:text-red-400 focus-visible:opacity-100 group-hover:opacity-100"
              >
                <svg
                  aria-hidden
                  viewBox="0 0 20 20"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  className="h-4 w-4"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M3.5 5.5h13m-10.5 0v-1a1.5 1.5 0 0 1 1.5-1.5h5a1.5 1.5 0 0 1 1.5 1.5v1m-8 3v6m3-6v6m3-6v6M5 5.5l.7 10.1a1.5 1.5 0 0 0 1.5 1.4h5.6a1.5 1.5 0 0 0 1.5-1.4l.7-10.1"
                  />
                </svg>
              </button>
            </li>
          ))}
        </ul>
      )}

      <Modal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        title="New project"
        footer={
          <>
            <Button variant="ghost" onClick={() => setModalOpen(false)}>
              Cancel
            </Button>
            <Button onClick={onCreate} loading={create.isPending} disabled={!name.trim()}>
              Create
            </Button>
          </>
        }
      >
        <form onSubmit={onCreate}>
          <Input
            label="Project name"
            value={name}
            autoFocus
            placeholder="My web app"
            onChange={(e) => setName(e.target.value)}
          />
        </form>
      </Modal>

      <ConfirmDialog
        open={doomed !== null}
        onClose={() => setDoomed(null)}
        title="Delete project"
        description={
          <>
            This permanently deletes <span className="font-medium">{doomed?.name}</span> — its
            sandbox, its database, its conversation, and every artifact. There is no undo.
          </>
        }
        confirmPhrase={doomed?.name}
        confirmLabel="Delete forever"
        loading={isDeleting}
        onConfirm={() => {
          if (doomed) remove(doomed.id, { onDeleted: () => setDoomed(null) });
        }}
      />
    </div>
  );
}
