import {
  ArrowRight,
  Check,
  GitBranch,
  ImagePlus,
  Radar,
  RefreshCcw,
  Rocket,
  ShieldCheck,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { Link } from 'react-router-dom';

import { ThemeToggle } from '../../app/layout/ThemeToggle';
import { AmbientBackground } from '../../components/AmbientBackground';
import { BrandMark } from '../../components/BrandMark';
import { Button } from '../../components/ui';
import { cn } from '../../lib/cn';
import { motionOK, prefersReducedMotion } from '../../lib/motion';
import { useAuthStore } from '../../lib/stores/authStore';
import { STAGE_ICONS, STAGE_LABELS, STAGE_ORDER } from '../workspace/stageMeta';

/**
 * The public face of ForgeFlow (`/`). Built from the same semantic tokens as the app, so the
 * landing and the product read as one system in both themes.
 *
 * Motion is deliberately small and all of it is optional: a one-time entrance, sections that ease
 * in as they arrive, a pipeline demo that runs once and rests on its finished frame, and one
 * scroll-driven example (the repair loop). It is plain CSS plus an IntersectionObserver and a
 * single throttled scroll listener — no animation library — and with reduced motion, in tests or
 * without script, every element simply renders in its final state.
 *
 * Copy discipline: no invented logos, testimonials, or metrics. Every number and claim on this
 * page is a real property of the system.
 */

// ---------------------------------------------------------------------------------------------
// Shared bits

function scrollToSection(id: string): void {
  document.getElementById(id)?.scrollIntoView({
    behavior: prefersReducedMotion() ? 'auto' : 'smooth',
  });
}

function AnchorLink({ id, children }: { id: string; children: string }): JSX.Element {
  return (
    <a
      href={`#${id}`}
      onClick={(e) => {
        e.preventDefault();
        scrollToSection(id);
      }}
      className="rounded-lg px-3 py-2 text-sm text-fg-muted transition-colors hover:text-fg"
    >
      {children}
    </a>
  );
}

/**
 * Ease an element in the first time it scrolls into view. The element is only hidden once this
 * hook has confirmed it can show it again (see the `data-reveal` rules in index.css).
 */
function useReveal<T extends HTMLElement>(): React.RefObject<T> {
  const ref = useRef<T>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el || !motionOK() || typeof IntersectionObserver === 'undefined') return;
    el.dataset.reveal = 'pending';
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) {
          el.dataset.reveal = 'shown';
          observer.disconnect();
        }
      },
      { rootMargin: '0px 0px -12% 0px' },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return ref;
}

/** A wrapper that reveals its children on arrival; `delay` staggers siblings. */
function Reveal({
  children,
  delay = 0,
  className,
  as: Tag = 'div',
}: {
  children: ReactNode;
  delay?: number;
  className?: string;
  as?: 'div' | 'li';
}): JSX.Element {
  const ref = useReveal<HTMLDivElement>();
  return (
    <Tag
      ref={ref as React.RefObject<HTMLDivElement & HTMLLIElement>}
      className={className}
      style={{ '--reveal-delay': `${delay}ms` } as CSSProperties}
    >
      {children}
    </Tag>
  );
}

/** A section heading that eases in as it arrives. */
function SectionHeading({ children }: { children: ReactNode }): JSX.Element {
  const ref = useReveal<HTMLHeadingElement>();
  return (
    <h2 ref={ref} className="text-2xl font-semibold tracking-tight text-fg sm:text-3xl">
      {children}
    </h2>
  );
}

/** Staggered one-time entrance for the hero's blocks. */
const rise = (i: number): CSSProperties => ({ animationDelay: `${120 + i * 90}ms` });

// ---------------------------------------------------------------------------------------------
// Sections

