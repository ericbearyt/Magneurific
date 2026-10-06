#!/usr/bin/env python3
"""
SWC → TouchDesigner CSV Converter
==================================
Reads all .swc files from a skeletonization output directory and produces:

1. A normalized CSV ready for TouchDesigner's Table DAT import
2. An edge-list CSV for drawing line primitives between connected nodes
3. A summary JSON with per-segment statistics (node count, bounding box, etc.)

The CSV is designed so that TouchDesigner's Script SOP can directly iterate
rows and call appendPoint() / appendPoly() without any additional parsing.

Usage:
    python swc_to_csv.py <swc_directory> [--output <output_dir>]
    python swc_to_csv.py ../../skeletonization_output/ac3synapse_batch

Output files (in <output_dir> or <swc_directory>/touchdesigner/):
    nodes.csv         — one row per skeleton node
    edges.csv         — one row per parent-child edge
    segments.json     — per-segment metadata and stats
    td_import.py      — ready-to-paste Script SOP callback for TouchDesigner
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path


# ── Data types ───────────────────────────────────────────────────────────────

@dataclass
class SWCNode:
    """Single node from an SWC file."""
    node_id: int
    type_id: int
    x: float
    y: float
    z: float
    radius: float
    parent_id: int


@dataclass
class SegmentStats:
    """Summary statistics for one skeletonized segment."""
    segment_id: int
    node_count: int
    edge_count: int
    branch_points: int  # nodes with >1 child
    tips: int           # nodes with 0 children (leaf nodes)
    bbox_min: tuple[float, float, float]
    bbox_max: tuple[float, float, float]
    total_path_length: float  # sum of all edge lengths (normalized coords)
    mean_radius: float


# ── SWC Parsing ──────────────────────────────────────────────────────────────

def parse_swc(filepath: Path) -> tuple[list[SWCNode], tuple[float, float, float]]:
    """Parse an SWC file, returning nodes and the voxel scale from the header."""
    nodes: list[SWCNode] = []
    scale = (1.0, 1.0, 1.0)

    with open(filepath, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                # Try to extract SCALE from header
                if "SCALE" in line.upper():
                    parts = line.split()
                    try:
                        # "# SCALE 4.000000 4.000000 40.000000"
                        idx = next(
                            i for i, p in enumerate(parts)
                            if p.upper() == "SCALE"
                        )
                        scale = (
                            float(parts[idx + 1]),
                            float(parts[idx + 2]),
                            float(parts[idx + 3]),
                        )
                    except (StopIteration, IndexError, ValueError):
                        pass
                continue

            parts = line.split()
            if len(parts) < 7:
                continue

            try:
                nodes.append(SWCNode(
                    node_id=int(parts[0]),
                    type_id=int(parts[1]),
                    x=float(parts[2]),
                    y=float(parts[3]),
                    z=float(parts[4]),
                    radius=float(parts[5]),
                    parent_id=int(parts[6]),
                ))
            except ValueError:
                continue

    return nodes, scale


# ── Coordinate normalization ─────────────────────────────────────────────────

def normalize_coordinates(
    nodes: list[SWCNode],
    scale: tuple[float, float, float],
) -> list[dict]:
    """
    Normalize SWC coordinates for TouchDesigner consumption.

    Kimimaro's to_swc() already emits coordinates in physical space
    (nanometers), with the voxel scale baked in. The SCALE header is
    metadata only — we do NOT re-apply it here.

    The global centering and viewport scaling happen later in the
    CSV writing step.
    """
    normalized = []
    for n in nodes:
        normalized.append({
            "node_id": n.node_id,
            "type_id": n.type_id,
            "x": n.x,
            "y": n.y,
            "z": n.z,
            "radius": n.radius,
            "parent_id": n.parent_id,
        })

    return normalized


# ── Statistics ───────────────────────────────────────────────────────────────

def compute_segment_stats(
    segment_id: int,
    nodes: list[dict],
) -> SegmentStats:
    """Compute summary statistics for a single segment."""
    if not nodes:
        return SegmentStats(
            segment_id=segment_id,
            node_count=0, edge_count=0,
            branch_points=0, tips=0,
            bbox_min=(0, 0, 0), bbox_max=(0, 0, 0),
            total_path_length=0.0, mean_radius=0.0,
        )

    # Build child count map
    node_map = {n["node_id"]: n for n in nodes}
    child_count: dict[int, int] = {n["node_id"]: 0 for n in nodes}
    edge_count = 0
    total_path_length = 0.0

    for n in nodes:
        pid = n["parent_id"]
        if pid != -1 and pid in node_map:
            child_count[pid] = child_count.get(pid, 0) + 1
            edge_count += 1
            # Euclidean distance
            p = node_map[pid]
            dx = n["x"] - p["x"]
            dy = n["y"] - p["y"]
            dz = n["z"] - p["z"]
            total_path_length += math.sqrt(dx*dx + dy*dy + dz*dz)

    branch_points = sum(1 for c in child_count.values() if c > 1)
    tips = sum(1 for c in child_count.values() if c == 0)

    xs = [n["x"] for n in nodes]
    ys = [n["y"] for n in nodes]
    zs = [n["z"] for n in nodes]

    return SegmentStats(
        segment_id=segment_id,
        node_count=len(nodes),
        edge_count=edge_count,
        branch_points=branch_points,
        tips=tips,
        bbox_min=(min(xs), min(ys), min(zs)),
        bbox_max=(max(xs), max(ys), max(zs)),
        total_path_length=round(total_path_length, 2),
        mean_radius=round(sum(n["radius"] for n in nodes) / len(nodes), 2),
    )


# ── CSV Generation ───────────────────────────────────────────────────────────

def write_nodes_csv(
    filepath: Path,
    all_nodes: list[tuple[int, list[dict]]],
    global_offset: tuple[float, float, float],
    global_scale: float,
):
    """Write the master nodes CSV for TouchDesigner Table DAT."""
    with open(filepath, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "segment_id", "node_id", "type_id",
            "x", "y", "z",          # centered + scaled coordinates
            "x_raw", "y_raw", "z_raw",  # original coordinates (for reference)
            "radius", "radius_norm",  # raw and normalized radius
            "parent_id",
        ])

        ox, oy, oz = global_offset

        for seg_id, nodes in all_nodes:
            for n in nodes:
                # Center around origin and scale to TD-friendly range
                cx = (n["x"] - ox) * global_scale
                cy = (n["y"] - oy) * global_scale
                cz = (n["z"] - oz) * global_scale
                r_norm = n["radius"] * global_scale

                writer.writerow([
                    seg_id,
                    n["node_id"],
                    n["type_id"],
                    f"{cx:.6f}",
                    f"{cy:.6f}",
                    f"{cz:.6f}",
                    f"{n['x']:.2f}",
                    f"{n['y']:.2f}",
                    f"{n['z']:.2f}",
                    f"{n['radius']:.4f}",
                    f"{r_norm:.6f}",
                    n["parent_id"],
                ])


def write_edges_csv(
    filepath: Path,
    all_nodes: list[tuple[int, list[dict]]],
    global_offset: tuple[float, float, float],
    global_scale: float,
):
    """Write the edge-list CSV for building line primitives."""
    with open(filepath, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "segment_id",
            "child_node_id", "parent_node_id",
            "x0", "y0", "z0",  # parent position
            "x1", "y1", "z1",  # child position
            "length",           # edge length (normalized)
        ])

        ox, oy, oz = global_offset

        for seg_id, nodes in all_nodes:
            node_map = {n["node_id"]: n for n in nodes}
            for n in nodes:
                pid = n["parent_id"]
                if pid == -1 or pid not in node_map:
                    continue
                p = node_map[pid]

                cx0 = (p["x"] - ox) * global_scale
                cy0 = (p["y"] - oy) * global_scale
                cz0 = (p["z"] - oz) * global_scale
                cx1 = (n["x"] - ox) * global_scale
                cy1 = (n["y"] - oy) * global_scale
                cz1 = (n["z"] - oz) * global_scale

                length = math.sqrt(
                    (cx1-cx0)**2 + (cy1-cy0)**2 + (cz1-cz0)**2
                )

                writer.writerow([
                    seg_id,
                    n["node_id"], pid,
                    f"{cx0:.6f}", f"{cy0:.6f}", f"{cz0:.6f}",
                    f"{cx1:.6f}", f"{cy1:.6f}", f"{cz1:.6f}",
                    f"{length:.6f}",
                ])


# ── TouchDesigner Script SOP template ────────────────────────────────────────

TD_SCRIPT_TEMPLATE = '''\
"""
TouchDesigner Script SOP — Brain Circuit Skeleton Renderer
============================================================
Paste this into the Script SOP's cook() callback (script1_callbacks DAT).

