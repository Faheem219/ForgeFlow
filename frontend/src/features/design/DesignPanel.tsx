import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';

import { AgentWorking } from '../../components/AgentWorking';
import { Badge, Button, EmptyState, Input, Spinner } from '../../components/ui';
import { toastError } from '../../lib/errors';
import type { IntentRequest, ProviderHealth } from '../../lib/types';
import { listStages, submitIntent } from '../workspace/api';
import { STATUS_LABELS, STATUS_TONES } from '../workspace/stageMeta';
import {
  fileToBase64,
  getDesignArtifact,
  getDesignProvider,
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

function parsePayload(content: string | null | undefined): DesignContent {
  if (!content) return { html: '', css: '', screens: [] };
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
      screens: Array.isArray(parsed.screens) ? parsed.screens : [],
    };
  } catch {
    return { html: content, css: '', screens: [] };
  }
}

export function DesignPanel({ projectId }: { projectId: string }): JSX.Element {
  const queryClient = useQueryClient();
  const [prompt, setPrompt] = useState('');
  const [instruction, setInstruction] = useState('');
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
  const version = parsePayload(contentQuery.data?.content);

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
  const design = viewingOther
    ? { html: screenQuery.data?.html ?? '', css: screenQuery.data?.css ?? '', screens: [] }
    : version;
  /** What a refine targets: the screen on show, falling back to this version's own. */
  const refineRef = shownRef;

  const designStatus = stagesQuery.data?.find((s) => s.stage === 'design')?.status ?? 'empty';

  const intent = useMutation({
    mutationFn: (body: IntentRequest) => submitIntent(projectId, body),
    onSuccess: () => {
      setPrompt('');
      setInstruction('');
      setOwnHtml('');
      setOwnCss('');
      setImportOpen(false);
      void queryClient.invalidateQueries({ queryKey: ['design-versions', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['stages', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['messages', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['design-provider', projectId] });
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
      void queryClient.invalidateQueries({ queryKey: ['stages', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['design-provider', projectId] });
    },
    onError: (err) => toastError(err, 'Upload failed'),
  });

  const busy = intent.isPending || uploadAndGenerate.isPending;
  const hasDesign = versions.length > 0;
  const provider = providerQuery.data;

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
                if (prompt.trim())
                  intent.mutate({
                    stage: 'design',
                    action: 'refine',
                    payload: { text: prompt.trim() },
                  });
              }}
            >
              <Input
                aria-label="Design prompt"
                data-testid="design-prompt"
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder="Describe the UI…"
                disabled={busy}
              />
              <Button type="submit" size="sm" disabled={busy || !prompt.trim()} className="w-full">
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