function Nav(): JSX.Element {
  const token = useAuthStore((s) => s.token);
  const ref = useRef<HTMLElement | null>(null);

  // Past the hero the bar condenses and gains a seam — the chrome acknowledging you have left the
  // top of the page. A data attribute (not React state) so crossing the threshold costs no render.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let frame = 0;
    const update = (): void => {
      frame = 0;
      el.dataset.scrolled = String(window.scrollY > 72);
    };
    const onScroll = (): void => {
      if (!frame) frame = requestAnimationFrame(update);
    };
    update();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      window.removeEventListener('scroll', onScroll);
      cancelAnimationFrame(frame);
    };
  }, []);

  return (
    <header
      ref={ref}
      className="group fixed inset-x-0 top-0 z-40 border-b border-transparent bg-surface-canvas/[0.97] transition-colors duration-300 data-[scrolled=true]:border-edge"
    >
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4 transition-[height] duration-300 group-data-[scrolled=true]:h-14 sm:px-6">
        <Link to="/" className="flex items-center gap-2.5">
          <BrandMark size={24} />
          <span className="text-[15px] font-semibold tracking-tight text-fg">ForgeFlow</span>
        </Link>
        <nav className="hidden items-center gap-1 sm:flex" aria-label="Landing sections">
          <AnchorLink id="features">Features</AnchorLink>
          <AnchorLink id="workflow">Workflow</AnchorLink>
        </nav>
        <div className="flex items-center gap-2">
          <ThemeToggle />
          {token ? (
            <Link to="/dashboard">
              <Button size="sm">Open dashboard</Button>
            </Link>
          ) : (
            <>
              <Link to="/login" className="hidden sm:block">
                <Button size="sm" variant="ghost">
                  Sign in
                </Button>
              </Link>
              <Link to="/register">
                <Button size="sm">Get started</Button>
              </Link>
            </>
          )}
        </div>
      </div>
    </header>
  );
}

/**
 * The six-stage pipeline, run once: each stage activates, completes and hands off, and the card
 * then rests on the finished frame. (It used to loop forever — a demo that never stops reads as a
 * screensaver, not as a run that finished.)
 */
function PipelineDemo(): JSX.Element {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const root = ref.current;
    if (!root || !motionOK()) return;
    const chips = Array.from(root.querySelectorAll<HTMLElement>('[data-demo-stage]'));
    const status = root.querySelector<HTMLElement>('[data-demo-status]');
    const STEP = 650;
    const START = 900;
    for (const chip of chips) chip.dataset.state = 'idle';
    if (status) status.dataset.done = 'false';
    const timers: number[] = [];
    chips.forEach((chip, i) => {
      timers.push(window.setTimeout(() => (chip.dataset.state = 'active'), START + i * STEP));
      timers.push(
        window.setTimeout(() => (chip.dataset.state = 'done'), START + i * STEP + STEP - 80),
      );
    });
    timers.push(
      window.setTimeout(
        () => {
          if (status) status.dataset.done = 'true';
        },
        START + chips.length * STEP,
      ),
    );
    return () => timers.forEach((t) => window.clearTimeout(t));
  }, []);

  return (
    <div
      ref={ref}
      className="relative mx-auto mt-14 w-full max-w-3xl overflow-hidden rounded-xl border border-edge bg-surface text-left shadow-pop"
    >
      {/* Window chrome without the three decorative traffic-light dots: the path is the only
          thing in a title bar that carries information, so it is the only thing here. */}
      <div className="flex items-center gap-3 border-b border-edge px-4 py-2.5">
        <span className="font-mono text-2xs text-fg-subtle">forgeflow.local</span>
        <span aria-hidden className="h-3 w-px bg-edge" />
        <span className="truncate font-mono text-2xs text-fg-muted">/projects/demo</span>
      </div>

      <div className="grid gap-2 p-4 sm:grid-cols-2 lg:grid-cols-3">
        {STAGE_ORDER.map((stage) => {
          const Icon = STAGE_ICONS[stage];
          // The resting frame (also the no-motion frame) is the finished run.
          return (
            <div
              key={stage}
              data-demo-stage
              data-state="done"
              className="group flex items-center gap-3 rounded-lg border border-edge bg-fg/[0.02] px-3 py-2.5 transition-colors duration-300 data-[state=active]:border-brand/50 data-[state=active]:bg-brand/[0.08] data-[state=done]:border-success/25"
            >
              <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-edge-strong bg-surface text-fg-faint transition-colors duration-300 group-data-[state=active]:border-brand/60 group-data-[state=active]:text-brand-text group-data-[state=done]:border-success/50 group-data-[state=done]:text-success">
                <Icon aria-hidden className="h-3.5 w-3.5" strokeWidth={1.75} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium text-fg">
                  {STAGE_LABELS[stage]}
                </span>
                <span className="block text-2xs text-fg-subtle transition-colors duration-300 group-data-[state=active]:text-brand-text group-data-[state=done]:text-success">
                  <span className="hidden group-data-[state=idle]:inline">queued</span>
                  <span className="hidden group-data-[state=active]:inline">running…</span>
                  <span className="hidden group-data-[state=done]:inline">complete</span>
                </span>
              </span>
              <span
                aria-hidden
                className="h-1.5 w-1.5 shrink-0 rounded-full bg-fg-faint/50 transition-colors duration-300 group-data-[state=active]:bg-brand-text group-data-[state=done]:bg-success"
              />
            </div>
          );
        })}
      </div>

      <div
        data-demo-status
        data-done="true"
        className="group/status flex items-center gap-1.5 border-t border-edge bg-surface-sunken/60 px-4 py-2.5 font-mono text-xs text-fg-subtle"
      >
        <Check
          aria-hidden
          className="h-3.5 w-3.5 shrink-0 text-fg-faint transition-colors duration-300 group-data-[done=true]/status:text-success"
          strokeWidth={2.5}
        />
        <span>
          tests passing · repair loop idle · <span className="text-brand-text">agent</span> waiting
          for you
        </span>
      </div>
    </div>
  );
}

