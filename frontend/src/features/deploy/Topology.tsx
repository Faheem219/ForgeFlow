import {
  Background,
  ReactFlow,
  type Node,
  type NodeMouseHandler,
  type NodeTypes,
} from '@xyflow/react';
import { useMemo } from 'react';

import { useThemeStore } from '../../lib/stores/themeStore';
import type { TopologyNodeId, TopologySnapshotDto } from '../../lib/types';
import { TopologyNode } from './TopologyNode';
import { buildFlowGraph, type LiveDeployState } from './topologyModel';

import '@xyflow/react/dist/style.css';

// React Flow types node `data` as an open `Record<string, unknown>`; our view model is a closed
// shape. The casts are confined to these two boundary lines so the rest of the feature stays
// precisely typed. Module-level, so React Flow sees one stable `nodeTypes` reference (a fresh
// object each render would remount every node).
const nodeTypes = { topologyNode: TopologyNode } as unknown as NodeTypes;

/**
 * The live component map: frontend → backend → database, each node's status driven by
 * `deploy.status` events. Clicking a node opens its details.
 */
export function Topology({
  snapshot,
  state,
  onSelect,
}: {
  snapshot: TopologySnapshotDto;
  state: LiveDeployState;
  onSelect: (nodeId: TopologyNodeId) => void;
}): JSX.Element {
  const { nodes, edges } = useMemo(() => buildFlowGraph(snapshot, state), [snapshot, state]);
  // React Flow's canvas chrome (edges, controls, background dots) keys on its own colorMode
  // rather than CSS variables — feed it the resolved app theme.
  const resolved = useThemeStore((s) => s.resolved);

  const handleNodeClick: NodeMouseHandler = (_event, node) => {
    onSelect(node.id as TopologyNodeId);
  };

  return (
    <div className="h-full min-h-[16rem] w-full" data-testid="deploy-topology">
      <ReactFlow
        nodes={nodes as unknown as Node[]}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodeClick={handleNodeClick}
        nodesDraggable={false}
        nodesConnectable={false}
        edgesFocusable={false}
        fitView
        colorMode={resolved}
        proOptions={{ hideAttribution: true }}
      >
        <Background />
      </ReactFlow>
    </div>
  );
}

export default Topology;
