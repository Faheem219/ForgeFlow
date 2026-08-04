import { Handle, Position } from '@xyflow/react';

import { Badge } from '../../components/ui';
import { NODE_STATUS_LABELS, NODE_STATUS_TONES, type TopologyNodeView } from './topologyModel';

const KIND_GLYPH: Record<TopologyNodeView['kind'], string> = {
  frontend: '🖥',
  backend: '⚙',
  database: '🗄',
};

/**
 * One box in the deployment graph. Shows what the node is, who hosts it, and its live status; the
 * URL and logs live in the drawer (click a node) to keep the graph readable.
 */
export function TopologyNode({ data }: { data: TopologyNodeView }): JSX.Element {
  const status = data.liveStatus;
  return (
    <div
      data-testid={`topology-node-${data.id}`}
      className="w-52 rounded-xl border border-edge-strong bg-surface-overlay px-3 py-2 text-left shadow-lg"
    >
      <Handle type="target" position={Position.Left} className="!bg-fg-faint" />
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium text-fg">
          <span aria-hidden className="mr-1.5">
            {KIND_GLYPH[data.kind]}
          </span>
          {data.label}
        </span>
        <span data-testid={`topology-status-${data.id}`}>
          <Badge tone={NODE_STATUS_TONES[status]}>{NODE_STATUS_LABELS[status]}</Badge>
        </span>
      </div>
      <p className="mt-1 truncate text-xs text-fg-subtle">{data.provider}</p>
      {data.liveUrl ? (
        <p className="mt-1 truncate text-xs text-fg-muted" title={data.liveUrl}>
          {data.liveUrl}
        </p>
      ) : null}
      <Handle type="source" position={Position.Right} className="!bg-fg-faint" />
    </div>
  );
}

export default TopologyNode;
