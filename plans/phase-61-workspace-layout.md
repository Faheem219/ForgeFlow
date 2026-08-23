# Phase 61 — Workspace layout: resizable panels & focus mode

## Title & ID

- **ID:** `phase-61`
- **Title:** Workspace layout — resizable panels, collapsible rails, focus overlay
- **Epic:** Epic 1 — Orchestration Core *(amends `phase-09`, `phase-14`, `phase-15`)*
- **Milestone:** M5 *(usability)*
- **Status:** ⬜ todo

## Goal

Make every panel usable at the same time. Today, opening the preview during a build leaves the file
tree, the editor and the preview each too narrow to read — the three things a user most needs while
watching a build are the three that collapse. Replace the fixed grid with a resizable, collapsible,
persisted layout, and give any pane a full-window focus mode.

## Context

The workspace is a hard-coded three-column flex (`frontend/src/features/workspace/Workspace.tsx:228-285`):

```
w-60 (stages + cost) | flex-1 (stage panel) | w-80 (chat)     + h-64 terminal
```

240 px and 320 px are **fixed and non-collapsible**, and the terminal takes a fixed 256 px whether
or not it is being used. Inside the remaining centre column, `BuildPanel` splits again
(`frontend/src/features/build/BuildPanel.tsx:553-554`):

```tsx
showPreview ? 'grid-rows-2 xl:grid-cols-2 xl:grid-rows-1' : 'grid-rows-1'
```

So with the preview open on a 1440 px screen the IDE and the preview get roughly 200 px each — the
reported symptom, visible in the attached screenshot: *"when the build phase is running and the
preview tab is open neither the preview tab nor the files tab are properly visible at all."* The
build progress list, the report accordion, the file tree, the editor, the preview and its logs are
all competing inside one column that is already the smallest it will ever be.

`react-resizable-panels` is **already an authorised dependency** (`CLAUDE.md` → *UI-revamp additions
(authorized)*) and is currently unused. `plans/ui-refinement-audit.md` exists from the earlier UI
pass and should be honoured rather than contradicted.

