import { useCallback, useLayoutEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';

/**
 * Tabs with a sliding active indicator.
 *
 * The indicator is one absolutely-positioned element whose transform follows the active tab, rather
 * than a per-tab bottom border that pops between states. Its geometry is measured in a layout
 * effect (not read from refs during render, which would be empty on first paint) and re-measured on
 * container resize, so it stays aligned when labels reflow. Arrow-key navigation is wired because
 * `role="tablist"` promises it.
 */

export interface TabItem {
  id: string;
  label: string;
  content: ReactNode;
  /** Optional trailing count, e.g. failing tests. */
  badge?: ReactNode;
}

interface Indicator {
  left: number;
  width: number;
}

export function Tabs({
  items,
  initialId,
  onChange,
}: {
  items: TabItem[];
  initialId?: string;
  onChange?: (id: string) => void;
}): JSX.Element {
  const [active, setActive] = useState(initialId ?? items[0]?.id);
  const [indicator, setIndicator] = useState<Indicator>({ left: 0, width: 0 });
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const listRef = useRef<HTMLDivElement | null>(null);

  const current = items.find((t) => t.id === active) ?? items[0];
  const currentId = current?.id ?? '';
  const activeIndex = Math.max(
    items.findIndex((t) => t.id === currentId),
    0,
  );

  const measure = useCallback(() => {
    const el = tabRefs.current[currentId];
    if (!el) return;
    setIndicator({ left: el.offsetLeft, width: el.offsetWidth });
  }, [currentId]);

  useLayoutEffect(() => {
    measure();
    const list = listRef.current;
    if (!list || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(measure);
    observer.observe(list);
    return () => observer.disconnect();
  }, [measure]);

  function select(id: string): void {
    setActive(id);
    onChange?.(id);
  }

  function onKeyDown(event: React.KeyboardEvent): void {
    const delta = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
    if (delta === 0) return;
    event.preventDefault();
    const next = items[(activeIndex + delta + items.length) % items.length];
    select(next.id);
    tabRefs.current[next.id]?.focus();
  }

  return (
    <div>
      <div
        ref={listRef}
        role="tablist"
        onKeyDown={onKeyDown}
        className="relative flex gap-0.5 border-b border-subtle"
      >
        {items.map((tab) => {
          const selected = tab.id === currentId;
          return (
            <button
              key={tab.id}
              ref={(el) => {
                tabRefs.current[tab.id] = el;
              }}
              type="button"
              role="tab"
              aria-selected={selected}
              tabIndex={selected ? 0 : -1}
              onClick={() => select(tab.id)}
              className={`relative flex items-center gap-1.5 rounded-t px-3 py-2 text-sm transition-colors duration-fast ease-out ${
                selected ? 'text-primary' : 'text-muted hover:text-secondary'
              }`}
            >
              {tab.label}
              {tab.badge}
            </button>
          );
        })}
        <span
          aria-hidden
          className="pointer-events-none absolute bottom-0 left-0 h-px bg-brand transition-[transform,width] duration-base ease-out"
          style={{ width: indicator.width, transform: `translateX(${indicator.left}px)` }}
        />
      </div>
      <div role="tabpanel" className="animate-fade-in py-3.5">
        {current?.content}
      </div>
    </div>
  );
}
