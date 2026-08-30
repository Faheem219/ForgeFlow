import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Activity, Clock3, FolderKanban, FolderPlus, Layers, Plus, Trash2 } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import {
  Badge,
  Button,
  ConfirmDialog,
  EmptyState,
  Input,
  Modal,
  Skeleton,
  SpotlightCard,
} from '../../components/ui';
import { cn } from '../../lib/cn';
import { ApiError } from '../../lib/apiClient';
import { hourIST } from '../../lib/datetime';
import { EASE_OUT, Flip, SplitText, gsap, motionOK, safeAnimate, useGSAP } from '../../lib/gsap';
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

/** Headline figure with an icon chip; numbers count up on load (see the data-stat-counter tween). */
function StatTile({
  icon: Icon,
  label,
  value,
}: {
  icon: LucideIcon;
  label: string;
  value: number | string;
}): JSX.Element {
  return (
    <div className="rounded-xl border border-edge bg-surface p-4 transition-colors hover:border-edge-strong">
      <span className="flex items-center gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-brand/10 text-brand-text ring-1 ring-inset ring-brand/20">
          <Icon aria-hidden className="h-[18px] w-[18px]" strokeWidth={1.75} />
        </span>
        <span className="min-w-0">
          <span className="block truncate text-2xl font-semibold leading-tight tabular-nums text-fg">
            {typeof value === 'number' ? (
              <span data-stat-counter data-to={value}>
                {value}
              </span>
            ) : (
              value
            )}
          </span>
          <span className="block text-xs text-fg-subtle">{label}</span>
        </span>
      </span>
    </div>
  );
}

/**
 * Where every project sits in the pipeline, as one strip: a count per stage and a bar showing its
 * share. Real information from the same list the cards draw on — it fills the first screen with
 * the product's own shape rather than decoration.
 */
