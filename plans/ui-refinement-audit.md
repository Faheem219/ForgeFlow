# ForgeFlow UI/UX Audit & Refinement Plan

_Audited: every screen under `frontend/src` (auth, shell, dashboard, workspace + stage panels,
IDE, data browser, deploy topology, settings, admin, eval, kit). This document records what is
strong, what holds the product back, and the refinement program that follows from it. The
brand — indigo/violet on near-black/white, Inter + JetBrains Mono, the semantic token system —
is **kept**, not redesigned._

---

## 1. What is already strong (do not touch)

- **Token discipline.** `app/theme/palette.ts` is a true single source of truth; both themes are
  CI-tested for WCAG contrast (`contrast.test.ts`). Every surface composes semantic utilities
  (`bg-surface-raised`, `text-fg-muted`) — no hardcoded colors anywhere.
- **A11y bones.** Real focus trap + restore + scroll lock in `Modal`; `:focus-visible` ring
  globally; roles/aria on tabs, menus, toasts; `MotionConfig reducedMotion="user"` app-wide.
- **Primitive vocabulary.** Panel/Badge/Button/Input/EmptyState/Segmented/Stat/Skeleton/Kbd are
  consistent and typed; features rarely invent their own styling (auth was the exception).
- **Performance hygiene.** Monaco/xterm/React-Flow panels are lazy; the motion system is tiny.

## 2. Findings

### Critical (product-level)

- **F1 — No landing page.** `/` is auth-gated; logged-out visitors bounce straight to a login
  card. There is zero surface that communicates what ForgeFlow *is*. (`features/landing/` is an
  empty directory.)
- **F2 — The motion system exists but is unused.** `lib/motion.ts` defines a house easing,
  durations, `riseIn`, `staggerChildren`, `pageTransition` — and no screen imports them. Routes
  cut hard; lists pop in; loading states swap abruptly. The app *feels* static even though the
  vocabulary was already designed.

### High (visible on every session)

- **F3 — Auth screens were off-system.** Hand-rolled `Field` duplicating `fieldClasses`; raw
  submit `<button>` with a no-op hover (`hover:bg-brand` on `bg-brand`) and hardcoded
  `text-white`. _(Fixed in this pass: AuthShell rebuilt on primitives + motion.)_
- **F4 — Loading = lone centered spinner everywhere** (dashboard, workspace, eval, stage panels).
  The `Skeleton` primitive exists but has zero call sites → every load is a layout jump.
- **F5 — Glyph inconsistency.** lucide icons in the shell, but emoji `🔒` (StageNavigator),
  text glyphs `⋯ ▾ ▸ ·` (menus, chat activity, card metadata), and one hand-inlined SVG trash
  icon (Dashboard) elsewhere.
- **F6 — StageNavigator reads as a list, not a pipeline.** The stage flow is ForgeFlow's core
  mental model, yet the navigator has no stage icons, no connecting rail/progress affordance,
  and its `⋯` menu has no outside-click or Escape dismissal (a11y gap) and no motion.
- **F7 — Dashboard cards are inert.** No entrance choreography, faint hover feedback, no
  per-project visual identity, no summary strip (counts, stage distribution). First screen after
  login, weakest screen in the app.

### Medium

- **F8 — Workspace toolbar.** Six identical text buttons; the dividers use `bg-surface-raised`
  (invisible on the canvas); view switching (stage/data) is a toggle rather than a segmented
  control; no tooltips or keyboard hints.
- **F9 — Chat composer.** No Enter-to-send (Shift+Enter for newline), no auto-grow, plain
  "streaming" indicator, text-glyph disclosure for Activity.
- **F10 — Empty states are dry.** No icon treatment; identical dashed box everywhere.
- **F11 — Header user affordance.** A raw email string + logout button; no avatar/user chip.
- **F12 — NotFound is a plain empty-state box.**

### Low

- **F13** Sidebar collapse animates width only; no tooltip affordance when collapsed beyond
  `title`. **F14** Panels are flat — elevation language stops at 1px borders; cards could carry
  a restrained hover sheen. **F15** No first-run guidance beyond "No projects yet".

## 3. Animation architecture (decision)

**Hybrid, with a clear boundary:**

- **GSAP** (`gsap` + `@gsap/react`, plugins: ScrollTrigger, SplitText — free since GSAP 3.13) owns
  *choreography*: the landing page (hero timeline, split-text headline, scroll-triggered reveals,
  counters, floating ambient elements), route-enter transitions, dashboard card staggers, and any
  future timeline work. Central setup in `lib/gsap.ts` registers plugins once and exposes a
  `prefersReducedMotion()` guard; every timeline respects it.
- **framer-motion** stays where it is already load-bearing: React *presence* work (Modal/Toaster
  enter-exit via `AnimatePresence`, the Tabs `layoutId` underline, ThemeToggle icon swap).
  Replacing those with GSAP would add code to lose capability.
- One easing family (the existing `[0.16, 1, 0.3, 1]` house curve) shared by both.

## 4. Refinement program

1. **Foundation** — GSAP install, `lib/gsap.ts`, reduced-motion guard, jsdom test shims.
2. **Landing page** (`/`) — nav, hero (SplitText + timeline), live-feeling pipeline showcase
   built from the real six-stage model, feature grid (scroll reveals), how-it-works stepper,
   CTA + footer. App moves to `/dashboard`; all internal `navigate('/')` targets updated;
   router/guard tests updated to the new topology.
3. **Core surfaces** — Dashboard (skeleton grid, GSAP stagger, card redesign, summary strip),
   AppShell (animated nav indicator, route transitions, user chip, edge-token dividers),
   StageNavigator (icon stepper + rail, dismissable animated menu), ChatPanel (composer UX,
   icons), auth Register parity.
4. **Consistency sweep** — EmptyState icon well, NotFound, glyph → lucide everywhere, toolbar
   segmented control, Settings/Admin/Eval spacing + headers.
5. **Verification** — `pnpm lint`, `pnpm typecheck`, `pnpm test`; every pinned test string and
   `data-testid` preserved (or the test updated deliberately with the topology change, never
   silently).

_Not in scope: changing the palette, typography, information architecture of stage panels, or
any backend contract._
