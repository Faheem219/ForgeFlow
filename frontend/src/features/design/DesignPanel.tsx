import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { HelpCircle } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';

import { AgentWorking } from '../../components/AgentWorking';
import { Badge, Button, EmptyState, Input, Spinner } from '../../components/ui';
import { toastError } from '../../lib/errors';
import { useWorkspaceStore } from '../../lib/stores/workspaceStore';
import type { IntentRequest, ProviderHealth } from '../../lib/types';
import { listStages, submitIntent } from '../workspace/api';
import { nextStage, STATUS_LABELS, STATUS_TONES } from '../workspace/stageMeta';
import {
  fileToBase64,
  getDesignArtifact,
  getDesignProvider,
  getDesignQuestion,
  getDesignScreen,
  listDesignScreens,
  listDesignVersions,
  uploadDesignImages,
} from './api';
import { DesignPreview, type DesignScreen } from './DesignPreview';

const HEALTH_TONES: Record<ProviderHealth, 'success' | 'warning' | 'danger'> = {
  ok: 'success',
  degraded: 'warning',
  down: 'danger',
};

interface DesignContent {
  html: string;
  css: string;
  screens: DesignScreen[];
}

// A single shared empty array — `DesignPreview` keys a `useEffect` off this reference (via props),
// so handing it a fresh `[]` literal on every call site would re-fire that effect on every render.
const EMPTY_SCREENS: DesignScreen[] = [];

function parsePayload(content: string | null | undefined): DesignContent {
  if (!content) return { html: '', css: '', screens: EMPTY_SCREENS };
  try {
    const parsed = JSON.parse(content) as {
      html?: string;
      css?: string;
      screens?: DesignScreen[];
    };
    return {
      html: parsed.html ?? '',
      css: parsed.css ?? '',
      // Only present when the provider returned a multi-screen design.
      screens: Array.isArray(parsed.screens) ? parsed.screens : EMPTY_SCREENS,
    };
  } catch {
    return { html: content, css: '', screens: EMPTY_SCREENS };
  }
}