/*
 * Four facts, each verifiable in the codebase. The model line deliberately does NOT name a vendor:
 * `LLM_PROVIDER` is operator-configurable (Anthropic, or any OpenAI-compatible endpoint), so
 * "2 Claude models" would be false for every deployment that repoints it. What is always true is
 * the shape — two model roles, a cheap one for routing and a capable one for codegen.
 */
const STATS: Array<{ value: number; label: string }> = [
  { value: 6, label: 'stages, none mandatory' },
  { value: 2, label: 'model roles, cost-routed' },
  { value: 1, label: 'sandbox per project' },
  { value: 0, label: 'secrets in your browser' },
];

function Hero(): JSX.Element {
  const token = useAuthStore((s) => s.token);

  return (
    <section className="relative overflow-hidden px-4 pb-12 pt-32 sm:px-6 sm:pt-36">
      {/* The backdrop is the shared static grid. No orbs, no halo, no pointer glow: the headline
          and the pipeline below it are the composition. */}
      <AmbientBackground intensity="bold" />

      <div className="relative mx-auto max-w-6xl text-center">
        {/* One colour, one weight, arriving as one block. Hierarchy comes from size and from what
            the sentence says — not a dimmed second line, an eyebrow label, or a word-by-word
            reveal performing the headline at you. */}
        <h1
          className="mx-auto max-w-3xl text-4xl font-semibold leading-[1.05] tracking-[-0.025em] text-fg motion-safe:animate-ff-rise sm:text-5xl lg:text-[3.75rem]"
          style={rise(0)}
        >
          <span className="block">From idea to deployed app,</span>
          <span className="block">in one guided flow.</span>
        </h1>

        <p
          className="mx-auto mt-6 max-w-2xl text-base leading-relaxed text-fg-muted motion-safe:animate-ff-rise sm:text-[17px]"
          style={rise(1)}
        >
          ForgeFlow takes a web-app idea — screenshots or a spec — through design, requirements,
          build, test, self-healing repair, deploy, and live validation. You stay in the loop at
          every stage, free to refine, skip, or jump back.
        </p>

        <div
          className="mt-8 flex flex-wrap items-center justify-center gap-3 motion-safe:animate-ff-rise"
          style={rise(2)}
        >
          <Link to={token ? '/dashboard' : '/register'} className="group inline-flex">
            <Button size="lg" className="px-5">
              Start building
              <ArrowRight
                aria-hidden
                className="h-4 w-4 transition-transform duration-200 group-hover:translate-x-0.5"
              />
            </Button>
          </Link>
          <Button
            variant="secondary"
            size="lg"
            className="px-5"
            onClick={() => scrollToSection('workflow')}
          >
            See how it works
          </Button>
        </div>

        <div className="motion-safe:animate-ff-rise" style={rise(3)}>
          <PipelineDemo />
        </div>

        {/* One inline fact strip rather than four identical metric widgets. Static numbers: a
            count-up from zero told the reader "0 stages" until it happened to fire. */}
        <dl
          className="mx-auto mt-12 flex flex-wrap items-baseline justify-center gap-x-8 gap-y-3 border-t border-edge pt-6 motion-safe:animate-ff-rise"
          style={rise(4)}
        >
          {STATS.map((stat) => (
            <div key={stat.label} className="flex items-baseline gap-2">
              <dt className="sr-only">{stat.label}</dt>
              <dd className="flex items-baseline gap-2">
                <span className="text-lg font-semibold tabular-nums text-fg">{stat.value}</span>
                <span className="text-xs text-fg-subtle">{stat.label}</span>
              </dd>
            </div>
          ))}
        </dl>
      </div>
    </section>
  );
}

