import { useMemo } from 'react';

import { EmptyState } from '../../components/ui';

/**
 * Renders a generated design's HTML/CSS in a **fully sandboxed** iframe (via ``srcdoc``). The markup
 * is untrusted, so the iframe gets an empty ``sandbox`` (no scripts, no same-origin) and is never
 * injected into the platform DOM (phase-19 design note).
 */
export function DesignPreview({ html, css }: { html: string; css: string }): JSX.Element {
  const srcDoc = useMemo(() => {
    if (!html && !css) return '';
    return `<!doctype html><html><head><meta charset="utf-8" /><style>${css}</style></head><body>${html}</body></html>`;
  }, [html, css]);

  if (!srcDoc) {
    return (
      <EmptyState title="Nothing to preview" description="Generate or import a design to see it." />
    );
  }

  return (
    <iframe
      title="Design preview"
      data-testid="design-preview-frame"
      srcDoc={srcDoc}
      // Untrusted markup: no scripts, no same-origin. Never rendered into the platform DOM.
      sandbox=""
      className="h-full w-full border-0 bg-white"
    />
  );
}
