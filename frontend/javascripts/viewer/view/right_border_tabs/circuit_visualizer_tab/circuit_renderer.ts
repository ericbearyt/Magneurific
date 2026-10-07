import {
  BufferAttribute,
  BufferGeometry,
  Color,
  LineBasicMaterial,
  LineSegments,
  PerspectiveCamera,
  Points,
  PointsMaterial,
  Scene,
  Sphere,
  Vector3 as ThreeVector3,
  WebGLRenderer,
} from "three";
import type { TreeMap } from "viewer/model/types/tree_types";

export type LayoutMode = "spatial" | "graph";

type CircuitStats = { segmentCount: number; nodeCount: number };

const BASE_POINT_SIZE = 4;
const FORCE_LAYOUT_ITERATIONS = 50;
// The repulsion step is quadratic in the node count, so it is skipped for large circuits.
const MAX_NODES_FOR_REPULSION = 1500;

export default class CircuitRenderer {
  private renderer: WebGLRenderer;
  private scene = new Scene();
  private camera = new PerspectiveCamera(45, 1, 0.1, 1e7);
  private geometry = new BufferGeometry();
  private points: Points;
  private lines: LineSegments;
  private resizeObserver: ResizeObserver;
  private layoutMode: LayoutMode = "spatial";
  private spatialPositions = new Float32Array(0);
  private edgeIndices: number[] = [];

  constructor(private canvas: HTMLCanvasElement) {
    this.renderer = new WebGLRenderer({ canvas, antialias: true });
    this.renderer.setPixelRatio(window.devicePixelRatio);
    this.scene.background = new Color(0x0a0a0f);
    this.points = new Points(
      this.geometry,
      new PointsMaterial({ size: BASE_POINT_SIZE, sizeAttenuation: false, vertexColors: true }),
    );
    this.lines = new LineSegments(this.geometry, new LineBasicMaterial({ vertexColors: true }));
    this.scene.add(this.lines, this.points);
    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(canvas);
    this.resize();
  }

  updateFromSkeletonTracing(trees: TreeMap): CircuitStats {
    const indexByNodeId = new Map<number, number>();
    const positions: number[] = [];
    const colors: number[] = [];
    this.edgeIndices = [];

    for (const tree of trees.values()) {
      if (!tree.isVisible) continue;
      for (const node of tree.nodes.values()) {
        indexByNodeId.set(node.id, positions.length / 3);
        positions.push(...node.untransformedPosition);
        colors.push(...tree.color);
      }
      for (const edge of tree.edges.all()) {
        const source = indexByNodeId.get(edge.source);
        const target = indexByNodeId.get(edge.target);
        if (source != null && target != null) this.edgeIndices.push(source, target);
      }
    }

    this.spatialPositions = new Float32Array(positions);
    this.geometry.setAttribute("color", new BufferAttribute(new Float32Array(colors), 3));
    this.geometry.setIndex(this.edgeIndices);
    this.applyLayout();

    let segmentCount = 0;
    for (const tree of trees.values()) if (tree.isVisible) segmentCount++;
    return { segmentCount, nodeCount: indexByNodeId.size };
  }

  setLayoutMode(layoutMode: LayoutMode) {
    this.layoutMode = layoutMode;
    this.applyLayout();
  }

  setNodeScale(scale: number) {
    (this.points.material as PointsMaterial).size = BASE_POINT_SIZE * scale;
    this.render();
  }

  dispose() {
    this.resizeObserver.disconnect();
    this.geometry.dispose();
    (this.points.material as PointsMaterial).dispose();
    (this.lines.material as LineBasicMaterial).dispose();
    this.renderer.dispose();
  }

  private applyLayout() {
    const positions =
      this.layoutMode === "graph"
        ? computeForceLayout(this.spatialPositions.length / 3, this.edgeIndices)
        : this.spatialPositions;
    this.geometry.setAttribute("position", new BufferAttribute(positions, 3));
    this.fitCamera();
    this.render();
  }

  private fitCamera() {
    this.geometry.computeBoundingSphere();
    const sphere = this.geometry.boundingSphere ?? new Sphere(new ThreeVector3(), 1);
    const radius = Math.max(sphere.radius, 1);
    const distance = radius / Math.sin((this.camera.fov * Math.PI) / 360);
    this.camera.position.copy(sphere.center).add(new ThreeVector3(0, 0, distance));
    this.camera.near = distance / 100;
    this.camera.far = distance * 100;
    this.camera.lookAt(sphere.center);
    this.camera.updateProjectionMatrix();
  }

  private resize() {
    const { clientWidth, clientHeight } = this.canvas;
    if (clientWidth === 0 || clientHeight === 0) return;
    this.renderer.setSize(clientWidth, clientHeight, false);
    this.camera.aspect = clientWidth / clientHeight;
    this.camera.updateProjectionMatrix();
    this.render();
  }

  private render() {
    this.renderer.render(this.scene, this.camera);
  }
}

// Simple spring-electrical layout: edges pull connected nodes together,
// all node pairs push each other apart (for circuits small enough).
function computeForceLayout(nodeCount: number, edgeIndices: number[]): Float32Array {
  const positions = new Float32Array(nodeCount * 3);
  for (let i = 0; i < positions.length; i++) positions[i] = (Math.random() - 0.5) * 100;
  const idealLength = 10;
  const useRepulsion = nodeCount <= MAX_NODES_FOR_REPULSION;

  for (let iteration = 0; iteration < FORCE_LAYOUT_ITERATIONS; iteration++) {
    const displacement = new Float32Array(nodeCount * 3);
    const step = idealLength * (1 - iteration / FORCE_LAYOUT_ITERATIONS);

    if (useRepulsion) {
      for (let a = 0; a < nodeCount; a++) {
        for (let b = a + 1; b < nodeCount; b++) {
          applyForce(positions, displacement, a, b, (distance) => -(idealLength ** 2) / distance);
        }
      }
    }
    for (let i = 0; i < edgeIndices.length; i += 2) {
      applyForce(positions, displacement, edgeIndices[i], edgeIndices[i + 1], (distance) => {
        return distance ** 2 / idealLength;
      });
    }
    for (let node = 0; node < nodeCount; node++) {
      const [dx, dy, dz] = displacement.subarray(node * 3, node * 3 + 3);
      const length = Math.hypot(dx, dy, dz) || 1;
      const scale = Math.min(length, step) / length;
      positions[node * 3] += dx * scale;
      positions[node * 3 + 1] += dy * scale;
      positions[node * 3 + 2] += dz * scale;
    }
  }
  return positions;
}

// Moves nodes a and b towards each other by `attraction(distance)` (negative values repel).
function applyForce(
  positions: Float32Array,
  displacement: Float32Array,
  a: number,
  b: number,
  attraction: (distance: number) => number,
) {
  const dx = positions[b * 3] - positions[a * 3];
  const dy = positions[b * 3 + 1] - positions[a * 3 + 1];
  const dz = positions[b * 3 + 2] - positions[a * 3 + 2];
  const distance = Math.hypot(dx, dy, dz) || 0.01;
  const force = attraction(distance) / distance;
  displacement[a * 3] += dx * force;
  displacement[a * 3 + 1] += dy * force;
  displacement[a * 3 + 2] += dz * force;
  displacement[b * 3] -= dx * force;
  displacement[b * 3 + 1] -= dy * force;
  displacement[b * 3 + 2] -= dz * force;
}
