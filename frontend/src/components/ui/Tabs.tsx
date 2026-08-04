import { useState } from 'react';
import type { ReactNode } from 'react';

export interface TabItem {
  id: string;
  label: string;
  content: ReactNode;
}

export function Tabs({ items, initialId }: { items: TabItem[]; initialId?: string }): JSX.Element {
  const [active, setActive] = useState(initialId ?? items[0]?.id);
  const current = items.find((t) => t.id === active) ?? items[0];

  return (
    <div>
      <div role="tablist" className="flex gap-1 border-b border-slate-800">
        {items.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={tab.id === active}
            onClick={() => setActive(tab.id)}
            className={`px-3 py-2 text-sm ${
              tab.id === active
                ? 'border-b-2 border-brand-500 text-slate-100'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <div role="tabpanel" className="py-4">
        {current?.content}
      </div>
    </div>
  );
}
