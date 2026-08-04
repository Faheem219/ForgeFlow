import { useEffect, useMemo, useState } from 'react';

import { EmptyState } from '../../components/ui';

/** One screen of a multi-screen design (a provider can return a whole flow at once). */
export interface DesignScreen {
  id: string;
  title: string;
  html: string;
  preview_image?: string | null;
}

/** Does the markup already carry its own `<html>` (a complete document, head and all)? */
function isFullDocument(html: string): boolean {
  return /<html[\s>]/i.test(html);
}

/**
 * Build the document the iframe renders.
 *
 * A provider that returns a **complete** document (Stitch does: `<head>` with font links, a
 * stylesheet and the Tailwind CDN) is used verbatim — wrapping it inside another `<body>` would
 * nest `html`/`head` elements and lose exactly the styling we are trying to show. A fragment gets
 * the usual scaffold.
 */
function buildSrcDoc(html: string, css: string): string {
  if (!html && !css) return '';
  if (isFullDocument(html)) {
    if (!css) return html;
    // Append our stylesheet last so it wins over the document's own rules.
    return html.replace(/<\/head>/i, `<style>${css}</style></head>`);
  }
  return `<!doctype html><html><head><meta charset="utf-8" /><style>${css}</style></head><body>${html}</body></html>`;
}

/**
 * Renders a generated design's HTML/CSS in a **sandboxed** iframe (via `srcdoc`).
 *
 * The markup is untrusted, so the frame is sandboxed — but `allow-scripts` is deliberately granted,
 * because real design exports style themselves at runtime (Stitch ships the Tailwind Play CDN,
 * which is a `<script>`). Without it the browser blocks the script and the design renders as
 * unstyled HTML, which is a false picture of what was generated.
 *
 * **`allow-same-origin` is deliberately NOT granted**, and that is the load-bearing part: the frame
 * runs in an *opaque origin*, so its scripts cannot reach the platform DOM, our cookies,
 * localStorage, or any same-origin API. Forms, popups and top-level navigation stay blocked too
 * (no `allow-forms` / `allow-popups` / `allow-top-navigation`). Granting `allow-scripts` *together
 * with* `allow-same-origin` would dissolve the sandbox entirely — never pair them.
 */
export function DesignPreview({
  html,
  css,
  screens = [],
}: {
  html: string;
  css: string;
  /** When a design has several screens, they are tabbed; the first is the primary. */
  screens?: DesignScreen[];
}): JSX.Element {
  const [activeId, setActiveId] = useState<string | null>(null);
  const multi = screens.length > 1;

  // Reset to the primary screen whenever a different design is shown.
  useEffect(() => {
    setActiveId(multi ? screens[0].id : null);
  }, [multi, screens]);

  const active = multi ? (screens.find((s) => s.id === activeId) ?? screens[0]) : null;
  const markup = active ? active.html : html;
  const srcDoc = useMemo(() => buildSrcDoc(markup, css), [markup, css]);

  if (!srcDoc) {
    return (
      <EmptyState title="Nothing to preview" description="Generate or import a design to see it." />
    );
  }

  const frame = (
    <iframe
      title="Design preview"
      data-testid="design-preview-frame"
      srcDoc={srcDoc}
      // Untrusted markup: scripts may run, but only in an opaque origin — never same-origin.
      sandbox="allow-scripts"
      referrerPolicy="no-referrer"
      className="h-full w-full border-0 bg-white"
    />
  );

  if (!multi) return frame;

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="design-screens">
      <div className="flex flex-wrap gap-1 border-b border-edge bg-surface px-2 py-1.5">
        {screens.map((screen) => (
          <button
            key={screen.id}
            type="button"
            data-testid={`screen-tab-${screen.id}`}
            aria-current={screen.id === active?.id}
            onClick={() => setActiveId(screen.id)}
            className={`max-w-[16rem] truncate rounded-md px-2 py-1 text-xs ${
              screen.id === active?.id
                ? 'bg-brand/20 text-brand-text'
                : 'text-fg-muted hover:bg-surface-raised'
            }`}
          >
            {screen.title || screen.id}
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1">{frame}</div>
    </div>
  );
}