interface Feature {
  icon: LucideIcon;
  title: string;
  body: string;
}

/** The differentiator. Gets the large panel and the page's only concrete artifact. */
const LEAD_FEATURE: Feature = {
  icon: RefreshCcw,
  title: 'Self-healing repair',
  body: 'Failing tests feed a bounded, diff-aware repair loop that patches, re-runs, and knows when to hand back to you. It cannot run forever — every loop has a ceiling, and stops the moment it stops making progress.',
};

/** The two that carry the most weight after it — compact panels beside the lead. */
const SIDE_FEATURES: Feature[] = [
  {
    icon: GitBranch,
    title: 'Non-linear by design',
    body: 'No forced order. Start at any stage, skip ahead, or jump back — every stage tolerates missing upstream work.',
  },
  {
    icon: ShieldCheck,
    title: 'Sandboxed execution',
    body: 'Generated code runs in an isolated container with no host access — never in the control plane.',
  },
];

/** The remaining three, as a text band with no card chrome. */
const ROW_FEATURES: Feature[] = [
  {
    icon: ImagePlus,
    title: 'Design from screenshots',
    body: 'Drop in screenshots or a structured spec and get an editable design back, before a line of code exists.',
  },
  {
    icon: Rocket,
    title: 'One-step deploy',
    body: 'Frontend and backend to Vercel (Render if you prefer), with the database wired in from an analyzed deployment plan.',
  },
  {
    icon: Radar,
    title: 'Live validation',
    body: 'End-to-end Playwright checks run against the real deployed URL — proof, not a simulation.',
  },
];