export function DesignPanel({ projectId }: { projectId: string }): JSX.Element {
  const queryClient = useQueryClient();
  const setActiveStage = useWorkspaceStore((s) => s.setActiveStage);
  const [prompt, setPrompt] = useState('');
  const [nudgeDismissed, setNudgeDismissed] = useState(false);
  const [instruction, setInstruction] = useState('');
  const [answer, setAnswer] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [screenRef, setScreenRef] = useState<string | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [ownHtml, setOwnHtml] = useState('');
  const [ownCss, setOwnCss] = useState('');
  const fileInput = useRef<HTMLInputElement>(null);

  const providerQuery = useQuery({
    queryKey: ['design-provider', projectId],
    queryFn: () => getDesignProvider(projectId),
  });
  const versionsQuery = useQuery({
    queryKey: ['design-versions', projectId],
    queryFn: () => listDesignVersions(projectId),
  });
  const stagesQuery = useQuery({
    queryKey: ['stages', projectId],
    queryFn: () => listStages(projectId),
  });
  // A provider can answer a brief with a question instead of a design ("shall I proceed with
  // these five screens?"). The stage then has nothing to preview and is waiting on the user, so
  // the question is rendered in that gap rather than only existing in the chat transcript.
  const questionQuery = useQuery({
    queryKey: ['design-question', projectId],
    queryFn: () => getDesignQuestion(projectId),
  });
  const question = questionQuery.data ?? null;

  const versions = useMemo(() => versionsQuery.data ?? [], [versionsQuery.data]);
  const latest = versions.at(-1);
  const latestId = latest?.id ?? null;
  const lastSeenLatest = useRef<string | null>(null);

  // Keep a valid selection, and *follow* a newly-arrived version — after a refine the user asked
  // for that design, so jumping to it is what they expect. Selecting an older version by hand
  // still sticks, because only a change of the newest id moves the selection.
  useEffect(() => {
    if (latestId === null) {
      lastSeenLatest.current = null;
      setSelectedId(null);
      return;
    }
    if (latestId !== lastSeenLatest.current) {
      lastSeenLatest.current = latestId;
      setSelectedId(latestId);
      return;
    }
    if (!selectedId || !versions.some((v) => v.id === selectedId)) {
      setSelectedId(latestId);
    }
  }, [versions, latestId, selectedId]);

  const contentQuery = useQuery({
    queryKey: ['design-artifact', selectedId],
    queryFn: () => getDesignArtifact(selectedId as string),
    enabled: Boolean(selectedId),
  });
  // Memoized on the raw content string — `parsePayload` builds a fresh object (and a fresh empty
  // `screens` array when the payload carries none) on every call, and an unmemoized inline call
  // here handed DesignPreview a new array reference on every DesignPanel render. DesignPreview
  // resets its active-screen state off that reference in a `useEffect`, so it was re-firing far
  // more often than the actual data ever changed.
  const version = useMemo(
    () => parsePayload(contentQuery.data?.content),
    [contentQuery.data?.content],
  );

  // -- screen picker ---------------------------------------------------------------------------
  // A design *version* holds the one screen that turn produced, but a real app is a set of screens
  // built up over many turns (landing, login, dashboard…). The provider knows the full set, so it
  // is listed here and any of them can be previewed — and refined — without hunting through
  // version history.
  const screensQuery = useQuery({
    queryKey: ['design-screens', projectId],
    queryFn: () => listDesignScreens(projectId),
  });
  const projectScreens = screensQuery.data?.screens ?? [];
  const versionRef =
    typeof contentQuery.data?.meta?.external_ref === 'string'
      ? contentQuery.data.meta.external_ref
      : '';

  // Opening a different *version* clears any screen choice; the picker then defaults to that
  // version's own screen. Deliberately not keyed on `versionRef` — the artifact can resolve after
  // the user has already picked a screen, and a default must never stomp a choice. The same
  // applies to the version list itself: its first resolution (null → latest id) is a load, not a
  // switch, and a screen picked while it was still in flight must survive it.
  const prevSelectedId = useRef<string | null>(null);
  useEffect(() => {
    const prev = prevSelectedId.current;
    prevSelectedId.current = selectedId;
    if (prev !== null && prev !== selectedId) setScreenRef(null);
  }, [selectedId]);

  const shownRef = screenRef ?? versionRef;
  const viewingOther = Boolean(screenRef) && screenRef !== versionRef;

  const screenQuery = useQuery({
    queryKey: ['design-screen', projectId, screenRef],
    queryFn: () => getDesignScreen(projectId, screenRef as string),
    // Only fetch when looking at a screen other than the one already in the artifact.
    enabled: viewingOther,
  });
  // Same reasoning as `version` above — a stable `screens` reference (not a fresh `[]` per render)
  // for whichever branch is active.
  const design = useMemo(
    () =>
      viewingOther
        ? {
            html: screenQuery.data?.html ?? '',
            css: screenQuery.data?.css ?? '',
            screens: EMPTY_SCREENS,
          }
        : version,
    [viewingOther, screenQuery.data?.html, screenQuery.data?.css, version],
  );
  /** What a refine targets: the screen on show, falling back to this version's own. */
  const refineRef = shownRef;

  const designStatus = stagesQuery.data?.find((s) => s.stage === 'design')?.status ?? 'empty';

  // Requirements now precedes design, and a design generated from a known feature list is a better
  // one. It is only ever a nudge: `empty` means the user has not decided yet, so suggest it once.
  // `skipped`/`complete`/`stale` are all deliberate choices — say nothing. Dismissal is component
  // state, not persisted: it must not survive as a rule that hides the hint on some later project
  // or visit where requirements are genuinely still untouched.
  const requirementsStatus =
    stagesQuery.data?.find((s) => s.stage === 'requirements')?.status ?? 'empty';
  const showRequirementsNudge =
    !nudgeDismissed && stagesQuery.isSuccess && requirementsStatus === 'empty';

  const intent = useMutation({
    mutationFn: (body: IntentRequest) => submitIntent(projectId, body),
    onSuccess: (_data, body) => {
      setPrompt('');
      setInstruction('');
      setAnswer('');
      setOwnHtml('');
      setOwnCss('');
      setImportOpen(false);
      void queryClient.invalidateQueries({ queryKey: ['design-versions', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['design-question', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['stages', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['messages', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['design-provider', projectId] });
      // Approve/skip both mean "I'm done here for now" — a refine keeps you on the stage to
      // review what came back, but jump forward once the stage itself has moved on.
      if (body.action === 'proceed' || body.action === 'skip') {
        const next = nextStage('design');
        if (next) setActiveStage(next);
      }
    },
    onError: (err) => toastError(err, 'Design action failed'),
  });

  const uploadAndGenerate = useMutation({
    mutationFn: async (files: File[]) => {
      const images = await Promise.all(
        files.map(async (file) => ({
          filename: file.name,
          media_type: file.type || 'image/png',
          data_base64: await fileToBase64(file),
        })),
      );
      const { images: refs } = await uploadDesignImages(projectId, images);
      return submitIntent(projectId, {
        stage: 'design',
        action: 'refine',
        payload: { image_refs: refs },
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['design-versions', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['design-question', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['stages', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['design-provider', projectId] });
    },
    onError: (err) => toastError(err, 'Upload failed'),
  });

  const busy = intent.isPending || uploadAndGenerate.isPending;
  const hasDesign = versions.length > 0;
  const provider = providerQuery.data;

  /** Answer the provider's question. A plain message with no payload *is* the answer. */
  function sendAnswer(text: string): void {
    const trimmed = text.trim();
    if (trimmed) intent.mutate({ stage: 'design', action: 'refine', message: trimmed });
  }

  function onFilesSelected(list: FileList | null): void {
    const files = list ? Array.from(list) : [];
    if (files.length > 0) uploadAndGenerate.mutate(files);
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-sm font-medium text-fg">
          Design
          <span data-testid="design-status">
            <Badge tone={STATUS_TONES[designStatus]}>{STATUS_LABELS[designStatus]}</Badge>
          </span>
          {provider ? (
            <span data-testid="provider-health">
              <Badge tone={HEALTH_TONES[provider.health]}>
                {provider.key}: {provider.health}
              </Badge>
            </span>
          ) : null}
        </span>
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="secondary"
            disabled={busy}
            data-testid="design-skip"
            onClick={() => intent.mutate({ stage: 'design', action: 'skip' })}
          >
            Skip
          </Button>
          <Button
            size="sm"
            disabled={busy || !hasDesign}
            data-testid="design-approve"
            onClick={() => intent.mutate({ stage: 'design', action: 'proceed' })}
          >
            Approve
          </Button>
        </div>
      </header>

      {question ? (
        <aside
          className="space-y-3 rounded-xl border border-brand/40 bg-brand/5 p-3"
          data-testid="design-question"
        >
          <div className="flex items-start gap-2">
            <HelpCircle aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-brand-text" />
            <div className="min-w-0 space-y-1">
              <p className="text-sm font-medium text-fg">
                {question.provider} needs a decision before it designs this
              </p>
              <p className="whitespace-pre-wrap break-words text-sm text-fg-muted">
                {question.question}
              </p>
            </div>
          </div>

          {question.suggestions.length > 0 ? (
            <div className="flex flex-wrap gap-2" data-testid="design-question-suggestions">
              {question.suggestions.map((suggestion) => (
                <Button
                  key={suggestion}
                  size="sm"
                  variant="secondary"
                  disabled={busy}
                  onClick={() => sendAnswer(suggestion)}
                >
                  {suggestion}
                </Button>
              ))}
            </div>
          ) : null}

          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              sendAnswer(answer);
            }}
          >
            <input
              aria-label="Answer the design question"
              data-testid="design-question-input"
              value={answer}
              onChange={(e) => setAnswer(e.target.value)}
              placeholder="Your answer…"
              disabled={busy}
              className="flex-1 rounded-lg border border-edge-strong bg-surface-sunken px-3 py-2 text-sm text-fg outline-none focus:border-brand disabled:opacity-50"
            />
            <Button type="submit" size="sm" disabled={busy || !answer.trim()}>
              Send answer
            </Button>
          </form>

          <Button
            size="sm"
            variant="ghost"
            disabled={busy}
            data-testid="design-question-dismiss"
            onClick={() =>
              intent.mutate({
                stage: 'design',
                action: 'refine',
                payload: { dismiss_question: true },
              })
            }
          >
            Design without {question.provider}
          </Button>
        </aside>
      ) : null}

      {showRequirementsNudge ? (
        <aside
          className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-edge bg-surface-sunken/60 px-3 py-2"
          data-testid="requirements-nudge"
        >
          <p className="text-xs text-fg-muted">
            Defining what your app needs to do first makes for a better design — but you can design
            now and capture requirements later.
          </p>
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="secondary"
              data-testid="requirements-nudge-go"
              onClick={() => setActiveStage('requirements')}
            >
              Go to Requirements
            </Button>
            <Button
              size="sm"
              variant="ghost"
              data-testid="requirements-nudge-dismiss"
              onClick={() => setNudgeDismissed(true)}
            >
              Dismiss
            </Button>
          </div>
        </aside>
      ) : null}

      <div className="grid min-h-0 flex-1 gap-3 lg:grid-cols-[minmax(0,1fr)_18rem]">
        {/* Preview + refine */}
        <div className="flex min-h-0 flex-col gap-2">
          {projectScreens.length > 1 ? (
            <div
              className="flex flex-wrap items-center gap-2 rounded-xl border border-edge px-3 py-2"
              data-testid="screen-picker"
            >
              <label htmlFor="design-screen" className="text-xs text-fg-muted">
                Screen
              </label>
              <select
                id="design-screen"
                data-testid="screen-select"
                value={shownRef}
                onChange={(e) => setScreenRef(e.target.value || null)}
                className="max-w-xs flex-1 rounded-lg border border-edge-strong bg-surface-sunken px-2 py-1 text-sm text-fg outline-none focus:border-brand"
              >
                {projectScreens.map((s) => (
                  <option key={s.ref} value={s.ref}>
                    {s.title || s.ref}
                    {s.ref === versionRef ? ' — this version' : ''}
                  </option>
                ))}
              </select>
              <span className="text-xs text-fg-subtle">
                {projectScreens.length} screens in this project · a refine changes the one shown
              </span>
            </div>
          ) : null}

          <div className="min-h-0 flex-1 overflow-hidden rounded-xl border border-edge">
            {busy ? (
              // Generating a design is a provider round-trip measured in tens of seconds, and the
              // preview is blank the whole time — the pipeline mark says what is happening.
              <div className="flex h-full items-center justify-center">
                <AgentWorking label="Generating the design" />
              </div>
            ) : contentQuery.isFetching || screenQuery.isFetching ? (
              // A refetch of an existing design is quick; a spinner is the honest weight for it.
              <div className="flex h-full items-center justify-center">
                <Spinner />
              </div>
            ) : (
              <DesignPreview html={design.html} css={design.css} screens={design.screens} />
            )}
          </div>
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (instruction.trim())
                intent.mutate({
                  stage: 'design',
                  action: 'refine',
                  message: instruction.trim(),
                  // Refine what is on screen, not whichever screen was generated last.
                  ...(refineRef ? { payload: { design_ref: refineRef } } : {}),
                });
            }}
          >
            <input
              aria-label="Refine instruction"
              data-testid="design-refine-input"
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              placeholder={
                hasDesign ? 'Refine, e.g. "make the header violet"' : 'Generate a design first'
              }
              disabled={!hasDesign || busy}
              className="flex-1 rounded-lg border border-edge-strong bg-surface-sunken px-3 py-2 text-sm text-fg outline-none focus:border-brand disabled:opacity-50"
            />
            <Button type="submit" size="sm" disabled={!hasDesign || busy || !instruction.trim()}>
              Refine
            </Button>
          </form>
        </div>

        {/* Intake + version history */}
        <div className="flex min-h-0 flex-col gap-3 overflow-auto">
          <section className="space-y-2 rounded-xl border border-edge p-3">
            <h4 className="text-xs uppercase tracking-wide text-fg-subtle">New design</h4>
            <form
              className="space-y-2"
              onSubmit={(e) => {
                e.preventDefault();
                // An empty box is a legitimate submit, not a no-op: the backend falls back to the
                // description that seeded requirements, then the requirements themselves, then (as
                // a true last resort) a generic starting design — a UI always gets generated,
                // never a dead end asking the user to type something they may not have yet.
                const trimmed = prompt.trim();
                intent.mutate({
                  stage: 'design',
                  action: 'refine',
                  ...(trimmed ? { payload: { text: trimmed } } : {}),
                });
              }}
            >
              <Input
                aria-label="Design prompt"
                data-testid="design-prompt"
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder={
                  hasDesign ? 'Describe the UI…' : 'Describe the UI… or just click Generate'
                }
                disabled={busy}
              />
              <Button
                type="submit"
                size="sm"
                disabled={busy || (hasDesign && !prompt.trim())}
                className="w-full"
              >
                Generate from text
              </Button>
            </form>

            <input
              ref={fileInput}
              type="file"
              accept="image/*"
              multiple
              hidden
              data-testid="design-file-input"
              onChange={(e) => onFilesSelected(e.target.files)}
            />
            <Button
              size="sm"
              variant="secondary"
              className="w-full"
              disabled={busy || provider?.capabilities.from_image === false}
              title={
                provider?.capabilities.from_image === false
                  ? `${provider.key} can't generate from screenshots`
                  : undefined
              }
              data-testid="design-upload"
              onClick={() => fileInput.current?.click()}
            >
              Upload screenshots
            </Button>

            <Button
              size="sm"
              variant="ghost"
              className="w-full"
              data-testid="design-import-toggle"
              onClick={() => setImportOpen((v) => !v)}
            >
              {importOpen ? 'Cancel import' : 'Import own design'}
            </Button>
            {importOpen ? (
              <form
                className="space-y-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (ownHtml.trim())
                    intent.mutate({
                      stage: 'design',
                      action: 'refine',
                      payload: { own_design: { html: ownHtml, css: ownCss } },
                    });
                }}
              >
                <textarea
                  aria-label="Import HTML"
                  data-testid="design-own-html"
                  value={ownHtml}
                  onChange={(e) => setOwnHtml(e.target.value)}
                  placeholder="<html>…"
                  rows={3}
                  className="w-full rounded-lg border border-edge-strong bg-surface-sunken px-2 py-1 font-mono text-xs text-fg"
                />
                <textarea
                  aria-label="Import CSS"
                  data-testid="design-own-css"
                  value={ownCss}
                  onChange={(e) => setOwnCss(e.target.value)}
                  placeholder="/* css */"
                  rows={2}
                  className="w-full rounded-lg border border-edge-strong bg-surface-sunken px-2 py-1 font-mono text-xs text-fg"
                />
                <Button
                  type="submit"
                  size="sm"
                  disabled={busy || !ownHtml.trim()}
                  className="w-full"
                >
                  Import
                </Button>
              </form>
            ) : null}
          </section>

          <section className="space-y-1 rounded-xl border border-edge p-3">
            <h4 className="text-xs uppercase tracking-wide text-fg-subtle">Versions</h4>
            {versions.length === 0 ? (
              <EmptyState title="No versions yet" description="Generate or import a design." />
            ) : (
              <ul className="space-y-1" data-testid="design-versions">
                {[...versions].reverse().map((v) => (
                  <li key={v.id}>
                    <button
                      type="button"
                      data-testid={`design-version-${v.version}`}
                      onClick={() => setSelectedId(v.id)}
                      className={`flex w-full items-center justify-between rounded-lg border px-2 py-1.5 text-left text-sm ${
                        v.id === selectedId
                          ? 'border-brand bg-brand/10'
                          : 'border-edge hover:bg-surface-raised'
                      }`}
                    >
                      <span className="text-fg">
                        v{v.version}
                        {v.id === latest?.id ? (
                          <span className="ml-1 text-xs text-fg-subtle">latest</span>
                        ) : null}
                      </span>
                      <span className="text-xs text-fg-subtle">
                        {String((v.meta.source as string) ?? '')}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}

export default DesignPanel;
