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
import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

import { ThemeToggle } from '../../app/layout/ThemeToggle';
import { AmbientBackground } from '../../components/AmbientBackground';
import { BrandMark } from '../../components/BrandMark';
import { Button, SpotlightCard } from '../../components/ui';
import {
  EASE_OUT,
  ScrollTrigger,
  SplitText,
  gsap,
  motionOK,
  prefersReducedMotion,
  reversibleHover,
  safeAnimate,
  useGSAP,
} from '../../lib/gsap';
import { useAuthStore } from '../../lib/stores/authStore';
import { STAGE_ICONS, STAGE_LABELS, STAGE_ORDER } from '../workspace/stageMeta';

/**
 * The public face of ForgeFlow (`/`). Everything here is built from the same semantic tokens as
 * the app, so the landing and the product read as one system in both themes. All choreography
 * goes through `lib/gsap.ts` and is skipped entirely under reduced motion — the page is complete
 * without a single animation.
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
 * Magnetic hover: the wrapped control leans toward the cursor and springs back on leave.
 * Pointer-only and gated on motion preference — on touch it is a plain wrapper.
 */
function Magnetic({ children }: { children: ReactNode }): JSX.Element {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el || !motionOK() || !window.matchMedia('(pointer: fine)').matches) return;
    const xTo = gsap.quickTo(el, 'x', { duration: 0.35, ease: 'power3.out' });
    const yTo = gsap.quickTo(el, 'y', { duration: 0.35, ease: 'power3.out' });
    // Arrows, not declarations: hoisted functions would out-run the null guard above.
    const move = (event: PointerEvent): void => {
      const rect = el.getBoundingClientRect();
      xTo((event.clientX - (rect.left + rect.width / 2)) * 0.3);
      yTo((event.clientY - (rect.top + rect.height / 2)) * 0.3);
    };
    const leave = (): void => {
      xTo(0);
      yTo(0);
    };
    el.addEventListener('pointermove', move);
    el.addEventListener('pointerleave', leave);
    return () => {
      el.removeEventListener('pointermove', move);
      el.removeEventListener('pointerleave', leave);
      gsap.killTweensOf(el);
    };
  }, []);

  return (
    <div ref={ref} className="inline-block">
      {children}
    </div>
  );
}

/**
 * Section heading whose lines rise out from behind a clipping mask as it enters view.
 *
 * Uses SplitText's native `mask: 'lines'` (3.13+), which wraps each line in its own overflow-hidden
 * parent — so the type appears to be uncovered rather than to fade in. Falls back to plain rendered
 * text whenever motion is off, since the split never runs.
 */
function MaskedHeading({
  children,
  className = '',
}: {
  children: ReactNode;
  className?: string;
}): JSX.Element {
  const ref = useRef<HTMLHeadingElement | null>(null);

  useGSAP(() => {
    const el = ref.current;
    if (!motionOK() || !el) return;
    const split = new SplitText(el, { type: 'lines', mask: 'lines' });
    gsap.from(split.lines, {
      yPercent: 115,
      duration: 0.8,
      ease: EASE_OUT,
      stagger: 0.1,
      scrollTrigger: { trigger: el, start: 'top 88%', once: true },
    });
    return () => split.revert();
  });

  return (
    <h2 ref={ref} className={className}>
      {children}
    </h2>
  );
}

/** Hairline reading-progress bar pinned above the nav, scrubbed against the whole page. */
function ScrollProgress(): JSX.Element {
  const ref = useRef<HTMLSpanElement | null>(null);

  useGSAP(() => {
    if (!motionOK() || !ref.current) return;
    gsap.fromTo(
      ref.current,
      { scaleX: 0 },
      {
        scaleX: 1,
        ease: 'none',
        transformOrigin: 'left center',
        scrollTrigger: { start: 0, end: 'max', scrub: 0.3 },
      },
    );
  });

  return (
    <span
      ref={ref}
      aria-hidden
      className="fixed inset-x-0 top-0 z-50 h-0.5 origin-left scale-x-0 bg-brand"
    />
  );
}

// ---------------------------------------------------------------------------------------------
// Sections

