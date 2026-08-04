import { type OpenFile, isDirty } from './ideStore';

interface EditorTabsProps {
  files: Record<string, OpenFile>;
  order: string[];
  active: string | null;
  onSelect: (path: string) => void;
  onClose: (path: string) => void;
}

export function EditorTabs({
  files,
  order,
  active,
  onSelect,
  onClose,
}: EditorTabsProps): JSX.Element | null {
  if (order.length === 0) return null;

  return (
    <div
      role="tablist"
      data-testid="editor-tabs"
      className="flex shrink-0 items-stretch overflow-x-auto border-b border-edge"
    >
      {order.map((path) => {
        const file = files[path];
        if (!file) return null;
        const dirty = isDirty(file);
        const isActive = path === active;
        const name = path.split('/').pop() ?? path;

        return (
          <div
            key={path}
            className={`flex items-center gap-1.5 border-r border-edge pl-3 pr-1.5 text-sm ${
              isActive ? 'bg-surface-raised text-fg' : 'text-fg-muted hover:bg-surface-raised'
            }`}
          >
            <button
              type="button"
              role="tab"
              aria-selected={isActive}
              title={path}
              data-testid={`tab-${path}`}
              onClick={() => onSelect(path)}
              className="py-1.5"
            >
              {name}
              {file.generating ? (
                <span
                  className="ml-1.5 text-xs text-warning"
                  data-testid={`tab-generating-${path}`}
                >
                  generating…
                </span>
              ) : dirty ? (
                <span
                  className="ml-1.5 text-brand-text"
                  aria-label="Unsaved changes"
                  data-testid={`tab-dirty-${path}`}
                >
                  ●
                </span>
              ) : null}
            </button>
            <button
              type="button"
              aria-label={`Close ${name}`}
              data-testid={`tab-close-${path}`}
              onClick={() => onClose(path)}
              className="rounded px-1 text-xs text-fg-subtle hover:bg-edge-strong hover:text-fg"
            >
              ✕
            </button>
          </div>
        );
      })}
    </div>
  );
}
