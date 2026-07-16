import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';

import { Badge, Button, EmptyState, Input, Spinner } from '../../components/ui';
import { ApiError } from '../../lib/apiClient';
import { toast } from '../../lib/stores/toastStore';
import type { IntentRequest, ProviderHealth } from '../../lib/types';
import { listStages, submitIntent } from '../workspace/api';
import { STATUS_LABELS, STATUS_TONES } from '../workspace/stageMeta';
import {
  fileToBase64,
  getDesignArtifact,
  getDesignProvider,
  listDesignVersions,
  uploadDesignImages,
} from './api';
import { DesignPreview } from './DesignPreview';

const HEALTH_TONES: Record<ProviderHealth, 'success' | 'warning' | 'danger'> = {
  ok: 'success',
  degraded: 'warning',
  down: 'danger',
};

function parsePayload(content: string | null | undefined): { html: string; css: string } {
  if (!content) return { html: '', css: '' };
  try {
    const parsed = JSON.parse(content) as { html?: string; css?: string };
    return { html: parsed.html ?? '', css: parsed.css ?? '' };
  } catch {
    return { html: content, css: '' };
  }
}

export function DesignPanel({ projectId }: { projectId: string }): JSX.Element {
  const queryClient = useQueryClient();
  const [prompt, setPrompt] = useState('');
  const [instruction, setInstruction] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);
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

  // Keep a valid selection; follow the newest version as it arrives.
  useEffect(() => {
    if (versions.length === 0) {
      setSelectedId(null);
    } else if (!selectedId || !versions.some((v) => v.id === selectedId)) {
      setSelectedId(versions[versions.length - 1].id);
    }
  }, [versions, selectedId]);

  const contentQuery = useQuery({
    queryKey: ['design-artifact', selectedId],
    queryFn: () => getDesignArtifact(selectedId as string),
    enabled: Boolean(selectedId),
  });
  const design = parsePayload(contentQuery.data?.content);

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
    onError: (err) => {
      const description =
        err instanceof ApiError
          ? [err.message, err.fallbackHint].filter(Boolean).join(' — ')
          : 'Design action failed';
      toast({ title: 'Design action failed', description, variant: 'error' });
    },
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
    onError: (err) => {
      const description = err instanceof ApiError ? err.message : 'Upload failed';
      toast({ title: 'Upload failed', description, variant: 'error' });
    },
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
        <span className="flex items-center gap-2 text-sm font-medium text-slate-200">
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
          <div className="min-h-0 flex-1 overflow-hidden rounded-xl border border-slate-800">
            {contentQuery.isFetching ? (
              <div className="flex h-full items-center justify-center">
                <Spinner />
              </div>
            ) : (
              <DesignPreview html={design.html} css={design.css} />
            )}
          </div>
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (instruction.trim())
                intent.mutate({ stage: 'design', action: 'refine', message: instruction.trim() });
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
              className="flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-brand-500 disabled:opacity-50"
            />
            <Button type="submit" size="sm" disabled={!hasDesign || busy || !instruction.trim()}>
              Refine
            </Button>
          </form>
        </div>

        {/* Intake + version history */}
        <div className="flex min-h-0 flex-col gap-3 overflow-auto">
          <section className="space-y-2 rounded-xl border border-slate-800 p-3">
            <h4 className="text-xs uppercase tracking-wide text-slate-500">New design</h4>
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
                  className="w-full rounded-lg border border-slate-700 bg-slate-950 px-2 py-1 font-mono text-xs text-slate-100"
                />
                <textarea
                  aria-label="Import CSS"
                  data-testid="design-own-css"
                  value={ownCss}
                  onChange={(e) => setOwnCss(e.target.value)}
                  placeholder="/* css */"
                  rows={2}
                  className="w-full rounded-lg border border-slate-700 bg-slate-950 px-2 py-1 font-mono text-xs text-slate-100"
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

          <section className="space-y-1 rounded-xl border border-slate-800 p-3">
            <h4 className="text-xs uppercase tracking-wide text-slate-500">Versions</h4>
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
                          ? 'border-brand-500 bg-brand-600/10'
                          : 'border-slate-800 hover:bg-slate-800/50'
                      }`}
                    >
                      <span className="text-slate-200">
                        v{v.version}
                        {v.id === latest?.id ? (
                          <span className="ml-1 text-xs text-slate-500">latest</span>
                        ) : null}
                      </span>
                      <span className="text-xs text-slate-500">
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