function Nav(): JSX.Element {
  const token = useAuthStore((s) => s.token);
  const ref = useRef<HTMLElement | null>(null);

  // Past the hero the bar condenses and gains a seam — the chrome acknowledging you have left the
  // top of the page. A data attribute (not React state) so crossing the threshold costs no render.
  useGSAP(() => {
    const el = ref.current;
    if (!motionOK() || !el) return;
    const trigger = ScrollTrigger.create({
      start: 'top -72',
      end: 99999,
      onToggle: (self) => {
        el.dataset.scrolled = String(self.isActive);
      },
    });
    return () => trigger.kill();
  });

  return (
    <header
      ref={ref}
      className="group fixed inset-x-0 top-0 z-40 border-b border-transparent bg-surface-canvas/95 backdrop-blur-sm transition-colors duration-300 data-[scrolled=true]:border-edge"
    >
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4 transition-[height] duration-300 group-data-[scrolled=true]:h-14 sm:px-6">
        <Link to="/" className="flex items-center gap-2.5">
          <BrandMark size={26} />
          <span className="font-semibold tracking-tight text-fg">ForgeFlow</span>
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

/** The six-stage pipeline, played as a loop: each stage activates, completes, and hands off.
 *  The card also tilts a few degrees toward the cursor — enough depth to feel physical. */
function PipelineDemo(): JSX.Element {
  const cardRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const el = cardRef.current;
    if (!el || !motionOK() || !window.matchMedia('(pointer: fine)').matches) return;
    gsap.set(el, { transformPerspective: 900 });
    const rx = gsap.quickTo(el, 'rotationX', { duration: 0.5, ease: 'power2.out' });
    const ry = gsap.quickTo(el, 'rotationY', { duration: 0.5, ease: 'power2.out' });
    const move = (event: PointerEvent): void => {
      const rect = el.getBoundingClientRect();
      const px = (event.clientX - rect.left) / rect.width - 0.5;
      const py = (event.clientY - rect.top) / rect.height - 0.5;
      ry(px * 7);
      rx(py * -5);
    };
    const leave = (): void => {
      rx(0);
      ry(0);
    };
    el.addEventListener('pointermove', move);
    el.addEventListener('pointerleave', leave);
    return () => {
      el.removeEventListener('pointermove', move);
      el.removeEventListener('pointerleave', leave);
      gsap.killTweensOf(el);
    };
  }, []);

  return (
    <div
      ref={cardRef}
      data-hero-card
      className="relative mx-auto mt-14 w-full max-w-3xl overflow-hidden rounded-2xl border border-edge bg-surface shadow-2xl will-change-transform"
    >
      {/* Window chrome without the three decorative traffic-light dots: the path is the only
          thing in a title bar that carries information, so it is the only thing here. */}
      <div className="flex items-center gap-3 border-b border-edge px-4 py-2.5">
        <span className="font-mono text-[11px] text-fg-subtle">forgeflow.local</span>
        <span aria-hidden className="h-3 w-px bg-edge" />
        <span className="truncate font-mono text-[11px] text-fg-muted">/projects/demo</span>
      </div>

      <div className="grid gap-2 p-4 sm:grid-cols-2 lg:grid-cols-3">
        {STAGE_ORDER.map((stage, i) => {
          const Icon = STAGE_ICONS[stage];
          // Static first frame (also the reduced-motion frame): mid-run, so the card looks alive.
          const state = i < 3 ? 'done' : i === 3 ? 'active' : 'idle';
          return (
            <div
              key={stage}
              data-demo-stage
              data-state={state}
              className="group flex items-center gap-3 rounded-xl border border-edge bg-surface-raised/50 px-3 py-2.5 transition-colors duration-300 data-[state=active]:border-brand/60 data-[state=active]:bg-brand/10 data-[state=done]:border-success/30"
            >
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-edge bg-surface text-fg-muted transition-colors duration-300 group-data-[state=active]:border-brand/50 group-data-[state=active]:text-brand-text group-data-[state=done]:text-success">
                <Icon aria-hidden className="h-4 w-4" strokeWidth={1.75} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium text-fg">
                  {STAGE_LABELS[stage]}
                </span>
                <span className="block text-[11px] text-fg-subtle transition-colors duration-300 group-data-[state=active]:text-brand-text group-data-[state=done]:text-success">
                  <span className="hidden group-data-[state=idle]:inline">queued</span>
                  <span className="hidden group-data-[state=active]:inline">running…</span>
                  <span className="hidden group-data-[state=done]:inline">complete</span>
                </span>
              </span>
              <span
                aria-hidden
                className="h-1.5 w-1.5 shrink-0 rounded-full bg-fg-faint/50 transition-colors duration-300 group-data-[state=active]:animate-pulse group-data-[state=active]:bg-brand group-data-[state=done]:bg-success"
              />
            </div>
          );
        })}
      </div>

      <div className="flex items-center gap-1.5 border-t border-edge bg-surface-sunken/60 px-4 py-2.5 font-mono text-xs text-fg-subtle">
        <Check aria-hidden className="h-3.5 w-3.5 shrink-0 text-success" strokeWidth={2.5} />
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
const STATS: Array<{ value: number; suffix?: string; label: string }> = [
  { value: 6, label: 'stages, none mandatory' },
  { value: 2, label: 'model roles, cost-routed' },
  { value: 1, label: 'sandbox per project' },
  { value: 0, label: 'secrets in your browser' },
];

function Hero(): JSX.Element {
  const token = useAuthStore((s) => s.token);
  const scope = useRef<HTMLDivElement | null>(null);
  const line1 = useRef<HTMLSpanElement | null>(null);
  const line2 = useRef<HTMLSpanElement | null>(null);

  useGSAP(
    () => {
      if (!motionOK() || !line1.current || !line2.current) return;

      const s1 = new SplitText(line1.current, { type: 'words' });
      const s2 = new SplitText(line2.current, { type: 'words' });

      const tl = gsap.timeline({ defaults: { ease: EASE_OUT } });
      tl.from([...s1.words, ...s2.words], {
        yPercent: 70,
        opacity: 0,
        duration: 0.7,
        stagger: 0.045,
      })
        .from('[data-hero-badge]', { y: -10, opacity: 0, duration: 0.4 }, '-=0.55')
        .from('[data-hero-sub]', { y: 14, opacity: 0, duration: 0.55 }, '-=0.35')
        .from('[data-hero-cta] > *', { y: 12, opacity: 0, duration: 0.45, stagger: 0.07 }, '-=0.35')
        .from('[data-hero-card]', { y: 36, opacity: 0, duration: 0.8 }, '-=0.25');

      // Pipeline loop: reset, then each stage runs and completes in turn.
      const chips = gsap.utils.toArray<HTMLElement>('[data-demo-stage]');
      const STEP = 0.85;
      const loop = gsap.timeline({ repeat: -1, repeatDelay: 1.6, delay: 1.4 });
      loop.call(() => {
        for (const c of chips) c.dataset.state = 'idle';
      });
      chips.forEach((chip, i) => {
        loop.call(() => void (chip.dataset.state = 'active'), undefined, 0.01 + i * STEP);
        loop.call(() => void (chip.dataset.state = 'done'), undefined, 0.01 + i * STEP + 0.7);
      });
      loop.set({}, {}, 0.01 + chips.length * STEP + 0.4); // hold the all-green frame

      // The demo recedes as you scroll past it, so the section below arrives over the top of it
      // rather than merely after it.
      gsap.to('[data-hero-card-wrap]', {
        yPercent: -8,
        scale: 0.96,
        opacity: 0.55,
        ease: 'none',
        scrollTrigger: {
          trigger: '[data-hero-card-wrap]',
          start: 'center center',
          end: 'bottom top',
          scrub: 0.5,
        },
      });

      // Counters tick up when the fact strip scrolls into view.
      gsap.utils.toArray<HTMLElement>('[data-counter]').forEach((el) => {
        const target = Number(el.dataset.to ?? '0');
        const obj = { n: 0 };
        gsap.to(obj, {
          n: target,
          duration: 1.1,
          ease: 'power2.out',
          scrollTrigger: { trigger: el, start: 'top 88%', once: true },
          onUpdate: () => {
            el.textContent = String(Math.round(obj.n));
          },
        });
      });
    },
    { scope },
  );

  return (
    <section ref={scope} className="relative overflow-hidden px-4 pb-12 pt-32 sm:px-6 sm:pt-36">
      {/* The backdrop is the shared ambient layer: a hairline grid plus one pool of brand light
          that follows the pointer. Static gradient orbs behind a hero were the loudest generated-UI
          tell on this page; a glow that answers your cursor is an interaction, not wallpaper. */}
      <AmbientBackground intensity="bold" />

      <div className="relative mx-auto max-w-6xl text-center">
        {/* A tracked eyebrow between two rules, not a pill badge with a sparkle in it. */}
        <p
          data-hero-badge
          className="mb-6 flex items-center justify-center gap-3 text-[11px] font-medium uppercase tracking-[0.16em] text-fg-subtle"
        >
          <span aria-hidden className="h-px w-6 bg-brand" />
          AI-native · human-in-the-loop
          <span aria-hidden className="h-px w-6 bg-brand" />
        </p>

        {/* Two-tone headline: the second line recedes to `fg-muted`. Hierarchy carried by weight
            and value rather than by a clip-to-text gradient. */}
        <h1 className="mx-auto max-w-3xl text-4xl font-semibold leading-[1.08] tracking-tight text-fg sm:text-5xl lg:text-6xl">
          <span ref={line1} className="block">
            From idea to deployed app,
          </span>
          <span ref={line2} className="block text-fg-muted">
            in one guided flow.
          </span>
        </h1>

        <p data-hero-sub className="mx-auto mt-6 max-w-2xl text-base text-fg-muted sm:text-lg">
          ForgeFlow takes a web-app idea — screenshots or a spec — through design, requirements,
          build, test, self-healing repair, deploy, and live validation. You stay in the loop at
          every stage, free to refine, skip, or jump back.
        </p>

        <div data-hero-cta className="mt-8 flex flex-wrap items-center justify-center gap-3">
          <Magnetic>
            <Link to={token ? '/dashboard' : '/register'}>
              <Button className="h-10 px-5">
                Start building
                <ArrowRight aria-hidden className="h-4 w-4" />
              </Button>
            </Link>
          </Magnetic>
          <Magnetic>
            <Button
              variant="secondary"
              className="h-10 px-5"
              onClick={() => scrollToSection('workflow')}
            >
              See how it works
            </Button>
          </Magnetic>
        </div>

        {/* Parallax lives on the wrapper, never on the card itself — the card's own transform is
            owned by its cursor tilt, and two tweens fighting over one matrix is a jitter bug. */}
        <div data-hero-card-wrap>
          <PipelineDemo />
        </div>

        {/* One inline fact strip rather than four identical metric widgets — the same four
            numbers, without pretending a marketing page is an analytics dashboard. */}
        <dl className="mx-auto mt-12 flex max-w-4xl flex-wrap items-baseline justify-center gap-x-8 gap-y-3 border-t border-edge pt-6">
          {STATS.map((stat) => (
            <div key={stat.label} className="flex items-baseline gap-2">
              <dt className="sr-only">{stat.label}</dt>
              <dd className="flex items-baseline gap-2">
                <span
                  data-counter
                  data-to={stat.value}
                  className="text-lg font-semibold tabular-nums text-fg"
                >
                  {stat.value}
                </span>
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
    body: 'Frontend to Vercel, backend to Render, wired up from an analyzed deployment plan.',
  },
  {
    icon: Radar,
    title: 'Live validation',
    body: 'End-to-end Playwright checks run against the real deployed URL — proof, not a simulation.',
  },
];

function Features(): JSX.Element {
  const scope = useRef<HTMLElement | null>(null);

  useGSAP(
    () => {
      if (!motionOK()) return;
      gsap.from('[data-feature]', {
        opacity: 0,
        y: 28,
        duration: 0.65,
        ease: EASE_OUT,
        stagger: 0.08,
        scrollTrigger: { trigger: '[data-features-grid]', start: 'top 80%', once: true },
      });

      /*
       * The repair loop, scrubbed. Rather than replaying a canned stagger, the failure count is
       * tied to the scrollbar: scrolling through the panel *runs the loop*, and scrolling back up
       * un-runs it. The big number counts 4 → 0, the trail lights one step at a time behind it,
       * and the panel flips to a success state on the frame it reaches zero.
       */
      const trail = gsap.utils.toArray<HTMLElement>('[data-trail-step]');
      const readout = document.querySelector<HTMLElement>('[data-repair-readout]');
      const panel = document.querySelector<HTMLElement>('[data-repair-panel]');
      const counter = { failing: 4 };

      /** Paint one frame of the loop. Steps light as the count passes them: 4 → none, 0 → all. */
      const paint = (remaining: number): void => {
        if (readout) readout.textContent = String(remaining);
        if (panel) panel.dataset.converged = String(remaining === 0);
        trail.forEach((step, i) => {
          step.dataset.lit = String(4 - remaining > i);
        });
      };

      // The markup ships converged (the correct no-motion resting state), so rewind to the failing
      // start now that the convergence can actually be played back.
      paint(4);

      gsap.to(counter, {
        failing: 0,
        ease: 'none',
        scrollTrigger: {
          trigger: '[data-repair-panel]',
          start: 'top 78%',
          end: 'bottom 62%',
          scrub: 0.8,
        },
        onUpdate: () => paint(Math.round(counter.failing)),
      });
    },
    { scope },
  );

  return (
    <section
      ref={scope}
      id="features"
      className="relative scroll-mt-20 overflow-hidden px-4 py-20 sm:px-6"
    >
      <div className="relative mx-auto max-w-6xl">
        {/* Left-aligned, not centered: a centered heading over a symmetric grid is the shape every
            generated landing page takes. The section leads from the edge, like a page of prose. */}
        <div className="max-w-2xl">
          <MaskedHeading className="text-2xl font-semibold tracking-tight text-fg sm:text-3xl">
            Everything between the idea and the URL
          </MaskedHeading>
          <p className="mt-3 text-fg-muted">
            One workspace owns the whole journey — with a human hand on every tiller.
          </p>
        </div>

        {/*
          Deliberately asymmetric. The repair loop is the one thing here no other tool does, so it
          gets the large panel and the only concrete artifact on the page; two supporting features
          stack beside it; the remaining three sit in a hairline-divided band with no card chrome
          at all. Three densities, one focal point — the opposite of six equal boxes.
        */}
        <div data-features-grid className="mt-12 grid gap-4 lg:grid-cols-12">
          <SpotlightCard
            data-feature
            data-repair-panel
            // Rendered CONVERGED. With motion off the scrub never runs, and a panel resting
            // forever on "4 failing" would tell the opposite of the story; the loop's outcome is
            // that it finishes. GSAP rewinds it to the failing start state only when it can
            // actually play the convergence back.
            data-converged="true"
            className="group rounded-2xl border border-edge bg-surface p-7 transition-colors duration-500 hover:border-edge-strong data-[converged=true]:border-success/40 lg:col-span-7"
          >
            <span className="mb-5 flex h-11 w-11 items-center justify-center rounded-xl bg-brand/10 text-brand-text ring-1 ring-inset ring-brand/20">
              <LEAD_FEATURE.icon aria-hidden className="h-5 w-5" strokeWidth={1.75} />
            </span>
            <h3 className="text-lg font-semibold text-fg">{LEAD_FEATURE.title}</h3>
            <p className="mt-2 max-w-md leading-relaxed text-fg-muted">{LEAD_FEATURE.body}</p>

            {/*
              The loop, wired to the scrollbar. Scrolling through this panel runs it: the readout
              counts 4 → 0, the trail lights step by step, and the panel turns success-green on the
              frame it converges. Labelled "example run" because the counts are illustrative — every
              other number on this page is a claim that holds for any deployment.
            */}
            <div className="mt-6 border-t border-edge pt-4">
              <div className="flex items-baseline justify-between gap-4">
                <p className="text-[11px] uppercase tracking-[0.12em] text-fg-faint">Example run</p>
                <p className="flex items-baseline gap-1.5 font-mono text-xs text-fg-subtle">
                  <span
                    data-repair-readout
                    className="text-2xl font-semibold tabular-nums text-warning transition-colors duration-300 group-data-[converged=true]:text-success"
                  >
                    0
                  </span>
                  failing
                </p>
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-2 font-mono text-xs">
                {['run tests', 'patch reducer', 'patch guard', 'patch fixture'].map((label, i) => (
                  <span key={label} className="flex items-center gap-2">
                    <span
                      data-trail-step
                      data-lit="true"
                      className="rounded border border-edge px-1.5 py-0.5 text-fg-faint transition-colors duration-300 data-[lit=true]:border-brand/40 data-[lit=true]:bg-brand/10 data-[lit=true]:text-brand-text"
                    >
                      {label}
                    </span>
                    {i < 3 ? (
                      <span aria-hidden className="text-fg-faint">
                        →
                      </span>
                    ) : null}
                  </span>
                ))}
                <span className="text-fg-subtle transition-colors duration-300 group-data-[converged=true]:text-success">
                  · loop exits
                </span>
              </div>
            </div>
          </SpotlightCard>

          <div className="grid gap-4 lg:col-span-5">
            {SIDE_FEATURES.map((feature) => (
              <SpotlightCard
                key={feature.title}
                data-feature
                className="group rounded-2xl border border-edge bg-surface p-6 transition-colors duration-200 hover:border-edge-strong"
              >
                <span className="mb-4 flex h-10 w-10 items-center justify-center rounded-xl bg-brand/10 text-brand-text ring-1 ring-inset ring-brand/20">
                  <feature.icon aria-hidden className="h-[18px] w-[18px]" strokeWidth={1.75} />
                </span>
                <h3 className="text-sm font-semibold text-fg">{feature.title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-fg-muted">{feature.body}</p>
              </SpotlightCard>
            ))}
          </div>

          <div className="grid border-t border-edge sm:grid-cols-3 lg:col-span-12">
            {ROW_FEATURES.map((feature) => (
              <div
                key={feature.title}
                data-feature
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
              </div>
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
        ['Users can add a todo', '92%'],
        ['Todos persist across reloads', '88%'],
        ['Completed items can be filtered', '81%'],
      ].map(([label, confidence]) => (
        <li
          key={label}
          className="flex items-center gap-2 rounded-lg border border-edge bg-surface-sunken px-2.5 py-2"
        >
          <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded border border-brand/50 bg-brand/10">
            <span className="h-1.5 w-1.5 rounded-[2px] bg-brand" />
          </span>
          <span className="min-w-0 flex-1 truncate text-[11px] text-fg-muted">{label}</span>
          <span className="font-mono text-[10px] text-fg-faint">{confidence}</span>
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
          <span className="text-[10px] uppercase tracking-wide text-fg-faint">{state}</span>
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
            <span className="ml-auto text-[10px] uppercase tracking-wide text-fg-faint">
              {provider}
            </span>
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
  design:
    'Upload screenshots or describe the app; refine the look in conversation before any code exists.',
  requirements: 'The design becomes concrete, reviewable requirements you can edit and approve.',
  build:
    'The agent scaffolds and implements the app inside its sandbox — every file lands live in the workspace IDE.',
  test: 'Unit and end-to-end suites run in the sandbox; failures trigger the bounded repair loop automatically.',
  deploy: 'One action ships the frontend and backend to real hosting, wired together.',
  validate: 'Post-deploy checks prove the live URL actually works — the loop closes on evidence.',
};

/**
 * The pipeline, pinned and scrubbed horizontally.
 *
 * This deliberately is NOT the GSAP seamless-infinite-loop technique. That was tried here and
 * removed: its `buildSeamlessLoop` helper is only numerically valid above a card threshold, its
 * canonical scroll handling wraps the page (a trap mid-document), and — decisively — it could not
 * be observed running in this environment, so every fix was a guess. This version does one thing
 * that can be reasoned about completely: the track translates left by exactly the distance needed
 * to bring its last panel into view, driven by the pin's scroll progress.
 *
 * Progressive enhancement throughout: with motion off there is no pin and the track is an ordinary
 * horizontally-scrollable row, so all six stages stay readable and reachable.
 */
function Workflow(): JSX.Element {
  const scope = useRef<HTMLElement | null>(null);
  const trackRef = useRef<HTMLDivElement | null>(null);

  useGSAP(
    () => {
      const track = trackRef.current;
      if (!motionOK() || !track) return;

      /*
       * Travel distance is measured against the VIEWPORT (the clipping parent), not the track: the
       * track is `w-max`, so its own scrollWidth and offsetWidth are identical and the difference
       * is always zero — which silently disables the pin entirely.
       */
      const viewport = track.parentElement;
      const distance = (): number => Math.max(0, track.scrollWidth - (viewport?.clientWidth ?? 0));
      if (distance() <= 0) return;

      return safeAnimate('landing pipeline pin', () => {
        /*
         * The viewport is deliberately left scrollable even though GSAP is driving the track.
         *
         * Hiding the overflow here reads as tidier, but it makes correct rendering conditional on
         * the pin working: if the scrub fails for any reason the track sits at x=0 with stages
         * 04–06 clipped off and no way to reach them. Leaving native scrolling available means the
         * worst case is a plain scrollable row — which is exactly the no-motion fallback — instead
         * of hidden content. The redundancy is worth it.
         */
        /*
         * Locks at full visibility, plays through, then releases.
         *
         * `start: 'top top'` fires the pin the instant the section's top meets the viewport top —
         * and because the section is exactly `h-screen`, that is the same moment it becomes fully
         * visible. `end: '+=' + distance()` spends exactly enough scroll to bring the last panel
         * in, so the pin releases the moment the run finishes and the page continues to the CTA
         * and footer. Nothing is held longer than the animation actually needs.
         *
         * The dead space this used to leave was never the pin's fault — it was a viewport-height
         * section holding content that did not fill it. The panels now flex to fill instead.
         */
        const window_ = () => ({
          trigger: scope.current,
          start: 'top top',
          end: () => '+=' + distance(),
          scrub: 0.6,
          invalidateOnRefresh: true,
        });

        const drift = gsap.to(track, {
          x: () => -distance(),
          ease: 'none',
          scrollTrigger: { ...window_(), pin: true, anticipatePin: 1 },
        });

        gsap.fromTo(
          '[data-track-rail]',
          { scaleX: 0 },
          { scaleX: 1, ease: 'none', transformOrigin: 'left center', scrollTrigger: window_() },
        );

        // Backdrop parallax at ~40% of the track's travel: the gap between the slow grid and the
        // faster panels is what sells moving *through* the pipeline rather than past it.
        gsap.to('[data-workflow-bg]', {
          x: () => -distance() * 0.4,
          ease: 'none',
          scrollTrigger: window_(),
        });

        // `containerAnimation` is the only way to trigger on an element being moved by another
        // tween; a normal ScrollTrigger would just see the pinned section standing still.
        gsap.utils.toArray<HTMLElement>('[data-stage-panel]').forEach((panel) => {
          ScrollTrigger.create({
            trigger: panel,
            containerAnimation: drift,
            start: 'left 62%',
            end: 'right 38%',
            onToggle: (self) => {
              panel.dataset.active = String(self.isActive);
            },
          });
        });

        const teardowns = gsap.utils
          .toArray<HTMLElement>('[data-stage-panel]')
          .map((panel) => reversibleHover(panel, { y: -8 }, { ease: 'back.out(2.2)' }));

        return () => teardowns.forEach((fn) => fn());
      });
    },
    { scope },
  );

  return (
    <section ref={scope} id="workflow" className="relative h-screen scroll-mt-20 overflow-hidden">
      {/* Scroll-driven backdrop: a grid drifting behind the panels, plus a pool of brand light. */}
      <div
        aria-hidden
        data-workflow-bg
        className="pointer-events-none absolute inset-y-0 -left-[15%] w-[130%]"
        style={{
          backgroundImage:
            'linear-gradient(rgb(var(--ff-edge) / 0.55) 1px, transparent 1px), linear-gradient(90deg, rgb(var(--ff-edge) / 0.55) 1px, transparent 1px)',
          backgroundSize: '56px 56px',
          maskImage: 'linear-gradient(to bottom, transparent, black 22%, black 78%, transparent)',
          WebkitMaskImage:
            'linear-gradient(to bottom, transparent, black 22%, black 78%, transparent)',
        }}
      />
      <div
        aria-hidden
        className="pointer-events-none absolute left-1/2 top-1/2 h-[40rem] w-[40rem] -translate-x-1/2 -translate-y-1/2 rounded-full"
        style={{
          background: 'radial-gradient(circle, rgb(var(--ff-brand) / 0.08), transparent 65%)',
        }}
      />

      {/* A column that fills the pinned viewport exactly: fixed-height header, then a track that
          takes every remaining pixel. `pt-28` clears the fixed nav while pinned. */}
      <div className="relative mx-auto flex h-full max-w-6xl flex-col px-4 pb-12 pt-28 sm:px-6">
        <div className="max-w-2xl">
          <MaskedHeading className="text-2xl font-semibold tracking-tight text-fg sm:text-3xl">
            Six stages. Your order.
          </MaskedHeading>
          <p className="mt-3 text-fg-muted">
            The pipeline reads left to right, but nothing forces you through it that way. Keep
            scrolling — this is the run, and it lets you out the other side.
          </p>
        </div>

        <div className="relative mt-8 h-px w-full bg-edge">
          <span
            aria-hidden
            data-track-rail
            className="absolute inset-y-0 left-0 w-full origin-left bg-brand"
          />
        </div>

        {/* The track takes the height the header leaves it, but not more than it can use: past
            ~34rem a panel is just a taller box around the same content, which is what made this
            section read as claiming space it had no need for. Capped and centred, the leftover
            becomes margin instead of emptiness. The GSAP pin measures `track.parentElement`, so
            the viewport stays the track's direct parent — the centring wrapper goes outside it. */}
        <div className="mt-8 flex min-h-0 flex-1 items-center">
          <div data-track-viewport className="h-full max-h-[34rem] w-full overflow-x-auto pb-3">
            <div ref={trackRef} className="flex h-full w-max gap-5 will-change-transform">
              {STAGE_ORDER.map((stage, i) => {
                const Icon = STAGE_ICONS[stage];
                return (
                  <article
                    key={stage}
                    data-stage-panel
                    className="group/panel relative flex h-full w-[clamp(17rem,25vw,23rem)] shrink-0 flex-col justify-between overflow-hidden rounded-2xl border border-edge bg-surface p-7 transition-colors duration-500 will-change-transform data-[active=true]:border-brand/40 data-[active=true]:bg-surface-raised"
                  >
                    {/* The stage number, big enough to read across a moving panel. */}
                    <span
                      aria-hidden
                      className="pointer-events-none absolute -right-3 -top-6 select-none font-mono text-[7rem] font-semibold leading-none text-fg/[0.05]"
                    >
                      {String(i + 1).padStart(2, '0')}
                    </span>

                    <div className="relative flex items-center justify-between">
                      <span className="flex h-12 w-12 items-center justify-center rounded-xl border border-edge bg-surface-sunken text-fg-muted transition-colors duration-500 group-data-[active=true]/panel:border-brand/50 group-data-[active=true]/panel:bg-brand/10 group-data-[active=true]/panel:text-brand-text">
                        <Icon aria-hidden className="h-5 w-5" strokeWidth={1.75} />
                      </span>
                      <span className="font-mono text-sm font-medium text-fg-muted">
                        {String(i + 1).padStart(2, '0')}
                        <span className="text-fg-faint"> / 06</span>
                      </span>
                    </div>

                    {/* What the stage hands you, not a picture of the stage. Hidden from assistive
                      tech: it restates the copy below in shapes, and reading it aloud would be
                      noise. */}
                    <div
                      aria-hidden
                      className="relative my-6 flex min-h-0 flex-1 items-center overflow-hidden opacity-80 transition-opacity duration-500 group-data-[active=true]/panel:opacity-100"
                    >
                      <div className="w-full">{STAGE_PREVIEWS[stage]}</div>
                    </div>

                    <div className="relative">
                      <h3 className="text-xl font-semibold text-fg">{STAGE_LABELS[stage]}</h3>
                      <p className="mt-2 text-sm leading-relaxed text-fg-muted">
                        {WORKFLOW_COPY[stage]}
                      </p>
                    </div>
                  </article>
                );
              })}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

function CtaBanner(): JSX.Element {
  const token = useAuthStore((s) => s.token);
  const scope = useRef<HTMLElement | null>(null);

  useGSAP(
    () => {
      if (!motionOK()) return;
      gsap.from('[data-cta-panel]', {
        opacity: 0,
        y: 30,
        scale: 0.985,
        duration: 0.8,
        ease: EASE_OUT,
        scrollTrigger: { trigger: scope.current, start: 'top 82%', once: true },
      });
    },
    { scope },
  );

  return (
    <section ref={scope} className="px-4 pb-24 pt-6 sm:px-6">
      <div
        data-cta-panel
        className="relative mx-auto max-w-5xl overflow-hidden rounded-3xl border border-edge bg-surface px-6 py-16 text-center"
      >
        {/* One brand hairline across the top edge. No glow behind the panel — the closing ask
            should read as a considered slab, not a lightbox. */}
        <span aria-hidden className="absolute inset-x-0 top-0 h-px bg-brand/50" />
        <div className="relative">
          <MaskedHeading className="text-2xl font-semibold tracking-tight text-fg sm:text-3xl">
            Your next app is a conversation away.
          </MaskedHeading>
          <p className="mx-auto mt-3 max-w-xl text-fg-muted">
            Open a project, drop in a screenshot, and watch the pipeline pick it up.
          </p>
          <div className="mt-8 flex justify-center">
            <Link to={token ? '/dashboard' : '/register'}>
              <Button className="h-10 px-6">
                {/* Not "free account" — there is no paid tier to be free of. */}
                {token ? 'Open your dashboard' : 'Create an account'}
                <ArrowRight aria-hidden className="h-4 w-4" />
              </Button>
            </Link>
          </div>
        </div>
      </div>
    </section>
  );
}

function Footer(): JSX.Element {
  return (
    <footer className="border-t border-edge px-4 py-8 sm:px-6">
      <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 sm:flex-row">
        <div className="flex items-center gap-2.5">
          <BrandMark size={26} />
          <span className="text-sm font-medium text-fg">ForgeFlow</span>
          <span className="text-xs text-fg-faint">v{__APP_VERSION__}</span>
        </div>
        <p className="text-xs text-fg-subtle">
          Design → requirements → build → test → deploy → validate. In any order you like.
        </p>
      </div>
    </footer>
  );
}

export default function Landing(): JSX.Element {
  return (
    <main className="min-h-screen bg-surface-canvas">
      <ScrollProgress />
      <Nav />
      <Hero />
      <Features />
      <Workflow />
      <CtaBanner />
      <Footer />
    </main>
  );
}
