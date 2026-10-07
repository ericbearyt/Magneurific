import { ApartmentOutlined, ExpandOutlined, NodeIndexOutlined } from "@ant-design/icons";
import { Button, Empty, Radio, Slider, Space, Tooltip, Typography } from "antd";
import { useWkSelector } from "libs/react_hooks";
import type React from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import type { EmptyObject } from "types/type_utils";
import CircuitRenderer, { type LayoutMode } from "./circuit_renderer";

const { Text } = Typography;

/**
 * CircuitVisualizerView — Right-border tab for visualizing brain circuit
 * skeletons in a dedicated 3D mini-viewport.
 *
 * This component renders skeletonized neurons as colored 3D tree structures
 * and (in future phases) will overlay synaptic connections, signal propagation
 * animations, and interactive selection.
 *
 * Architecture:
 *   - This React component handles the UI controls
 *   - CircuitRenderer handles the Three.js scene, camera, and rendering
 *   - Data comes from the existing WebKnossos skeleton tracing store
 */
const CircuitVisualizerView: React.FC<EmptyObject> = () => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const rendererRef = useRef<CircuitRenderer | null>(null);
  const [isInitialized, setIsInitialized] = useState(false);
  const [layoutMode, setLayoutMode] = useState<LayoutMode>("spatial");
  const [nodeScale, setNodeScale] = useState(1.0);
  const [segmentCount, setSegmentCount] = useState(0);
  const [nodeCount, setNodeCount] = useState(0);

  const skeletonTracing = useWkSelector((state) => state.annotation.skeleton);

  // Initialize the Three.js renderer when the canvas is available
  const initRenderer = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas || rendererRef.current) return;

    const renderer = new CircuitRenderer(canvas);
    rendererRef.current = renderer;
    setIsInitialized(true);

    return () => {
      renderer.dispose();
      rendererRef.current = null;
    };
  }, []);

  // Update the circuit when skeleton tracing data changes
  useEffect(() => {
    const renderer = rendererRef.current;
    if (!renderer || !skeletonTracing) return;

    const { trees } = skeletonTracing;
    const stats = renderer.updateFromSkeletonTracing(trees);
    setSegmentCount(stats.segmentCount);
    setNodeCount(stats.nodeCount);
  }, [skeletonTracing, isInitialized]);

  // Handle layout mode changes
  useEffect(() => {
    const renderer = rendererRef.current;
    if (!renderer) return;
    renderer.setLayoutMode(layoutMode);
  }, [layoutMode]);

  // Handle node scale changes
  useEffect(() => {
    const renderer = rendererRef.current;
    if (!renderer) return;
    renderer.setNodeScale(nodeScale);
  }, [nodeScale]);

  if (!skeletonTracing) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={
          <span>
            No skeleton tracing available. <br />
            Open a skeleton annotation to use the Circuit Visualizer.
          </span>
        }
      />
    );
  }

  return (
    <div
      id="circuit-visualizer-container"
      style={{ height: "100%", display: "flex", flexDirection: "column" }}
    >
      {/* Controls Bar */}
      <div style={{ padding: "8px 12px", borderBottom: "1px solid rgba(255,255,255,0.1)" }}>
        <Space direction="vertical" size="small" style={{ width: "100%" }}>
          {/* Layout Mode */}
          <div>
            <Text type="secondary" style={{ fontSize: 11, display: "block", marginBottom: 4 }}>
              Layout
            </Text>
            <Radio.Group
              size="small"
              value={layoutMode}
              onChange={(e) => setLayoutMode(e.target.value)}
              optionType="button"
              buttonStyle="solid"
            >
              <Tooltip title="Show neurons at their real 3D positions">
                <Radio.Button value="spatial">
                  <ExpandOutlined /> Spatial
                </Radio.Button>
              </Tooltip>
              <Tooltip title="Force-directed graph layout (topology)">
                <Radio.Button value="graph">
                  <ApartmentOutlined /> Graph
                </Radio.Button>
              </Tooltip>
            </Radio.Group>
          </div>

          {/* Node Scale */}
          <div>
            <Text type="secondary" style={{ fontSize: 11, display: "block", marginBottom: 4 }}>
              Node Scale: {nodeScale.toFixed(1)}x
            </Text>
            <Slider
              min={0.1}
              max={5.0}
              step={0.1}
              value={nodeScale}
              onChange={setNodeScale}
              style={{ margin: "0 4px" }}
            />
          </div>

          {/* Stats */}
          <div style={{ display: "flex", gap: 16 }}>
            <Text type="secondary" style={{ fontSize: 11 }}>
              <NodeIndexOutlined /> {segmentCount} segments
            </Text>
            <Text type="secondary" style={{ fontSize: 11 }}>
              {nodeCount} nodes
            </Text>
          </div>
        </Space>
      </div>

      {/* 3D Viewport */}
      <div style={{ flex: 1, position: "relative", minHeight: 300, background: "#0a0a0f" }}>
        {!isInitialized ? (
          <div
            style={{
              display: "flex",
              justifyContent: "center",
              alignItems: "center",
              height: "100%",
            }}
          >
            <Button type="primary" onClick={initRenderer} icon={<ApartmentOutlined />}>
              Initialize Circuit Visualizer
            </Button>
          </div>
        ) : null}
        <canvas
          ref={canvasRef}
          style={{
            width: "100%",
            height: "100%",
            display: isInitialized ? "block" : "none",
          }}
        />
      </div>
    </div>
  );
};

export default CircuitVisualizerView;