function PipelinePulse({ projects }: { projects: { current_stage: string }[] }): JSX.Element {
  const counts = useMemo(() => {
    const byStage = Object.fromEntries(STAGE_ORDER.map((s) => [s, 0])) as Record<string, number>;
    for (const p of projects) {
      if (p.current_stage in byStage) byStage[p.current_stage] += 1;
    }
    return byStage;
  }, [projects]);
  const max = Math.max(1, ...Object.values(counts));

  return (
    <div
      className="grid grid-cols-3 gap-2 rounded-xl border border-edge bg-surface p-4 sm:grid-cols-6"
      data-testid="pipeline-pulse"
      aria-label="Projects per stage"
    >
      {STAGE_ORDER.map((stage) => {
        const Icon = STAGE_ICONS[stage];
        const count = counts[stage];
        return (
          <div key={stage} className="group/stage min-w-0">
            <div className="flex items-center gap-1.5 text-fg-subtle transition-colors group-hover/stage:text-fg-muted">
              <Icon aria-hidden className="h-3.5 w-3.5 shrink-0" strokeWidth={1.75} />
              <span className="truncate text-[11px]">{STAGE_LABELS[stage]}</span>
            </div>
            <p className="mt-1 text-lg font-semibold tabular-nums leading-none text-fg">
              <span data-stat-counter data-to={count}>
                {count}
              </span>
            </p>
            <div className="mt-2 h-1 overflow-hidden rounded-full bg-surface-raised">
              <div
                data-pulse-bar
                className={`h-full rounded-full ${count > 0 ? 'bg-brand' : 'bg-edge'}`}
                style={{ width: count > 0 ? `${Math.max(12, (count / max) * 100)}%` : '8%' }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** Six slots, one per stage: past ones dimmed brand, the current one solid brand and wider. */
function StageProgress({ current }: { current: string }): JSX.Element {
  const idx = STAGE_ORDER.indexOf(current as (typeof STAGE_ORDER)[number]);
  return (
    <span className="flex items-center gap-1" aria-label={`Current stage: ${current}`}>
      {STAGE_ORDER.map((stage, i) => (
        <span
          key={stage}
          title={STAGE_LABELS[stage]}
          className={cn(
            'h-1 rounded-full transition-colors',
            i < idx && 'w-4 bg-brand/60',
            i === idx && 'w-6 bg-brand',
            i > idx && 'w-4 bg-edge',
          )}
        />
      ))}
    </span>
  );
}

/** Ghost of a project card — same bones as the real one, so the load never jumps layout. */
function CardSkeleton(): JSX.Element {
  return (
    <li className="flex h-full flex-col gap-3 rounded-xl border border-edge bg-surface p-4">
      <div className="flex items-start justify-between gap-2">
        <span className="flex items-center gap-2.5">
          <Skeleton className="h-8 w-8 rounded-lg" />
          <Skeleton className="h-4 w-28" />
        </span>
        <Skeleton className="h-5 w-16 rounded-full" />
      </div>
      <Skeleton className="mt-auto h-3 w-40" />
    </li>
  );
}

export default function Dashboard(): JSX.Element {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const scope = useRef<HTMLDivElement | null>(null);
  const greetingRef = useRef<HTMLHeadingElement | null>(null);
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

  /*
   * Flip state captured the instant before a delete mutates the list. `Flip.from` then animates
   * every surviving card from where it *was* to where the grid has just put it, so removing a
   * project reads as the grid closing the gap rather than as a jump-cut. Null unless a delete is
   * in flight, so ordinary refetches never trigger a reflow.
   */
  const flipState = useRef<Flip.FlipState | null>(null);

  useGSAP(
    () => {
      const state = flipState.current;
      if (!state) return;
      flipState.current = null;
      safeAnimate('dashboard delete reflow', () => {
        Flip.from(state, {
          duration: 0.45,
          ease: 'power2.out',
          absolute: true,
          // The removed card is already gone; Flip re-inserts it just long enough to leave.
          onLeave: (elements) =>
            gsap.to(elements, { opacity: 0, scale: 0.92, duration: 0.25, ease: 'power2.in' }),
        });
      });
    },
    { dependencies: [projectsQuery.data?.length] },
  );

  // Cards cascade in once, when the list first resolves — not again on refetch or delete.
  // Stat figures count up in the same beat.
  useGSAP(
    () => {
      if (!motionOK() || !projectsQuery.isSuccess) return;

      /*
       * A plain stagger, deliberately.
       *
       * This was briefly a `ScrollTrigger.batch` bound to the shell's scroll container so that
       * below-the-fold cards waited to be scrolled to. It crashed the route
       * (`Cannot read properties of undefined (reading '_gsap')` out of ScrollTrigger's init) and
       * the payoff never justified it: the dashboard grid is three columns of short cards, so a
       * realistic project count is on screen already and there is nothing to defer. Scroll-driven
       * entrances belong on the landing page, where there is actually a page worth scrolling.
       */
      safeAnimate('dashboard entrance', () => {
        gsap.from('[data-project-card]', {
          opacity: 0,
          y: 20,
          duration: 0.5,
          ease: EASE_OUT,
          stagger: 0.05,
          clearProps: 'all',
        });

        // The greeting is uncovered rather than faded — the device the landing headings use.
        if (greetingRef.current) {
          const split = new SplitText(greetingRef.current, { type: 'lines', mask: 'lines' });
          gsap.from(split.lines, { yPercent: 110, duration: 0.6, ease: EASE_OUT });
        }

        gsap.utils.toArray<HTMLElement>('[data-stat-counter]').forEach((el) => {
          const target = Number(el.dataset.to ?? '0');
          const obj = { n: 0 };
          gsap.to(obj, {
            n: target,
            duration: 0.9,
            ease: 'power2.out',
            onUpdate: () => {
              el.textContent = String(Math.round(obj.n));
            },
          });
        });

        // The pipeline strip's bars grow out from the left in the same beat.
        gsap.from('[data-pulse-bar]', {
          scaleX: 0,
          transformOrigin: 'left center',
          duration: 0.7,
          ease: EASE_OUT,
          stagger: 0.06,
          clearProps: 'all',
        });
      });
    },
    { scope, dependencies: [projectsQuery.isSuccess] },
  );

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
    // Capped width: on an ultrawide the grid otherwise stretches until the page reads as empty.
    <div ref={scope} className="mx-auto max-w-7xl space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 ref={greetingRef} className="text-2xl font-semibold tracking-tight text-fg">
            {timeGreeting()}
            {firstName ? <>, {firstName}</> : null}
          </h1>
          <p className="mt-0.5 text-sm text-fg-muted">
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
          <div className="grid gap-3 sm:grid-cols-3" data-testid="dashboard-stats">
            <StatTile icon={FolderKanban} label="Projects" value={projects.length} />
            <StatTile icon={Activity} label="Active this week" value={activeThisWeek} />
            <StatTile
              icon={Clock3}
              label="Last activity"
              value={lastTouched ? relativeTime(lastTouched) : '—'}
            />
          </div>
          <PipelinePulse projects={projects} />
        </>
      ) : null}

      {projectsQuery.isLoading ? (
        <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" aria-hidden>
          {Array.from({ length: 6 }, (_, i) => (
            <CardSkeleton key={i} />
          ))}
        </ul>
      ) : projects.length === 0 ? (
        <EmptyState
          icon={<FolderPlus aria-hidden className="h-6 w-6" strokeWidth={1.5} />}
          title="No projects yet"
          description="Create your first project to open a workspace."
          action={
            <Button onClick={() => setModalOpen(true)}>
              <Plus aria-hidden className="h-4 w-4" />
              New project
            </Button>
          }
        />
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" data-testid="project-list">
          {projects.map((project) => (
            // The open-project control and the delete control are SIBLINGS (a button cannot nest a
            // button), with delete floated over the card's corner and revealed on hover/focus.
            <li key={project.id} data-project-card className="group relative">
              <SpotlightCard className="h-full rounded-xl">
                <button
                  type="button"
                  data-testid={`project-${project.id}`}
                  onClick={() => navigate(`/projects/${project.id}`)}
                  className="flex h-full w-full flex-col gap-3 rounded-xl border border-edge bg-surface p-4 text-left transition-colors duration-200 hover:border-edge-strong hover:bg-surface-raised"
                >
                  <span className="flex items-start justify-between gap-2">
                    <span className="flex min-w-0 items-center gap-2.5">
                      <span
                        aria-hidden
                        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-brand/20 bg-brand/10 text-sm font-semibold text-brand-text"
                      >
                        {project.name.charAt(0).toUpperCase()}
                      </span>
                      <span className="truncate font-medium text-fg">{project.name}</span>
                    </span>
                    <Badge tone="brand">{STAGE_LABELS[project.current_stage]}</Badge>
                  </span>
                  <StageProgress current={project.current_stage} />
                  <span className="mt-auto flex items-center gap-2 text-xs text-fg-subtle">
                    <Layers aria-hidden className="h-3 w-3" />
                    <span>{project.stack}</span>
                    <span aria-hidden>·</span>
                    <Clock3 aria-hidden className="h-3 w-3" />
                    <span>Updated {relativeTime(project.updated_at)}</span>
                  </span>
                </button>
              </SpotlightCard>
              <button
                type="button"
                aria-label={`Delete ${project.name}`}
                data-testid={`delete-project-${project.id}`}
                onClick={() => setDoomed({ id: project.id, name: project.name })}
                className="absolute bottom-3 right-3 rounded-md p-1.5 text-fg-subtle opacity-0 transition hover:bg-danger/10 hover:text-danger focus-visible:opacity-100 group-hover:opacity-100"
              >
                <Trash2 aria-hidden className="h-4 w-4" strokeWidth={1.75} />
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
          if (!doomed) return;
          // Capture the grid's geometry BEFORE the list loses this project — Flip needs the
          // "from" frame while the card is still on screen.
          if (motionOK()) flipState.current = Flip.getState('[data-project-card]');
          remove(doomed.id, { onDeleted: () => setDoomed(null) });
        }}
      />
    </div>
  );
}