The chosen direction (user decision, 2026-08-23, *"choose the best option … do not consider
simplicity"*): **resizable panels + collapsible rails + a focus overlay** — everyday work gets a
layout the user shapes, and any single pane can take the whole window when it needs to.

## Depends on

- `phase-09` (workspace shell) · `phase-14` (Monaco IDE) · `phase-15` (preview) · `phase-13`
  (terminal) · the UI-revamp pass — all ✅.
- `react-resizable-panels` already in `package.json`; no new dependency.

## Detailed tasks

### 1. Layout primitives (`frontend/src/features/workspace/layout/`, new)

- Wrap `react-resizable-panels` in project-styled `SplitPane` / `PaneHandle` components using the
  existing semantic tokens (`bg-surface`, `border-edge`) so the seams read as part of the design,
  not as library chrome. Handles get a visible hover/drag affordance and a real hit area.
- `useLayoutPersistence(projectId)` — sizes and collapsed state stored per project in
  `workspaceStore` (Zustand, already present) and mirrored to `localStorage`, so a workspace reopens
  as the user left it. A `Reset layout` action restores defaults.

### 2. Rails become collapsible (`Workspace.tsx`)

- Left rail (Stages + Cost) and right rail (Chat) become resizable panels that collapse to a
  ~48 px icon strip; clicking an icon expands, and the stage icons remain clickable while collapsed
  so stage navigation never depends on the rail being open.
- The terminal becomes a **drawer**: collapsed to a title bar by default, dragged up to any height,
  double-click to snap. It stops consuming 256 px of a build's vertical space unconditionally.
- Keyboard: `[` / `]` toggle the rails, `` ` `` toggles the terminal — announced via the existing
  shortcut surface, and reflected in `aria-expanded`.

### 3. Centre column: real splits (`BuildPanel.tsx`, `features/ide/Ide.tsx`)

- Replace the `grid-rows-2 / xl:grid-cols-2` switch with a nested `SplitPane`: build stream above,
  IDE ⇄ preview side-by-side below, every seam draggable, each pane with an honest minimum size
  rather than a silent squeeze.
- The file tree gets its own draggable split against the editor (currently fixed inside `Ide`).
- Below a breakpoint the splits stack vertically instead of shrinking — small screens get scrolling,
  not four unusable panes.

### 4. Focus mode (`frontend/src/features/workspace/layout/FocusOverlay.tsx`, new)

- A maximize control on Build, Files/Editor, Preview and Terminal promotes that pane to a full-window
  overlay; `Esc` or the control restores it to exactly its previous size.
- Implemented as a portal with a focus trap, `role="dialog"`, restored focus on close, and the
  underlying layout left mounted so the preview iframe and the terminal socket are **not** torn down
  and re-created (a reload would lose xterm scrollback and restart the preview connection).
- Focus state is per project and not persisted across reloads — a maximized pane is a momentary
  intent, not a saved preference.

### 5. Fit and finish

- Density pass on `BuildPanel`'s header, progress list and report accordion so the stream reads at
  narrow widths; long file paths truncate from the left (the informative end).
- The preview keeps its status chips and Start/Refresh/Logs controls in focus mode.
- Honour `prefers-reduced-motion` on the collapse/expand and overlay transitions.

## Out of scope for this phase

- Any change to backend APIs, realtime events, or what the panels display.
- Re-theming, new colours, or typography changes — `plans/ui-refinement-audit.md` stands.
- Detachable/pop-out windows, multi-monitor, or user-defined panel arrangements beyond size and
  collapse.
- The Data browser, Admin, Eval and Settings pages.

## Acceptance criteria

1. With the preview open during a build, the file tree, editor and preview are all simultaneously
   usable at 1440 px, and each can be dragged larger.
2. Both rails and the terminal collapse and restore; stage navigation still works collapsed.
3. Sizes and collapsed state survive a reload and are per project; `Reset layout` restores defaults.
4. Any of the four panes maximizes to the full window and restores to its prior size on `Esc`.
5. Entering/leaving focus mode does **not** reload the preview iframe or drop terminal scrollback.
6. Keyboard shortcuts work, focus is trapped and restored in the overlay, and panels expose correct
   `aria-expanded`/`role`; the overlay is announced.
7. At tablet width panes stack rather than shrink; no horizontal page scroll at any width.
8. `pnpm lint`, `pnpm typecheck` and `pnpm test` clean; no `any`.

## Tests to write

- `features/workspace/layout/SplitPane.test.tsx` — drag resizes, minimum sizes hold, collapse/expand
  toggles emit the right state.
- `features/workspace/layout/persistence.test.ts` — sizes round-trip per project; a corrupt stored
  value falls back to defaults instead of throwing.
- `features/workspace/layout/FocusOverlay.test.tsx` — maximize/restore, `Esc`, focus trap and
  restoration, and that the underlying pane stays mounted.
- `features/workspace/Workspace.test.tsx` (extend) — rails collapse, stage nav works collapsed,
  terminal drawer toggles.
- `features/build/BuildPanel.test.tsx` (extend) — preview open renders both panes with non-zero
  size; the stacked layout appears below the breakpoint.

## Manual verification steps

1. Start a build with the preview open at 1440 px and at 1280 px; confirm all three panes readable.
2. Drag every seam; collapse both rails and the terminal; reload — layout restored.
3. Maximize the preview, interact with the app, `Esc` — no reload, state intact.
4. Maximize the terminal, confirm scrollback survives; repeat with keyboard only.
5. Tab through the workspace with the overlay open; confirm focus never escapes.

## Rollback / cleanup notes

- Self-contained in `features/workspace/layout/` plus the two panel components; reverting restores
  the fixed grid. Persisted layout keys are namespaced and inert if left behind.

## Design notes

- **The crowding is structural, not cosmetic.** Three fixed-width regions plus a fixed-height
  terminal leave the centre column as the only elastic area, and then it subdivides again. No amount
  of density work fixes that; the fixed dimensions have to become negotiable.
- **Why panels *and* an overlay.** Resizing serves the common case (watch the build, glance at the
  preview). Focus serves the moment a single pane is the whole task — reading a stack trace,
  clicking through the app. Either alone leaves one of those awkward, which is why the fuller option
  was chosen over the simpler ones.
- **Keeping panes mounted is the load-bearing detail.** Unmounting on maximize would be far simpler
  and would silently reload the preview and destroy terminal scrollback — the two things a user is
  most likely to be maximizing *in order to read*.
