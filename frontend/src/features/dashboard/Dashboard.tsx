import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AnimatePresence, motion } from 'framer-motion';
import { ArrowRight, FolderPlus, Plus, Trash2 } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { Button, ConfirmDialog, EmptyState, Input, Modal, Skeleton } from '../../components/ui';
import { cn } from '../../lib/cn';
import { ApiError } from '../../lib/apiClient';
import { hourIST } from '../../lib/datetime';
import { EASE } from '../../lib/motion';
import { useAuthStore } from '../../lib/stores/authStore';
import { toast } from '../../lib/stores/toastStore';
import { useUiStore } from '../../lib/stores/uiStore';
import { STAGE_ICONS, STAGE_LABELS, STAGE_ORDER } from '../workspace/stageMeta';
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
  const [, ms, unit] = UNITS.find(([limit]) => elapsed < limit) as [
    number,
    number,
    Intl.RelativeTimeFormatUnit,
  ];
  return format.format(-Math.round(elapsed / ms), unit);
}

function timeGreeting(): string {
  const hour = hourIST();
  if (hour < 5) return 'Working late';
  if (hour < 12) return 'Good morning';
  if (hour < 18) return 'Good afternoon';
  return 'Good evening';
}

/** A figure in the summary line: the number carries the weight, the label stays quiet. */
function Figure({ value, label }: { value: number | string; label: string }): JSX.Element {
  return (
    <span className="flex items-baseline gap-1.5 whitespace-nowrap">
      <span className="text-[15px] font-semibold tabular-nums text-fg">{value}</span>
      <span className="text-[13px] text-fg-subtle">{label}</span>
    </span>
  );
}

/*
 * Each stage gets a step along one hue: early stages light, late stages deep. The bar then reads as
 * "how far along is everything" at a glance, which six identically-coloured bars never did.
 */
const STAGE_SHADE: Record<string, string> = {
  requirements: 'bg-brand/25',
  design: 'bg-brand/40',
  build: 'bg-brand/55',
  test: 'bg-brand/70',
  deploy: 'bg-brand/85',
  validate: 'bg-success/80',
};

/**
 * Where every project sits in the pipeline, as one strip: a segmented bar (each stage's share of the
 * projects) over a legend of counts. Real information from the same list the rows draw on.
 */
