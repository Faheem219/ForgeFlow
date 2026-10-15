import { X } from 'lucide-react';
import { useEffect, useRef } from 'react';

import { cn } from '../../lib/cn';
import { fileIcon } from './fileIcons';
import { type OpenFile, isDirty } from './ideStore';

interface EditorTabsProps {
  files: Record<string, OpenFile>;
  order: string[];
  active: string | null;
  onSelect: (path: string) => void;
  onClose: (path: string) => void;
}

/**
 * Open files, editor-style: the active tab shares the editor's surface and so reads as attached to
 * it; inactive tabs sit back on the strip. A dirty file shows a dot where its close button sits,
 * and the close button takes over on hover — the convention every editor has taught.
 */
export function EditorTabs({
  files,
  order,
  active,
  onSelect,
  onClose,
}: EditorTabsProps): JSX.Element | null {
  const stripRef = useRef<HTMLDivElement | null>(null);

  // Keep the active tab in view: a file opened from the tree, or by the agent writing it, can land
  // past the strip's visible end — the tab is selected but you cannot see which one it is.
  // Only the strip scrolls (never the page): `scrollIntoView` would also move every scrollable
  // ancestor to reveal the tab.
  useEffect(() => {
    const strip = stripRef.current;
    const tab = strip?.querySelector<HTMLElement>('[aria-selected="true"]')?.parentElement;
    if (!strip || !tab) return;
    const bounds = strip.getBoundingClientRect();
    const rect = tab.getBoundingClientRect();
    const reserve = 36; // the maximize button's footprint at the strip's right end
    if (rect.right > bounds.right - reserve)
      strip.scrollLeft += rect.right - (bounds.right - reserve);
    else if (rect.left < bounds.left) strip.scrollLeft -= bounds.left - rect.left;
  }, [active, order.length]);

  if (order.length === 0) return null;

  return (
    <div
      ref={stripRef}
      role="tablist"
      data-testid="editor-tabs"
      // The trailing filler gives the last tab room clear of the pane's maximize button, which
      // overlays the strip's right end.
      className="flex h-9 shrink-0 items-stretch overflow-x-auto bg-fg/[0.02] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
    >
      {order.map((path) => {
        const file = files[path];
        if (!file) return null;
        const dirty = isDirty(file);
        const isActive = path === active;
        const name = path.split('/').pop() ?? path;
        const { icon: Glyph, tone } = fileIcon(path);

        return (
          <div
            key={path}
            className={cn(
              'group relative flex shrink-0 items-center gap-1.5 border-r border-edge pl-3 pr-1.5 text-[13px] transition-colors duration-100',
              isActive
                ? 'bg-surface text-fg'
                : 'border-b text-fg-subtle hover:bg-fg/[0.03] hover:text-fg-muted',
            )}
          >
            {isActive ? (
              <span aria-hidden className="absolute inset-x-0 top-0 h-px bg-brand-text/70" />
            ) : null}
            <button
              type="button"
              role="tab"
              aria-selected={isActive}
              title={path}
              data-testid={`tab-${path}`}
              onClick={() => onSelect(path)}
              className="flex h-full items-center gap-1.5"
            >
              <Glyph aria-hidden className={cn('h-3.5 w-3.5', tone)} strokeWidth={1.75} />
              <span className={cn(file.generating && 'italic')}>{name}</span>
              {file.generating ? (
                <span
                  className="ml-0.5 flex items-center gap-1 text-2xs text-warning"
                  data-testid={`tab-generating-${path}`}
                >
                  <span
                    aria-hidden
                    className="h-1.5 w-1.5 rounded-full bg-warning motion-safe:animate-ff-breathe"
                  />
                  generating
                </span>
              ) : null}
            </button>
            <span className="relative flex h-5 w-5 items-center justify-center">
              {dirty && !file.generating ? (
                <span
                  className="absolute h-2 w-2 rounded-full bg-fg-muted transition-opacity group-hover:opacity-0"
                  aria-label="Unsaved changes"
                  data-testid={`tab-dirty-${path}`}
                />
              ) : null}
              <button
                type="button"
                aria-label={`Close ${name}`}
                data-testid={`tab-close-${path}`}
                onClick={() => onClose(path)}
                className={cn(
                  'relative flex h-5 w-5 items-center justify-center rounded text-fg-subtle transition-opacity hover:bg-fg/[0.08] hover:text-fg',
                  dirty && !file.generating
                    ? 'opacity-0 focus-visible:opacity-100 group-hover:opacity-100'
                    : isActive
                      ? 'opacity-100'
                      : 'opacity-0 focus-visible:opacity-100 group-hover:opacity-100',
                )}
              >
                <X aria-hidden className="h-3 w-3" strokeWidth={2} />
              </button>
            </span>
          </div>
        );
      })}
      {/* Fills the strip to the right edge, carrying the strip's bottom hairline under it. */}
      <div className="min-w-[2.75rem] flex-1 border-b border-edge" />
    </div>
  );
}
