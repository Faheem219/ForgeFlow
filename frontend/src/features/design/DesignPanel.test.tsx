import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useToastStore } from '../../lib/stores/toastStore';
import { useWorkspaceStore } from '../../lib/stores/workspaceStore';
import type { DesignQuestionDto, Stage, StageStatus } from '../../lib/types';
import { DesignPanel } from './DesignPanel';

const PROJECT = 'p1';

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

interface MockState {
  versions: Array<Record<string, unknown>>;
  artifact: Record<string, unknown>;
  intentStatus: number;
  intentCalls: Array<Record<string, unknown>>;
  /** Screens the provider reports for the whole project (the app, not one version). */
  screens: Array<{ ref: string; title: string; preview_image: string | null }>;
  screenBodies: Record<string, string>;
  stages: Array<{ stage: Stage; status: StageStatus; artifacts: string[] }>;
  /** What the provider is waiting on, or null when it isn't waiting on anything. */
  question: DesignQuestionDto | null;
}

function installFetch(state: MockState): void {
  const fetchMock = vi.fn<typeof fetch>((input, init) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    if (url.includes('/design/question')) {
      return Promise.resolve(json(state.question));
    }
    if (url.includes('/design/provider')) {
      return Promise.resolve(
        json({
          key: 'fake',
          health: 'ok',
          capabilities: {
            provider: 'fake',
            from_image: true,
            from_text: true,
            refine: true,
            fetch_code: true,
            max_images: 8,
          },
        }),
      );
    }
    if (url.includes('/design/screens')) {
      return Promise.resolve(json({ screens: state.screens }));
    }
    if (url.includes('/design/screen?')) {
      const ref = decodeURIComponent(url.split('ref=')[1] ?? '');
      return Promise.resolve(json({ ref, html: state.screenBodies[ref] ?? '', css: '' }));
    }
    if (url.includes('/design/images')) {
      return Promise.resolve(
        json({ images: [{ ref: 'r1', filename: 'a.png', media_type: 'image/png' }] }),
      );
    }
    if (url.includes('/stages')) {
      return Promise.resolve(json(state.stages));
    }
    if (url.includes('/artifacts?')) return Promise.resolve(json(state.versions));
    if (url.includes('/artifacts/')) return Promise.resolve(json(state.artifact));
    if (url.includes('/intent')) {
      state.intentCalls.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
      if (state.intentStatus >= 400) {
        return Promise.resolve(
          json(
            {
              error: {
                type: 'provider_error',
                message: 'quota exhausted',
                fallback_hint: 'switch to figma',
              },
            },
            state.intentStatus,
          ),
        );
      }
      return Promise.resolve(
        json({
          stage: 'design',
          action: method,
          from_status: 'empty',
          to_status: 'awaiting_user',
          stale: [],
          messages: [],
          artifacts: [],
          run_id: 'r',
        }),
      );
    }
    return Promise.resolve(json({}));
  });
  vi.stubGlobal('fetch', fetchMock);
}

function version(id: string, v: number, source = 'text'): Record<string, unknown> {
  return {
    id,
    project_id: PROJECT,
    stage: 'design',
    type: 'design',
    version: v,
    ref: null,
    meta: { source },
    created_at: '',
  };
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(<DesignPanel projectId={PROJECT} />, { wrapper });
}

let state: MockState;

beforeEach(() => {
  state = {
    versions: [],
    artifact: {},
    intentStatus: 200,
    intentCalls: [],
    screens: [],
    screenBodies: {},
    question: null,
    // Requirements complete by default, so only the nudge tests below exercise the nudge.
    stages: [
      { stage: 'design', status: 'awaiting_user', artifacts: [] },
      { stage: 'requirements', status: 'complete', artifacts: [] },
    ],
  };
  useToastStore.setState({ toasts: [] });
  useWorkspaceStore.getState().reset();
});

afterEach(() => vi.unstubAllGlobals());