function PipelinePulse({ projects }: { projects: { current_stage: string }[] }): JSX.Element {
  const counts = useMemo(() => {
    const byStage = Object.fromEntries(STAGE_ORDER.map((s) => [s, 0])) as Record<string, number>;
    for (const p of projects) {
      if (p.current_stage in byStage) byStage[p.current_stage] += 1;
    }
    return byStage;
  }, [projects]);
  const total = Math.max(1, projects.length);

  return (
    <div
      className="rounded-xl border border-edge bg-surface px-4 py-3.5"
      data-testid="pipeline-pulse"
      aria-label="Projects per stage"
    >
      <div className="flex h-1.5 gap-0.5 overflow-hidden rounded-full bg-fg/[0.06]">
        {STAGE_ORDER.map((stage) =>
          counts[stage] > 0 ? (
            <div
              key={stage}
              title={`${STAGE_LABELS[stage]}: ${counts[stage]}`}
              className={cn('h-full first:rounded-l-full last:rounded-r-full', STAGE_SHADE[stage])}
              style={{ width: `${(counts[stage] / total) * 100}%` }}
            />
          ) : null,
        )}
      </div>
      <ul className="mt-3 grid grid-cols-3 gap-x-4 gap-y-2 sm:grid-cols-6">
        {STAGE_ORDER.map((stage) => {
          const Icon = STAGE_ICONS[stage];
          const count = counts[stage];
          return (
            <li key={stage} className="flex min-w-0 items-center gap-2">
              <span aria-hidden className={cn('h-2 w-2 shrink-0 rounded-sm', STAGE_SHADE[stage])} />
              <Icon aria-hidden className="h-3.5 w-3.5 shrink-0 text-fg-faint" strokeWidth={1.75} />
              <span className="truncate text-xs text-fg-subtle">{STAGE_LABELS[stage]}</span>
              <span
                className={cn(
                  'ml-auto text-xs font-medium tabular-nums',
                  count > 0 ? 'text-fg' : 'text-fg-faint',
                )}
              >
                {count}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** Six slots, one per stage: past ones filled, the current one wider, the rest empty. */
function StageProgress({ current }: { current: string }): JSX.Element {
  const idx = STAGE_ORDER.indexOf(current as (typeof STAGE_ORDER)[number]);
  return (
    <span className="flex items-center gap-[3px]" aria-label={`Current stage: ${current}`}>
      {STAGE_ORDER.map((stage, i) => (
        <span
          key={stage}
          title={STAGE_LABELS[stage]}
          className={cn(
            'h-1 rounded-full transition-colors',
            i < idx && 'w-3.5 bg-brand-text/50',
            i === idx && 'w-5 bg-brand-text',
            i > idx && 'w-3.5 bg-fg/[0.1]',
          )}
        />
      ))}
    </span>
  );
}

/** Ghost of a project row — same bones as the real one, so the load never jumps layout. */
function RowSkeleton(): JSX.Element {
  return (
    <li className="flex h-14 items-center gap-3 border-b border-edge px-4 last:border-b-0">
      <Skeleton className="h-7 w-7 rounded-lg" />
      <Skeleton className="h-3.5 w-36" />
      <Skeleton className="ml-auto h-1.5 w-28" />
      <Skeleton className="h-3 w-24" />
    </li>
  );
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
  const user = useAuthStore((s) => s.user);

  // The command palette's "New project" lands here: each bump of the tick opens the dialog.
  const newProjectTick = useUiStore((s) => s.newProjectTick);
  const seenTick = useRef(newProjectTick);
  useEffect(() => {
    if (newProjectTick !== seenTick.current) {
      seenTick.current = newProjectTick;
      setModalOpen(true);
    }
  }, [newProjectTick]);

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
  const WEEK = 7 * 24 * 60 * 60 * 1000;
  const activeThisWeek = projects.filter(
    (p) => Date.now() - new Date(p.updated_at).getTime() < WEEK,
  ).length;
  const lastTouched = projects.length
    ? [...projects].sort((a, b) => b.updated_at.localeCompare(a.updated_at))[0].updated_at
    : null;
  const firstName = (user?.email ?? '').split('@')[0];

  return (
    // Capped width: on an ultrawide the list otherwise stretches until the page reads as empty.
    <div className="mx-auto max-w-6xl space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-[22px] font-semibold tracking-tight text-fg">
            {timeGreeting()}
            {firstName ? <>, {firstName}</> : null}
          </h1>
          <p className="mt-1 text-[13px] text-fg-subtle">
            Take an idea from design to a deployed URL.
          </p>
        </div>
        <Button onClick={() => setModalOpen(true)}>
          <Plus aria-hidden className="h-4 w-4" />
          New project
        </Button>
      </div>

      {projectsQuery.isSuccess && projects.length > 0 ? (
        <>
          <div
            className="flex flex-wrap items-baseline gap-x-6 gap-y-2 border-y border-edge py-3"
            data-testid="dashboard-stats"
          >
            <Figure
              value={projects.length}
              label={projects.length === 1 ? 'project' : 'projects'}
            />
            <Figure value={activeThisWeek} label="active this week" />
            <span className="flex items-baseline gap-1.5 whitespace-nowrap text-[13px] text-fg-subtle">
              Last activity
              <span className="font-medium text-fg-muted">
                {lastTouched ? relativeTime(lastTouched) : '—'}
              </span>
            </span>
          </div>
          <PipelinePulse projects={projects} />
        </>
      ) : null}

      {projectsQuery.isLoading ? (
        <ul className="overflow-hidden rounded-xl border border-edge bg-surface" aria-hidden>
          {Array.from({ length: 5 }, (_, i) => (
            <RowSkeleton key={i} />
          ))}
        </ul>
      ) : projects.length === 0 ? (
        <div className="rounded-xl border border-dashed border-edge-strong">
          <EmptyState
            icon={<FolderPlus aria-hidden strokeWidth={1.5} />}
            title="No projects yet"
            description="Create your first project to open a workspace."
            action={
              <Button onClick={() => setModalOpen(true)}>
                <Plus aria-hidden className="h-4 w-4" />
                New project
              </Button>
            }
          />
        </div>
      ) : (
        <section>
          <div className="mb-2 flex items-baseline justify-between px-1">
            <h2 className="text-xs font-medium text-fg-muted">Projects</h2>
            <span className="text-2xs text-fg-faint">Newest first</span>
          </div>
          <ul
            className="overflow-hidden rounded-xl border border-edge bg-surface"
            data-testid="project-list"
          >
            {/* `layout` lets the list close the gap when a project is deleted instead of jump-cutting;
                the exiting row folds away. Entrance is a CSS stagger, played once per row mount. */}
            <AnimatePresence initial={false}>
              {projects.map((project, index) => (
                // The open-project control and the delete control are SIBLINGS (a button cannot
                // nest a button), with delete floated over the row's end, revealed on hover/focus.
                <motion.li
                  key={project.id}
                  layout="position"
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ duration: 0.25, ease: EASE }}
                  data-project-card
                  style={{ animationDelay: `${Math.min(index, 12) * 30}ms` }}
                  className="group relative overflow-hidden border-b border-edge last:border-b-0 motion-safe:animate-ff-rise-in"
                >
                  <button
                    type="button"
                    data-testid={`project-${project.id}`}
                    onClick={() => navigate(`/projects/${project.id}`)}
                    className="flex min-h-[3.5rem] w-full items-center gap-3 px-4 py-2.5 pr-14 text-left transition-colors duration-150 hover:bg-fg/[0.025]"
                  >
                    <span
                      aria-hidden
                      className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-fg/[0.06] text-xs font-semibold text-fg-muted transition-colors group-hover:bg-fg/[0.09] group-hover:text-fg"
                    >
                      {project.name.charAt(0).toUpperCase()}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium text-fg">
                        {project.name}
                      </span>
                      <span className="mt-0.5 block truncate font-mono text-2xs text-fg-faint sm:hidden">
                        {STAGE_LABELS[project.current_stage]}
                      </span>
                    </span>
                    <span className="hidden w-48 shrink-0 items-center gap-2.5 sm:flex">
                      <StageProgress current={project.current_stage} />
                      <span className="text-xs text-fg-muted">
                        {STAGE_LABELS[project.current_stage]}
                      </span>
                    </span>
                    <span className="hidden w-40 shrink-0 truncate font-mono text-2xs text-fg-faint lg:block">
                      {project.stack}
                    </span>
                    <span className="hidden w-32 shrink-0 text-right text-xs tabular-nums text-fg-subtle md:block">
                      {relativeTime(project.updated_at)}
                    </span>
                    <ArrowRight
                      aria-hidden
                      className="h-3.5 w-3.5 shrink-0 -translate-x-1 text-fg-faint opacity-0 transition-[opacity,transform] duration-150 group-hover:translate-x-0 group-hover:opacity-100"
                      strokeWidth={2}
                    />
                  </button>
                  <button
                    type="button"
                    aria-label={`Delete ${project.name}`}
                    title={`Delete ${project.name}`}
                    data-testid={`delete-project-${project.id}`}
                    onClick={() => setDoomed({ id: project.id, name: project.name })}
                    className="absolute right-3 top-1/2 -translate-y-1/2 rounded-md p-1.5 text-fg-faint opacity-0 transition hover:bg-danger/10 hover:text-danger focus-visible:opacity-100 group-hover:opacity-100 [@media(hover:none)]:opacity-100"
                  >
                    <Trash2 aria-hidden className="h-3.5 w-3.5" strokeWidth={1.75} />
                  </button>
                </motion.li>
              ))}
            </AnimatePresence>
          </ul>
        </section>
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
            This permanently deletes <span className="font-medium text-fg">{doomed?.name}</span> —
            its sandbox, its database, its conversation, and every artifact. There is no undo.
          </>
        }
        confirmPhrase={doomed?.name}
        confirmLabel="Delete forever"
        loading={isDeleting}
        onConfirm={() => {
          if (!doomed) return;
          remove(doomed.id, { onDeleted: () => setDoomed(null) });
        }}
      />
    </div>
  );
}
