import { motion } from 'framer-motion';
import { useId, useState } from 'react';
import type { ReactNode } from 'react';

export interface TabItem {
  id: string;
  label: string;
  content: ReactNode;
}

export function Tabs({ items, initialId }: { items: TabItem[]; initialId?: string }): JSX.Element {
  const [active, setActive] = useState(initialId ?? items[0]?.id);
  const current = items.find((t) => t.id === active) ?? items[0];
  // Scopes the underline's layoutId so two Tabs on one page can't steal each other's indicator.
  const groupId = useId();

  return (
    <div>
      <div role="tablist" className="flex gap-1 border-b border-edge">
        {items.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={tab.id === active}
            onClick={() => setActive(tab.id)}
            className={`relative px-3 py-2 text-sm transition-colors ${
              tab.id === active ? 'text-fg' : 'text-fg-subtle hover:text-fg-muted'
            }`}
          >
            {tab.label}
            {tab.id === active ? (
              <motion.span
                layoutId={`tab-underline-${groupId}`}
                className="absolute inset-x-1 -bottom-px h-0.5 rounded-full bg-brand"
                transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
              />
            ) : null}
          </button>
        ))}
      </div>
      <div role="tabpanel" className="py-4">
        {current?.content}
      </div>
    </div>
  );
}
