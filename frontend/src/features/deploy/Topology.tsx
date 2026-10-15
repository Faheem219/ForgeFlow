import {
  Background,
  BackgroundVariant,
  ReactFlow,
  type Node,
  type NodeMouseHandler,
  type NodeTypes,
  type ReactFlowInstance,
} from '@xyflow/react';
import { useEffect, useMemo, useRef, useState } from 'react';

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
  // The chain is drawn left-to-right, but a canvas squeezed beside the node drawer is taller than
  // it is wide — there, the same chain stacked top-to-bottom renders at a readable zoom instead of
  // three postage stamps. Only the drawing changes; the graph and its order are the model's.
  const [vertical, setVertical] = useState(false);
  const graph = useMemo(() => buildFlowGraph(snapshot, state), [snapshot, state]);
  const nodes = useMemo(
    () =>
      vertical
        ? graph.nodes.map((node, index) => ({
            ...node,
            position: { x: 0, y: index * 150 },
            data: { ...node.data, vertical: true },
          }))
        : graph.nodes,
    [graph.nodes, vertical],
  );
  const edges = graph.edges;
  // React Flow's canvas chrome (edges, controls, background dots) keys on its own colorMode
  // rather than CSS variables — feed it the resolved app theme.
  const resolved = useThemeStore((s) => s.resolved);

  const handleNodeClick: NodeMouseHandler = (_event, node) => {
    onSelect(node.id as TopologyNodeId);
  };

  // React Flow fits the view once, on init. The canvas widens and narrows as the node drawer opens
  // and closes (and as the panes are dragged), so refit whenever the container itself resizes —
  // otherwise a node opened from a full-width canvas can end up off the edge of a narrower one.
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const instanceRef = useRef<ReactFlowInstance | null>(null);
  useEffect(() => {
    const el = wrapperRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    let frame = 0;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry?.contentRect ?? { width: 0, height: 0 };
      if (width > 0) setVertical(width < 620 && height > width * 0.9);
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        void instanceRef.current?.fitView({ padding: 0.18, maxZoom: 1.1, duration: 200 });
      });
    });
    observer.observe(el);
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, []);

  // Switching between the stacked and the chained drawing moves every node — refit once it lands.
  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      void instanceRef.current?.fitView({ padding: 0.18, maxZoom: 1.1, duration: 200 });
    });
    return () => cancelAnimationFrame(frame);
  }, [vertical]);

  return (
    <div
      ref={wrapperRef}
      className="ff-flow h-full min-h-[16rem] w-full"
      data-testid="deploy-topology"
    >
      <ReactFlow
        onInit={(instance) => {
          instanceRef.current = instance as unknown as ReactFlowInstance;
        }}
        fitViewOptions={{ padding: 0.18, maxZoom: 1.1 }}
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
        <Background variant={BackgroundVariant.Dots} gap={16} size={1} />
      </ReactFlow>
    </div>
  );
}

export default Topology;