describe('DesignPanel', () => {
  it('shows the active provider health and generates from a text prompt', async () => {
    installFetch(state);
    renderPanel();

    expect(await screen.findByTestId('provider-health')).toHaveTextContent('fake: ok');

    fireEvent.change(screen.getByTestId('design-prompt'), { target: { value: 'a todo app' } });
    fireEvent.click(screen.getByRole('button', { name: 'Generate from text' }));

    await waitFor(() => {
      const call = state.intentCalls.at(-1);
      expect(call).toMatchObject({
        stage: 'design',
        action: 'refine',
        payload: { text: 'a todo app' },
      });
    });
  });

  it('generates with an empty box when no design exists yet — never a dead end', async () => {
    // state.versions starts empty (beforeEach) → hasDesign is false → the button must be usable.
    installFetch(state);
    renderPanel();
    await screen.findByTestId('provider-health');

    const button = screen.getByRole('button', { name: 'Generate from text' });
    expect(button).toBeEnabled(); // not gated on typing anything, unlike before this fix

    fireEvent.click(button);

    await waitFor(() => {
      const call = state.intentCalls.at(-1);
      expect(call).toMatchObject({ stage: 'design', action: 'refine' });
      // No `payload.text` at all — the backend's own fallback (original prompt / requirements /
      // a generic starting design) decides what to generate from, not an empty string here.
      expect((call as { payload?: { text?: string } }).payload?.text).toBeUndefined();
    });
  });

  it('still requires typed text to regenerate once a design already exists', async () => {
    state.versions = [version('a1', 1)];
    state.artifact = { ...version('a1', 1), content: JSON.stringify({ html: '<h1/>', css: '' }) };
    installFetch(state);
    renderPanel();

    await screen.findByTestId('design-version-1');
    // Empty here is ambiguous (refine? approve? nothing?) — the "Refine" field further up is the
    // correct place for a no-text action once a design exists, not this one.
    expect(screen.getByRole('button', { name: 'Generate from text' })).toBeDisabled();
  });

  it('renders the selected version in a sandboxed preview', async () => {
    state.versions = [version('a1', 1)];
    state.artifact = {
      ...version('a1', 1),
      content: JSON.stringify({ html: '<h1>hi</h1>', css: 'h1{}' }),
    };
    installFetch(state);
    renderPanel();

    const frame = await screen.findByTestId('design-preview-frame');
    await waitFor(() => expect(frame.getAttribute('srcdoc')).toContain('<h1>hi</h1>'));
    // Untrusted markup stays sandboxed. Scripts may run (real exports style themselves at
    // runtime), but `allow-same-origin` must never be granted alongside — that pairing would
    // dissolve the sandbox and hand the markup our DOM, cookies and API.
    expect(frame.getAttribute('sandbox')).toBe('allow-scripts');
  });

  it('lists every screen in the project and previews the one picked', async () => {
    // The gap this closes: an app's screens (landing, login, dashboard…) are spread across design
    // versions, so the version list alone can't show you the whole app.
    state.versions = [version('a1', 1)];
    state.artifact = {
      ...version('a1', 1),
      meta: { source: 'text', external_ref: 'proj/login' },
      content: JSON.stringify({ html: '<h1>login</h1>', css: '' }),
    };
    state.screens = [
      { ref: 'proj/landing', title: 'Landing', preview_image: null },
      { ref: 'proj/login', title: 'Login', preview_image: null },
    ];
    state.screenBodies = { 'proj/landing': '<h1>landing</h1>' };
    installFetch(state);
    renderPanel();

    // Opens on the screen this version produced, taken from the artifact's provider ref.
    const select = (await screen.findByTestId('screen-select')) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe('proj/login'));
    expect(screen.getByTestId('design-preview-frame').getAttribute('srcdoc')).toContain('login');

    fireEvent.change(select, { target: { value: 'proj/landing' } });

    await waitFor(() =>
      expect(screen.getByTestId('design-preview-frame').getAttribute('srcdoc')).toContain(
        'landing',
      ),
    );
  });

  it('refines the screen currently on show, not the latest one generated', async () => {
    state.versions = [version('a1', 1)];
    state.artifact = {
      ...version('a1', 1),
      meta: { source: 'text', external_ref: 'proj/dark' },
      content: JSON.stringify({ html: '<h1>dark</h1>', css: '' }),
    };
    state.screens = [
      { ref: 'proj/light', title: 'Light', preview_image: null },
      { ref: 'proj/dark', title: 'Dark', preview_image: null },
    ];
    state.screenBodies = { 'proj/light': '<h1>light</h1>' };
    installFetch(state);
    renderPanel();

    fireEvent.change(await screen.findByTestId('screen-select'), {
      target: { value: 'proj/light' },
    });
    fireEvent.change(screen.getByTestId('design-refine-input'), {
      target: { value: 'raise the contrast' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Refine' }));

    await waitFor(() => {
      const call = state.intentCalls.at(-1);
      expect(call).toMatchObject({
        stage: 'design',
        action: 'refine',
        message: 'raise the contrast',
        payload: { design_ref: 'proj/light' },
      });
    });
  });

  it('hides the picker when the provider reports a single screen', async () => {
    state.versions = [version('a1', 1)];
    state.artifact = { ...version('a1', 1), content: JSON.stringify({ html: '<h1>x</h1>' }) };
    state.screens = [{ ref: 'proj/only', title: 'Only', preview_image: null }];
    installFetch(state);
    renderPanel();

    await screen.findByTestId('design-preview-frame');
    expect(screen.queryByTestId('screen-picker')).not.toBeInTheDocument();
  });

  it('refines an existing design with a chat instruction', async () => {
    state.versions = [version('a1', 1)];
    state.artifact = { ...version('a1', 1), content: JSON.stringify({ html: '<h1/>', css: '' }) };
    installFetch(state);
    renderPanel();

    await screen.findByTestId('design-version-1'); // wait until the design has loaded (enables refine)
    const input = screen.getByTestId('design-refine-input');
    fireEvent.change(input, { target: { value: 'make the header violet' } });
    fireEvent.click(screen.getByRole('button', { name: 'Refine' }));

    await waitFor(() => {
      expect(state.intentCalls.at(-1)).toMatchObject({
        action: 'refine',
        message: 'make the header violet',
      });
    });
  });

  it('approves and skips via the conductor', async () => {
    state.versions = [version('a1', 1)];
    state.artifact = { ...version('a1', 1), content: JSON.stringify({ html: '<h1/>', css: '' }) };
    installFetch(state);
    renderPanel();

    await screen.findByTestId('design-version-1'); // wait until the design has loaded (enables approve)
    fireEvent.click(screen.getByTestId('design-approve'));
    await waitFor(() => expect(state.intentCalls.at(-1)).toMatchObject({ action: 'proceed' }));
    // Approve means "done here" — the workspace should move on to the next stage on its own,
    // rather than leaving the user stuck looking at the now-approved design.
    await waitFor(() => expect(useWorkspaceStore.getState().activeStage).toBe('build'));

    fireEvent.click(screen.getByTestId('design-skip'));
    await waitFor(() => expect(state.intentCalls.at(-1)).toMatchObject({ action: 'skip' }));
    expect(useWorkspaceStore.getState().activeStage).toBe('build');
  });

  it('does not advance the stage on a refine — only on approve/skip', async () => {
    state.versions = [version('a1', 1)];
    state.artifact = { ...version('a1', 1), content: JSON.stringify({ html: '<h1/>', css: '' }) };
    installFetch(state);
    renderPanel();

    await screen.findByTestId('design-version-1');
    fireEvent.change(screen.getByTestId('design-refine-input'), {
      target: { value: 'make the header violet' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Refine' }));
    await waitFor(() => expect(state.intentCalls.at(-1)).toMatchObject({ action: 'refine' }));
    // A refine keeps you here to review what came back — it must not bounce you to Build.
    expect(useWorkspaceStore.getState().activeStage).not.toBe('build');
  });

  it('imports a bring-your-own design without a provider call', async () => {
    installFetch(state);
    renderPanel();

    fireEvent.click(await screen.findByTestId('design-import-toggle'));
    fireEvent.change(screen.getByTestId('design-own-html'), { target: { value: '<h1>mine</h1>' } });
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));

    await waitFor(() => {
      expect(state.intentCalls.at(-1)).toMatchObject({
        action: 'refine',
        payload: { own_design: { html: '<h1>mine</h1>', css: '' } },
      });
    });
  });

  // The requirements-first nudge (2026-08-06 reorder). It is a hint, never a gate: design stays
  // fully usable with no requirements at all, which is what the hero path depends on.
  describe('requirements nudge', () => {
    const untouched = (): MockState['stages'] => [
      { stage: 'design', status: 'empty', artifacts: [] },
      { stage: 'requirements', status: 'empty', artifacts: [] },
    ];

    it('suggests requirements first when they are untouched, without blocking design', async () => {
      state.stages = untouched();
      installFetch(state);
      renderPanel();

      expect(await screen.findByTestId('requirements-nudge')).toBeInTheDocument();
      // …and design still works: nothing is disabled by the missing requirements.
      fireEvent.change(screen.getByTestId('design-prompt'), { target: { value: 'a todo app' } });
      fireEvent.click(screen.getByRole('button', { name: 'Generate from text' }));
      await waitFor(() => expect(state.intentCalls.at(-1)).toMatchObject({ stage: 'design' }));
    });

    it('sends the user to the requirements stage', async () => {
      state.stages = untouched();
      installFetch(state);
      renderPanel();

      fireEvent.click(await screen.findByTestId('requirements-nudge-go'));
      expect(useWorkspaceStore.getState().activeStage).toBe('requirements');
    });

    it('goes away when dismissed', async () => {
      state.stages = untouched();
      installFetch(state);
      renderPanel();

      fireEvent.click(await screen.findByTestId('requirements-nudge-dismiss'));
      await waitFor(() =>
        expect(screen.queryByTestId('requirements-nudge')).not.toBeInTheDocument(),
      );
    });

    it.each(['skipped', 'complete', 'stale'] as const)(
      'stays quiet once requirements are %s — the user already decided',
      async (status) => {
        state.stages = [
          { stage: 'design', status: 'empty', artifacts: [] },
          { stage: 'requirements', status, artifacts: [] },
        ];
        installFetch(state);
        renderPanel();

        await screen.findByTestId('design-status');
        await waitFor(() =>
          expect(screen.queryByTestId('requirements-nudge')).not.toBeInTheDocument(),
        );
      },
    );
  });

  it('surfaces a provider error with its fallback hint', async () => {
    state.intentStatus = 502;
    installFetch(state);
    renderPanel();

    fireEvent.change(await screen.findByTestId('design-prompt'), { target: { value: 'x' } });
    fireEvent.click(screen.getByRole('button', { name: 'Generate from text' }));

    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(toasts.some((t) => t.description?.includes('switch to figma'))).toBe(true);
    });
  });
  describe("a provider's clarifying question", () => {
    const QUESTION: DesignQuestionDto = {
      id: 'q1',
      provider: 'stitch',
      question: 'Should I start with the public feed, or the admin tools?',
      suggestions: ['The public feed first', 'The admin tools first'],
      source: 'text',
      created_at: '',
    };

    it('shows the question when the provider is waiting on one', async () => {
      state.question = QUESTION;
      installFetch(state);
      renderPanel();

      const card = await screen.findByTestId('design-question');
      expect(card).toHaveTextContent('stitch needs a decision');
      expect(card).toHaveTextContent('public feed, or the admin tools');
    });

    it('sends a typed answer as a plain message, which is what the backend answers with', async () => {
      state.question = QUESTION;
      installFetch(state);
      renderPanel();
      await screen.findByTestId('design-question');

      fireEvent.change(screen.getByTestId('design-question-input'), {
        target: { value: 'Start with the public feed' },
      });
      fireEvent.click(screen.getByRole('button', { name: 'Send answer' }));

      await waitFor(() => {
        const call = state.intentCalls.at(-1);
        expect(call).toMatchObject({
          stage: 'design',
          action: 'refine',
          message: 'Start with the public feed',
        });
        // No payload: intake of any kind would read as a *new* request, not an answer.
        expect((call as { payload?: unknown }).payload).toBeUndefined();
      });
    });

    it('sends a suggested reply in one click', async () => {
      state.question = QUESTION;
      installFetch(state);
      renderPanel();
      await screen.findByTestId('design-question');

      fireEvent.click(screen.getByRole('button', { name: 'The admin tools first' }));

      await waitFor(() => {
        expect(state.intentCalls.at(-1)).toMatchObject({ message: 'The admin tools first' });
      });
    });

    it('offers designing without the provider that asked', async () => {
      state.question = QUESTION;
      installFetch(state);
      renderPanel();
      await screen.findByTestId('design-question');

      fireEvent.click(screen.getByRole('button', { name: 'Design without stitch' }));

      await waitFor(() => {
        expect(state.intentCalls.at(-1)).toMatchObject({
          stage: 'design',
          action: 'refine',
          payload: { dismiss_question: true },
        });
      });
    });

    it('shows nothing when the provider is not waiting on anything', async () => {
      installFetch(state); // state.question is null
      renderPanel();
      await screen.findByTestId('provider-health');

      expect(screen.queryByTestId('design-question')).not.toBeInTheDocument();
    });
  });
});
