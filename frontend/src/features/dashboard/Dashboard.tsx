import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import {
  Badge,
  Button,
  ConfirmDialog,
  EmptyState,
  Input,
  Modal,
  SkeletonRows,
} from '../../components/ui';
import { ApiError } from '../../lib/apiClient';
import { stagger } from '../../lib/motion';
import { toast } from '../../lib/stores/toastStore';
import type { ProjectSummary } from '../../lib/types';
import { STAGE_LABELS, STAGE_ORDER, STATUS_TONES } from '../workspace/stageMeta';
import { createProject, listProjects } from './api';
import { useDeleteProject } from './useDeleteProject';

/**
 * Projects index.
 *
 * Each row carries a six-segment pipeline spine rather than a single "current stage" pill. The
 * spine is the product's actual shape (D12 — any stage, any order), and it answers the question a
 * user opens this page to ask: *how far is this one, and where is it stuck?* A lone badge cannot.
 */

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '';
  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 60) return 'just now';
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

/** Six dots, one per stage; the project's current stage is marked. */
function PipelineSpine({ current }: { current: ProjectSummary['current_stage'] }): JSX.Element {
  const currentIndex = STAGE_ORDER.indexOf(current);
  return (
    <span
      className="flex items-center gap-1"
      aria-label={`Current stage: ${STAGE_LABELS[current]}`}
    >
      {STAGE_ORDER.map((stage, i) => {
        const reached = i <= currentIndex;
        return (
          <span
            key={stage}
            title={STAGE_LABELS[stage]}
            className={`h-1 rounded-full transition-all duration-base ease-out ${
              i === currentIndex
                ? 'w-5 bg-brand'
                : reached
                  ? 'w-2.5 bg-brand/45'
                  : 'w-2.5 bg-elevated'
            }`}
          />
        );
      })}
    </span>
  );
}

function ProjectRow({
  project,
  index,
  onOpen,
  onDelete,
}: {
  project: ProjectSummary;
  index: number;
  onOpen: () => void;
  onDelete: () => void;
}): JSX.Element {
  return (
    <li
      className="group animate-rise-in border-b border-subtle last:border-b-0"
      style={stagger(index)}
    >
      <div className="row-interactive flex items-center gap-3 px-3.5 py-2.5">
        <button
          type="button"
          data-testid={`project-${project.id}`}
          onClick={onOpen}
          className="flex min-w-0 flex-1 items-center gap-3 text-left"
        >
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-medium text-primary">{project.name}</span>
            <span className="mt-0.5 flex items-center gap-2 text-2xs text-faint">
              <span>{relativeTime(project.updated_at)}</span>
              <span aria-hidden>·</span>
              <span className="truncate font-mono">{project.stack}</span>
            </span>
          </span>
          <PipelineSpine current={project.current_stage} />
          <Badge tone={STATUS_TONES.in_progress}>{STAGE_LABELS[project.current_stage]}</Badge>
        </button>

        {/*
          Revealed on hover/focus rather than always-on: a delete button sitting permanently next
          to every row invites the accident it is guarded against. It stays reachable by keyboard
          (focus-within) so this is a visual affordance, not a functional gate.
        */}
        <Button
          size="icon"
          variant="ghost"
          aria-label={`Delete ${project.name}`}
          data-testid={`delete-project-${project.id}`}
          onClick={onDelete}
          className="opacity-0 transition-opacity duration-fast ease-out hover:text-danger focus-visible:opacity-100 group-hover:opacity-100"
        >
          <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" aria-hidden fill="none">
            <path
              d="M2.5 4h11M6 4V2.5h4V4M4 4l.6 9a1 1 0 0 0 1 .9h4.8a1 1 0 0 0 1-.9L12 4M6.5 7v4M9.5 7v4"
              stroke="currentColor"
              strokeWidth="1.25"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </Button>
      </div>
    </li>
  );
}

export default function Dashboard(): JSX.Element {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [modalOpen, setModalOpen] = useState(false);
  const [name, setName] = useState('');
  const [pendingDelete, setPendingDelete] = useState<ProjectSummary | null>(null);

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
    if (trimmed) create.mutate(trimmed);
  }

  const projects = projectsQuery.data ?? [];

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-primary">Projects</h1>
          <p className="mt-0.5 text-sm text-muted">Take an idea from design to a deployed URL.</p>
        </div>
        <Button onClick={() => setModalOpen(true)} data-testid="new-project">
          New project
        </Button>
      </header>

      <section className="panel overflow-hidden">
        {projectsQuery.isLoading ? (
          <div className="p-3.5">
            <SkeletonRows rows={3} />
          </div>
        ) : projects.length === 0 ? (
          <div className="p-3.5">
            <EmptyState
              title="No projects yet"
              description="Create your first project to open a workspace."
              action={<Button onClick={() => setModalOpen(true)}>New project</Button>}
            />
          </div>
        ) : (
          <ul data-testid="project-list">
            {projects.map((project, i) => (
              <ProjectRow
                key={project.id}
                project={project}
                index={i}
                onOpen={() => navigate(`/projects/${project.id}`)}
                onDelete={() => setPendingDelete(project)}
              />
            ))}
          </ul>
        )}
      </section>

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
        open={pendingDelete !== null}
        onClose={() => setPendingDelete(null)}
        onConfirm={() => {
          const target = pendingDelete;
          if (target) remove(target.id, { onDeleted: () => setPendingDelete(null) });
        }}
        loading={isDeleting}
        title="Delete project"
        confirmLabel="Delete project"
        confirmPhrase={pendingDelete?.name}
        description={
          <>
            <strong className="font-medium text-primary">{pendingDelete?.name}</strong> and
            everything it owns will be permanently removed. This cannot be undone.
          </>
        }
      >
        <ul className="panel-inset space-y-1 px-3 py-2.5 text-xs text-muted">
          <li>· Sandbox container and its workspace volume</li>
          <li>· The project&apos;s application database and all its data</li>
          <li>· Conversation, designs, requirements, tests and deployments</li>
        </ul>
      </ConfirmDialog>
    </div>
  );
}
