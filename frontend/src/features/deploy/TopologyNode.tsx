import { Handle, Position } from '@xyflow/react';
import { Database, Monitor, Server } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

import { Badge } from '../../components/ui';
import { cn } from '../../lib/cn';
import { NODE_STATUS_LABELS, NODE_STATUS_TONES, type TopologyNodeView } from './topologyModel';

const KIND_ICON: Record<TopologyNodeView['kind'], LucideIcon> = {
  frontend: Monitor,
  backend: Server,
  database: Database,
};

/**
 * One box in the deployment graph. Shows what the node is, who hosts it, and its live status; the
 * URL and logs live in the drawer (click a node) to keep the graph readable.
 */
export function TopologyNode({
  data,
}: {
  data: TopologyNodeView & { vertical?: boolean };
}): JSX.Element {
  const status = data.liveStatus;
  const Icon = KIND_ICON[data.kind];
  // Stacked layout (a narrow canvas): the wire enters at the top and leaves at the bottom.
  const inSide = data.vertical ? Position.Top : Position.Left;
  const outSide = data.vertical ? Position.Bottom : Position.Right;
  return (
    <div
      data-testid={`topology-node-${data.id}`}
      className={cn(
        // Health is said once, by the badge. Only a deploy in flight marks the card itself.
        'w-44 cursor-pointer rounded-xl border bg-surface-overlay px-3 py-2.5 text-left shadow-raise transition-colors duration-150 hover:border-brand/50',
        status === 'deploying' ? 'border-brand/50' : 'border-edge-strong',
      )}
    >
      <Handle type="target" position={inSide} />
      <div className="flex items-center gap-2.5">
        <span
          aria-hidden
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-fg/[0.06] text-fg-muted"
        >
          <Icon className="h-4 w-4" strokeWidth={1.75} />
        </span>
        <span className="min-w-0">
          <span className="block truncate text-[13px] font-medium text-fg">{data.label}</span>
          <span className="block truncate text-2xs text-fg-subtle">{data.provider}</span>
        </span>
      </div>
      <div className="mt-2 flex items-center justify-between gap-2">
        <span data-testid={`topology-status-${data.id}`}>
          <Badge tone={NODE_STATUS_TONES[status]} dot>
            {NODE_STATUS_LABELS[status]}
          </Badge>
        </span>
      </div>
      {data.liveUrl ? (
        <p className="mt-1.5 truncate font-mono text-2xs text-fg-subtle" title={data.liveUrl}>
          {data.liveUrl}
        </p>
      ) : null}
      <Handle type="source" position={outSide} />
    </div>
  );
}

export default TopologyNode;