function Features(): JSX.Element {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const readoutRef = useRef<HTMLSpanElement | null>(null);

  /*
   * The repair loop, scrubbed by the scrollbar: scrolling through the panel *runs the loop* — the
   * failing count drains 4 → 0, the trail lights one step at a time, and the panel turns success-
   * green on the frame it converges — and scrolling back up un-runs it. One passive, frame-
   * throttled scroll listener; no animation library.
   */
  useEffect(() => {
    const panel = panelRef.current;
    const readout = readoutRef.current;
    if (!panel || !readout || !motionOK()) return;
    const trail = Array.from(panel.querySelectorAll<HTMLElement>('[data-trail-step]'));

    const paint = (remaining: number): void => {
      readout.textContent = String(remaining);
      panel.dataset.converged = String(remaining === 0);
      trail.forEach((step, i) => {
        step.dataset.lit = String(4 - remaining > i);
      });
    };

    let frame = 0;
    const update = (): void => {
      frame = 0;
      const rect = panel.getBoundingClientRect();
      const vh = window.innerHeight;
      // From the panel's top crossing 78% of the viewport to its bottom crossing 62%.
      const travel = 0.16 * vh + rect.height;
      const progress = Math.min(1, Math.max(0, (0.78 * vh - rect.top) / travel));
      paint(Math.round(4 * (1 - progress)));
    };
    const onScroll = (): void => {
      if (!frame) frame = requestAnimationFrame(update);
    };
    update();
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', onScroll);
    return () => {
      window.removeEventListener('scroll', onScroll);
      window.removeEventListener('resize', onScroll);
      cancelAnimationFrame(frame);
    };
  }, []);

  return (
    <section id="features" className="relative scroll-mt-20 px-4 py-20 sm:px-6">
      <div className="relative mx-auto max-w-6xl">
        {/* Left-aligned, not centered: a centered heading over a symmetric grid is the shape every
            generated landing page takes. The section leads from the edge, like a page of prose. */}
        <div className="max-w-2xl">
          <SectionHeading>Everything between the idea and the URL</SectionHeading>
          <p className="mt-3 text-fg-muted">
            One workspace owns the whole journey, with you in the loop at every stage.
          </p>
        </div>

        {/*
          Deliberately asymmetric. The repair loop is the one thing here no other tool does, so it
          gets the large panel and the only concrete artifact on the page; two supporting features
          stack beside it; the remaining three sit in a hairline-divided band with no card chrome
          at all. Three densities, one focal point — the opposite of six equal boxes.
        */}
        <div className="mt-12 grid gap-4 lg:grid-cols-12">
          <Reveal className="lg:col-span-7">
            <div
              ref={panelRef}
              // Rendered CONVERGED: with motion off the scrub never runs, and a panel resting
              // forever on "4 failing" would tell the opposite of the story.
              data-converged="true"
              className="group h-full rounded-xl border border-edge bg-surface p-7 transition-colors duration-500 hover:border-edge-strong data-[converged=true]:border-success/35"
            >
              <h3 className="flex items-center gap-2.5 text-lg font-semibold text-fg">
                <LEAD_FEATURE.icon
                  aria-hidden
                  className="h-[18px] w-[18px] text-brand-text"
                  strokeWidth={1.75}
                />
                {LEAD_FEATURE.title}
              </h3>
              <p className="mt-2 max-w-md leading-relaxed text-fg-muted">{LEAD_FEATURE.body}</p>

              {/* Labelled "example run" because the counts are illustrative — every other number
                  on this page is a claim that holds for any deployment. */}
              <div className="mt-6 border-t border-edge pt-4">
                <div className="flex items-baseline justify-between gap-4">
                  <p className="text-xs text-fg-faint">Example run</p>
                  <p className="flex items-baseline gap-1.5 font-mono text-xs text-fg-subtle">
                    <span
                      ref={readoutRef}
                      className="text-2xl font-semibold tabular-nums text-warning transition-colors duration-300 group-data-[converged=true]:text-success"
                    >
                      0
                    </span>
                    failing
                  </p>
                </div>
                <div className="mt-3 flex flex-wrap items-center gap-2 font-mono text-xs">
                  {['run tests', 'patch reducer', 'patch guard', 'patch fixture'].map(
                    (label, i) => (
                      <span key={label} className="flex items-center gap-2">
                        <span
                          data-trail-step
                          data-lit="true"
                          className="rounded-md border border-edge px-1.5 py-0.5 text-fg-faint transition-colors duration-300 data-[lit=true]:border-brand/40 data-[lit=true]:bg-brand/10 data-[lit=true]:text-brand-text"
                        >
                          {label}
                        </span>
                        {i < 3 ? (
                          <span aria-hidden className="text-fg-faint">
                            →
                          </span>
                        ) : null}
                      </span>
                    ),
                  )}
                  <span className="text-fg-subtle transition-colors duration-300 group-data-[converged=true]:text-success">
                    · loop exits
                  </span>
                </div>
              </div>
            </div>
          </Reveal>

          <div className="grid gap-4 lg:col-span-5">
            {SIDE_FEATURES.map((feature, i) => (
              <Reveal
                key={feature.title}
                delay={80 + i * 80}
                className="group rounded-xl border border-edge bg-surface p-6 transition-colors duration-200 hover:border-edge-strong"
              >
                <h3 className="flex items-center gap-2.5 text-sm font-semibold text-fg">
                  <feature.icon
                    aria-hidden
                    className="h-4 w-4 text-fg-subtle transition-colors group-hover:text-brand-text"
                    strokeWidth={1.75}
                  />
                  {feature.title}
                </h3>
                <p className="mt-2 text-sm leading-relaxed text-fg-muted">{feature.body}</p>
              </Reveal>
            ))}
          </div>

          <div className="grid border-t border-edge sm:grid-cols-3 lg:col-span-12">
            {ROW_FEATURES.map((feature, i) => (
              <Reveal
                key={feature.title}
                delay={i * 80}
                className="group border-edge py-6 pr-6 sm:border-r sm:pl-6 sm:first:pl-0 sm:last:border-r-0"
              >
                <h3 className="flex items-center gap-2.5 text-sm font-semibold text-fg">
                  <feature.icon
                    aria-hidden
                    className="h-4 w-4 shrink-0 text-fg-subtle transition-colors group-hover:text-brand-text"
                    strokeWidth={1.75}
                  />
                  {feature.title}
                </h3>
                <p className="mt-2 text-sm leading-relaxed text-fg-muted">{feature.body}</p>
              </Reveal>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

/**
 * A miniature of what each stage actually produces, drawn with plain divs.
 *
 * The panels are as tall as a pinned viewport allows, and until now they held an icon, a heading
 * and two lines of copy — so the middle of every card was several hundred pixels of nothing, which
 * read as the section demanding space it had no use for. Filling it with a *claim* (a stat, a
 * flourish) would have been decoration; filling it with the stage's own artifact is the argument
 * the section is making. Deliberately schematic rather than a screenshot: it has to stay legible
 * at a glance while sliding past, and it must never go stale against the real UI.
 */
const STAGE_PREVIEWS: Record<string, JSX.Element> = {
  requirements: (
    <ul className="space-y-2">
      {[
        ['Users can add a todo', 'e2e'],
        ['Todos persist across reloads', 'unit'],
        ['Completed items can be filtered', 'either'],
      ].map(([label, kind]) => (
        <li
          key={label}
          className="flex items-center gap-2 rounded-lg border border-edge bg-surface-sunken px-2.5 py-2"
        >
          <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded border border-brand/50 bg-brand/10">
            <span className="h-1.5 w-1.5 rounded-[2px] bg-brand" />
          </span>
          <span className="min-w-0 flex-1 truncate text-[11px] text-fg-muted">{label}</span>
          <span className="font-mono text-2xs text-fg-faint">{kind}</span>
        </li>
      ))}
    </ul>
  ),
  design: (
    <div className="space-y-2">
      <div className="flex gap-1.5">
        {['bg-brand', 'bg-accent', 'bg-fg/20', 'bg-surface-raised'].map((tone) => (
          <span key={tone} className={`h-7 flex-1 rounded-md ${tone}`} />
        ))}
      </div>
      <div className="space-y-1.5 rounded-lg border border-edge bg-surface-sunken p-2.5">
        <span className="block h-2 w-1/3 rounded-full bg-fg/25" />
        <span className="block h-2 w-full rounded-full bg-fg/10" />
        <span className="block h-2 w-4/5 rounded-full bg-fg/10" />
        <div className="flex gap-1.5 pt-1">
          <span className="h-5 w-14 rounded-md bg-brand/70" />
          <span className="h-5 w-10 rounded-md bg-fg/10" />
        </div>
      </div>
    </div>
  ),
  build: (
    <ul className="space-y-1 rounded-lg border border-edge bg-surface-sunken p-2.5 font-mono text-[11px]">
      {[
        ['src/App.tsx', '+48'],
        ['src/components/TodoList.tsx', '+96'],
        ['backend/src/routes/todos.ts', '+72'],
        ['backend/src/models/Todo.ts', '+31'],
      ].map(([file, added]) => (
        <li key={file} className="flex items-center gap-2">
          <span className="min-w-0 flex-1 truncate text-fg-subtle">{file}</span>
          <span className="text-success">{added}</span>
        </li>
      ))}
    </ul>
  ),
  test: (
    <ul className="space-y-2">
      {[
        ['todos.spec.ts', 'passed', 'bg-success'],
        ['api.integration.ts', 'passed', 'bg-success'],
        ['filter.e2e.ts', 'repaired', 'bg-warning'],
      ].map(([name, state, tone]) => (
        <li
          key={name}
          className="flex items-center gap-2 rounded-lg border border-edge bg-surface-sunken px-2.5 py-2"
        >
          <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${tone}`} />
          <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-fg-muted">
            {name}
          </span>
          <span className="text-2xs text-fg-faint">{state}</span>
        </li>
      ))}
    </ul>
  ),
  deploy: (
    <div className="space-y-1.5">
      {[
        ['web', 'vercel'],
        ['api', 'vercel'],
        ['db', 'atlas'],
      ].map(([node, provider], i) => (
        <div key={node}>
          <div className="flex items-center gap-2 rounded-lg border border-edge bg-surface-sunken px-2.5 py-2">
            <span className="font-mono text-[11px] text-brand-text">{node}</span>
            <span className="ml-auto text-2xs text-fg-faint">{provider}</span>
            <span className="h-1.5 w-1.5 rounded-full bg-success" />
          </div>
          {i < 2 ? <span aria-hidden className="ml-4 block h-2 w-px bg-edge-strong" /> : null}
        </div>
      ))}
    </div>
  ),
  validate: (
    <ul className="space-y-2">
      {[
        ['Live URL responds', '200'],
        ['Create + read a todo', 'ok'],
        ['Data survives a reload', 'ok'],
      ].map(([check, result]) => (
        <li
          key={check}
          className="flex items-center gap-2 rounded-lg border border-edge bg-surface-sunken px-2.5 py-2"
        >
          <Check aria-hidden className="h-3.5 w-3.5 shrink-0 text-success" strokeWidth={2.5} />
          <span className="min-w-0 flex-1 truncate text-[11px] text-fg-muted">{check}</span>
          <span className="font-mono text-[10px] text-success">{result}</span>
        </li>
      ))}
    </ul>
  ),
};

const WORKFLOW_COPY: Record<string, string> = {
  requirements:
    'Say what the app must do — or let the agent draft it — as features and acceptance criteria you can edit and approve.',
  design:
    'Upload screenshots or describe the look; refine it in conversation before any code exists.',
  build:
    'The agent scaffolds and implements the app inside its sandbox — every file lands live in the workspace IDE.',
  test: 'Unit and end-to-end suites run in the sandbox; failures trigger the bounded repair loop automatically.',
  deploy: 'One action ships the frontend and backend to real hosting, wired together.',
  validate: 'Post-deploy checks prove the live URL actually works — the loop closes on evidence.',
};

/**
 * The pipeline as a scroll-spy: the six stages listed in a column that stays put, and each stage's
 * artifact beside it. Whichever artifact is in the middle of the viewport lights its stage, and the
 * rail beside the list fills to it; clicking a stage scrolls to its artifact.
 *
 * This replaced a pinned, horizontally scrubbed track. That version hijacked the scroll for six
 * viewport-widths and needed a GSAP pin, a container animation and per-panel triggers to stay in
 * step. Here the page scrolls the way pages scroll, the only machinery is one
 * IntersectionObserver, and with motion off nothing changes at all — it was never animation.
 */
function Workflow(): JSX.Element {
  const [active, setActive] = useState(0);
  const cardsRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const root = cardsRef.current;
    if (!root || typeof IntersectionObserver === 'undefined') return;
    const cards = Array.from(root.querySelectorAll<HTMLElement>('[data-stage-card]'));
    // A thin band across the middle of the viewport: the card crossing it is the one being read.
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) setActive(Number(entry.target.getAttribute('data-index')));
        }
      },
      { rootMargin: '-45% 0px -45% 0px' },
    );
    cards.forEach((card) => observer.observe(card));
    return () => observer.disconnect();
  }, []);

  function jumpTo(stage: string): void {
    document.getElementById(`stage-${stage}`)?.scrollIntoView({
      behavior: prefersReducedMotion() ? 'auto' : 'smooth',
      block: 'center',
    });
  }

  return (
    <section id="workflow" className="relative scroll-mt-20 px-4 py-24 sm:px-6">
      <div className="mx-auto max-w-6xl">
        <div className="max-w-2xl">
          <SectionHeading>Six stages. Your order.</SectionHeading>
          <p className="mt-3 text-fg-muted">
            The pipeline reads top to bottom, but nothing forces you through it that way — start
            anywhere, skip a stage, or go back to one. Each stage hands you something you can check.
          </p>
        </div>

        <div className="mt-14 grid gap-10 lg:grid-cols-[17rem_minmax(0,1fr)]">
          <nav
            aria-label="Pipeline stages"
            className="hidden self-start lg:sticky lg:top-28 lg:block"
          >
            <ol className="relative">
              {/* The rail, and how far down it you have read. */}
              <span aria-hidden className="absolute bottom-4 left-[0.9rem] top-4 w-px bg-edge" />
              <span
                aria-hidden
                className="absolute left-[0.9rem] top-4 w-px bg-brand-text transition-[height] duration-500 ease-out"
                style={{
                  height: `calc((100% - 2rem) * ${active / (STAGE_ORDER.length - 1)})`,
                }}
              />
              {STAGE_ORDER.map((stage, i) => {
                const Icon = STAGE_ICONS[stage];
                const isActive = i === active;
                const passed = i < active;
                return (
                  <li key={stage}>
                    <button
                      type="button"
                      onClick={() => jumpTo(stage)}
                      aria-current={isActive ? 'step' : undefined}
                      className="group relative flex w-full items-center gap-3 rounded-lg py-2 pr-2 text-left"
                    >
                      <span
                        className={cn(
                          'relative z-10 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border bg-surface-canvas transition-colors duration-300',
                          isActive
                            ? 'border-brand-text text-brand-text'
                            : passed
                              ? 'border-brand-text/50 text-brand-text/70'
                              : 'border-edge-strong text-fg-faint group-hover:text-fg-subtle',
                        )}
                      >
                        <Icon aria-hidden className="h-3.5 w-3.5" strokeWidth={1.75} />
                      </span>
                      <span
                        className={cn(
                          'text-sm transition-colors duration-300',
                          isActive
                            ? 'font-medium text-fg'
                            : 'text-fg-subtle group-hover:text-fg-muted',
                        )}
                      >
                        {STAGE_LABELS[stage]}
                      </span>
                      <span className="ml-auto font-mono text-2xs tabular-nums text-fg-faint">
                        {String(i + 1).padStart(2, '0')}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ol>
          </nav>

          <div ref={cardsRef} className="space-y-5">
            {STAGE_ORDER.map((stage, i) => {
              const Icon = STAGE_ICONS[stage];
              return (
                <article
                  key={stage}
                  id={`stage-${stage}`}
                  data-stage-card
                  data-index={i}
                  data-active={i === active}
                  className="scroll-mt-28 rounded-xl border border-edge bg-surface p-6 transition-colors duration-500 data-[active=true]:border-brand/35 sm:p-8"
                >
                  <div className="grid gap-6 md:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)] md:items-center">
                    <div>
                      <h3 className="flex items-center gap-2.5 text-xl font-semibold tracking-tight text-fg">
                        <Icon
                          aria-hidden
                          className="h-[18px] w-[18px] text-fg-subtle"
                          strokeWidth={1.75}
                        />
                        {STAGE_LABELS[stage]}
                      </h3>
                      <p className="mt-2 text-sm leading-relaxed text-fg-muted">
                        {WORKFLOW_COPY[stage]}
                      </p>
                    </div>
                    {/* What the stage hands you, not a picture of the stage. Hidden from
                        assistive tech: it restates the copy beside it in shapes. */}
                    <div aria-hidden className="rounded-lg bg-fg/[0.025] p-4">
                      {STAGE_PREVIEWS[stage]}
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
        </div>
      </div>
    </section>
  );
}

function CtaBanner(): JSX.Element {
  const token = useAuthStore((s) => s.token);

  // The closing ask as a plain band — heading on the left, one action on the right — not a boxed,
  // centred panel, which is the last block of every templated landing page.
  return (
    <section className="px-4 pb-24 pt-8 sm:px-6">
      <Reveal className="mx-auto flex max-w-6xl flex-col items-start justify-between gap-6 border-t border-edge pt-12 md:flex-row md:items-center">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight text-fg sm:text-3xl">
            Your next app is a conversation away.
          </h2>
          <p className="mt-2 max-w-xl text-fg-muted">
            Open a project, drop in a screenshot, and watch the pipeline pick it up.
          </p>
        </div>
        <Link to={token ? '/dashboard' : '/register'} className="group inline-flex shrink-0">
          <Button size="lg" className="px-5">
            {/* Not "free account" — there is no paid tier to be free of. */}
            {token ? 'Open your dashboard' : 'Create an account'}
            <ArrowRight
              aria-hidden
              className="h-4 w-4 transition-transform duration-200 group-hover:translate-x-0.5"
            />
          </Button>
        </Link>
      </Reveal>
    </section>
  );
}

function Footer(): JSX.Element {
  return (
    <footer className="border-t border-edge px-4 py-8 sm:px-6">
      <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 sm:flex-row">
        <div className="flex items-center gap-2.5">
          <BrandMark size={22} />
          <span className="text-sm font-medium text-fg">ForgeFlow</span>
          <span className="font-mono text-2xs text-fg-faint">v{__APP_VERSION__}</span>
        </div>
        <p className="text-xs text-fg-subtle">
          Requirements → design → build → test → deploy → validate. In any order you like.
        </p>
      </div>
    </footer>
  );
}

export default function Landing(): JSX.Element {
  return (
    <main className="min-h-screen bg-surface-canvas">
      <Nav />
      <Hero />
      <Features />
      <Workflow />
      <CtaBanner />
      <Footer />
    </main>
  );
}