Prerequisites:
  - A Table DAT named "nodes_table" that loads nodes.csv
  - A Table DAT named "edges_table" that loads edges.csv

This creates:
  1. Points at each skeleton node (with color per segment)
  2. Line primitives along each edge (parent→child)
"""

import colorsys

def cook(scriptOp):
    scriptOp.clear()

    nodes_table = op('nodes_table')
    edges_table = op('edges_table')

    if nodes_table is None or edges_table is None:
        return

    # ── Generate a distinct color for each segment ──
    segment_ids = set()
    for row in range(1, nodes_table.numRows):
        segment_ids.add(int(nodes_table[row, 'segment_id']))

    segment_colors = {}
    for i, sid in enumerate(sorted(segment_ids)):
        hue = (i * 0.618033988749895) % 1.0  # golden ratio spacing
        r, g, b = colorsys.hsv_to_rgb(hue, 0.75, 0.95)
        segment_colors[sid] = (r, g, b)

    # ── Create points ──
    point_lookup = {}  # (segment_id, node_id) -> point index
    for row in range(1, nodes_table.numRows):
        seg_id = int(nodes_table[row, 'segment_id'])
        node_id = int(nodes_table[row, 'node_id'])
        x = float(nodes_table[row, 'x'])
        y = float(nodes_table[row, 'y'])
        z = float(nodes_table[row, 'z'])
        radius = float(nodes_table[row, 'radius_norm'])

        pt = scriptOp.appendPoint()
        pt.P = tdu.Position(x, y, z)

        # Store color
        r, g, b = segment_colors.get(seg_id, (1, 1, 1))
        pt.Cd = tdu.Vector(r, g, b)

        # Store radius as pscale custom attribute
        # (use for instancing sphere size)
        pt.pscale = radius

        point_lookup[(seg_id, node_id)] = pt

    # ── Create edges as line primitives ──
    for row in range(1, edges_table.numRows):
        seg_id = int(edges_table[row, 'segment_id'])
        child_id = int(edges_table[row, 'child_node_id'])
        parent_id = int(edges_table[row, 'parent_node_id'])

        key_child = (seg_id, child_id)
        key_parent = (seg_id, parent_id)

        if key_child in point_lookup and key_parent in point_lookup:
            poly = scriptOp.appendPoly(2, closed=False)
            poly[0].point = point_lookup[key_parent]
            poly[1].point = point_lookup[key_child]

    return
'''


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Convert SWC skeleton files to TouchDesigner-ready CSV",
    )
    parser.add_argument(
        "swc_dir",
        type=Path,
        help="Directory containing .swc files (e.g., skeletonization_output/ac3synapse_batch)",
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=None,
        help="Output directory (default: <swc_dir>/touchdesigner/)",
    )
    parser.add_argument(
        "--viewport-size",
        type=float,
        default=10.0,
        help="Target viewport size — coordinates will be scaled so the "
             "longest axis spans this many units (default: 10.0)",
    )
    args = parser.parse_args()

    swc_dir = args.swc_dir.resolve()
    output_dir = (args.output or swc_dir / "touchdesigner").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    # Discover SWC files
    swc_files = sorted(
        swc_dir.glob("*.swc"),
        key=lambda p: int(p.stem) if p.stem.isdigit() else p.stem,
    )

    if not swc_files:
        print(f"ERROR: No .swc files found in {swc_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(swc_files)} SWC files in {swc_dir}")
    print(f"Output directory: {output_dir}")

    # ── Parse all SWC files ──────────────────────────────────────────────
    all_segments: list[tuple[int, list[dict]]] = []
    all_stats: list[SegmentStats] = []

    for swc_path in swc_files:
        seg_id = int(swc_path.stem) if swc_path.stem.isdigit() else hash(swc_path.stem) % 10000
        nodes, scale = parse_swc(swc_path)

        if not nodes:
            continue

        normalized = normalize_coordinates(nodes, scale)
        all_segments.append((seg_id, normalized))

        stats = compute_segment_stats(seg_id, normalized)
        all_stats.append(stats)

    print(f"Parsed {len(all_segments)} segments, {sum(len(n) for _, n in all_segments)} total nodes")

    # ── Compute global bounding box for centering ────────────────────────
    all_xs = [n["x"] for _, nodes in all_segments for n in nodes]
    all_ys = [n["y"] for _, nodes in all_segments for n in nodes]
    all_zs = [n["z"] for _, nodes in all_segments for n in nodes]

    center_x = (min(all_xs) + max(all_xs)) / 2.0
    center_y = (min(all_ys) + max(all_ys)) / 2.0
    center_z = (min(all_zs) + max(all_zs)) / 2.0

    extent = max(
        max(all_xs) - min(all_xs),
        max(all_ys) - min(all_ys),
        max(all_zs) - min(all_zs),
    )

    global_scale = args.viewport_size / extent if extent > 0 else 1.0
    global_offset = (center_x, center_y, center_z)

    print(f"Bounding box: X[{min(all_xs):.0f}, {max(all_xs):.0f}] "
          f"Y[{min(all_ys):.0f}, {max(all_ys):.0f}] "
          f"Z[{min(all_zs):.0f}, {max(all_zs):.0f}]")
    print(f"Center: ({center_x:.0f}, {center_y:.0f}, {center_z:.0f})")
    print(f"Scale factor: {global_scale:.8f}  (target viewport: {args.viewport_size})")

    # ── Write outputs ────────────────────────────────────────────────────
    nodes_path = output_dir / "nodes.csv"
    edges_path = output_dir / "edges.csv"
    stats_path = output_dir / "segments.json"
    script_path = output_dir / "td_script_sop.py"

    write_nodes_csv(nodes_path, all_segments, global_offset, global_scale)
    print(f"  ✓ Nodes CSV:    {nodes_path}")

    write_edges_csv(edges_path, all_segments, global_offset, global_scale)
    print(f"  ✓ Edges CSV:    {edges_path}")

    # Write segment stats
    stats_out = {
        "source_directory": str(swc_dir),
        "total_segments": len(all_segments),
        "total_nodes": sum(len(n) for _, n in all_segments),
        "total_edges": sum(s.edge_count for s in all_stats),
        "coordinate_system": {
            "center": list(global_offset),
            "scale": global_scale,
            "viewport_size": args.viewport_size,
            "note": "Coordinates in nodes.csv are centered at origin and scaled to fit within viewport_size units",
        },
        "segments": [
            {
                "segment_id": s.segment_id,
                "node_count": s.node_count,
                "edge_count": s.edge_count,
                "branch_points": s.branch_points,
                "tips": s.tips,
                "bbox_min": list(s.bbox_min),
                "bbox_max": list(s.bbox_max),
                "total_path_length": s.total_path_length,
                "mean_radius": s.mean_radius,
            }
            for s in all_stats
        ],
    }
    stats_path.write_text(json.dumps(stats_out, indent=2))
    print(f"  ✓ Stats JSON:   {stats_path}")

    # Write TD script template
    script_path.write_text(TD_SCRIPT_TEMPLATE)
    print(f"  ✓ TD Script:    {script_path}")

    # ── Summary ──────────────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("  TOUCHDESIGNER IMPORT READY")
    print("=" * 60)
    print(f"  Segments:       {len(all_segments)}")
    print(f"  Total nodes:    {sum(len(n) for _, n in all_segments)}")
    print(f"  Total edges:    {sum(s.edge_count for s in all_stats)}")
    print(f"  Branch points:  {sum(s.branch_points for s in all_stats)}")
    print(f"  Tip nodes:      {sum(s.tips for s in all_stats)}")
    print()
    print("  HOW TO USE IN TOUCHDESIGNER:")
    print("  1. Create a Table DAT → File mode → load nodes.csv")
    print("  2. Create a Table DAT → File mode → load edges.csv")
    print("  3. Create a Script SOP")
    print("  4. Paste td_script_sop.py into the Script SOP callbacks")
    print("  5. Reference the Table DATs as 'nodes_table' / 'edges_table'")
    print("=" * 60)


if __name__ == "__main__":
    main()
