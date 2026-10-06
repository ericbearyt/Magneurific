# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

"""Skeleton merger.

Walks every job-id subdirectory under
    <root>/<dataset_directory_name>/<layer_name>/*/
collects every per-job .nml produced by skeletonization-service, and writes
a single merged .nml that you can drag-and-drop into the WEBKNOSSOS NML
3D viewer.

Per-tree behaviour:
  - Trees from different jobs are concatenated.
  - Trees with the same segment id (e.g. produced when a job was re-run)
    are merged into one tree by concatenation. No edges are added between
    fragments — they remain disconnected sub-trees inside one tree, which
    matches kimimaro behaviour for fragmented segments.
  - Node ids are renumbered globally so the output is internally consistent.
  - Tree ids are renumbered 1..N.

Usage:
    uv run merge_skeletons.py <dataset_directory_name> <layer_name>
        [--root <skeletonization_output_dir>]
        [--out <output.nml>]

Examples:
    uv run merge_skeletons.py ac3synapse_zarr ac3synapse
    uv run merge_skeletons.py ac3synapse_zarr ac3synapse --out ~/Desktop/all.nml
"""

import argparse
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)


@dataclass
class Tree:
    segment_id: int | None
    name: str
    color: dict[str, str]
    nodes: list[ET.Element] = field(default_factory=list)
    edges: list[ET.Element] = field(default_factory=list)


SEGMENT_NAME_RE = re.compile(r"segment[_\- ]?(\d+)", re.IGNORECASE)


def main() -> int:
    setup_logging()
    args = parse_args()
    root = args.root.resolve()
    layer_dir = root / args.dataset / args.layer
    if not layer_dir.is_dir():
        logger.error(f"Layer directory not found: {layer_dir}")
        return 2

    nml_paths = sorted(layer_dir.rglob("*.nml"))
    # Skip a previously-merged file living at the root of the layer dir.
    nml_paths = [p for p in nml_paths if p.parent != layer_dir or "merged" not in p.name]
    if not nml_paths:
        logger.error(f"No .nml files found under {layer_dir}/<jobId>/")
        return 3
    logger.info(f"Found {len(nml_paths)} per-job .nml files under {layer_dir}")

    params, trees_by_segment = collect_trees(nml_paths)

    out_path = args.out or (layer_dir / f"merged_{args.dataset}_{args.layer}.nml")
    out_path = Path(out_path).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    write_merged_nml(out_path, params, trees_by_segment)

    total_trees = len(trees_by_segment)
    total_nodes = sum(len(t.nodes) for t in trees_by_segment.values())
    total_edges = sum(len(t.edges) for t in trees_by_segment.values())
    banner = "=" * 70
    logger.info(banner)
    logger.info("MERGED NML")
    logger.info(f"  output:  {out_path}")
    logger.info(f"  trees:   {total_trees}")
    logger.info(f"  nodes:   {total_nodes}")
    logger.info(f"  edges:   {total_edges}")
    logger.info(f"  source:  {len(nml_paths)} per-job NMLs")
    logger.info(banner)
    return 0


def collect_trees(nml_paths: list[Path]) -> tuple[ET.Element | None, dict[int | str, Tree]]:
    """Parse every NML, return (first-seen <parameters> element, segment_id -> Tree)."""
    params_proto: ET.Element | None = None
    trees_by_segment: dict[int | str, Tree] = {}

    for path in nml_paths:
        try:
            doc = ET.parse(path).getroot()
        except ET.ParseError as e:
            logger.warning(f"Skipping unreadable NML {path}: {e}")
            continue

        if params_proto is None:
            p = doc.find("parameters")
            if p is not None:
                params_proto = p

        for thing in doc.findall("thing"):
            name = thing.get("name", "")
            color = {k: v for k, v in thing.attrib.items() if k.startswith("color.")}
            seg_match = SEGMENT_NAME_RE.search(name)
            if seg_match:
                seg_id: int | str = int(seg_match.group(1))
            else:
                # Fall back to a synthetic key so the tree is preserved
                # even if it lacks a parseable "segment_<id>" name.
                seg_id = f"_anon::{path.parent.name}::{thing.get('id', '?')}"

            tree = trees_by_segment.get(seg_id)
            if tree is None:
                tree = Tree(
                    segment_id=seg_id if isinstance(seg_id, int) else None,
                    name=name,
                    color=color,
                )
                trees_by_segment[seg_id] = tree

            nodes_el = thing.find("nodes")
            if nodes_el is not None:
                tree.nodes.extend(list(nodes_el))
            edges_el = thing.find("edges")
            if edges_el is not None:
                tree.edges.extend(list(edges_el))

    return params_proto, trees_by_segment


def write_merged_nml(out_path: Path,
                     params_proto: ET.Element | None,
                     trees_by_segment: dict[int | str, Tree]) -> None:
    """Write the merged NML, renumbering node ids globally and tree ids 1..N."""
    root = ET.Element("things")
    if params_proto is not None:
        # Deep-copy params so we don't disturb the source elements.
        root.append(_clone(params_proto))
    else:
        ET.SubElement(root, "parameters")

    # Sort trees: numeric segment ids first (ascending), then anonymous keys (lex).
    def sort_key(item: tuple[int | str, Tree]):
        k, _ = item
        return (0, k) if isinstance(k, int) else (1, k)

    next_node_id = 1
    for tree_idx, (seg_key, tree) in enumerate(sorted(trees_by_segment.items(), key=sort_key), start=1):
        thing_attrs = {
            "id": str(tree_idx),
            "name": tree.name or (f"segment_{seg_key}" if isinstance(seg_key, int) else str(seg_key)),
        }
        thing_attrs.update(tree.color or {
            "color.r": "1.0", "color.g": "0.5", "color.b": "0.0", "color.a": "1.0",
        })
        thing_el = ET.SubElement(root, "thing", thing_attrs)
        nodes_el = ET.SubElement(thing_el, "nodes")
        edges_el = ET.SubElement(thing_el, "edges")

        local_to_global: dict[str, int] = {}
        for node in tree.nodes:
            old_id = node.get("id", "")
            if old_id in local_to_global:
                # Same fragment imported twice; skip duplicate.
                continue
            global_id = next_node_id
            next_node_id += 1
            local_to_global[old_id] = global_id
            new_node = ET.SubElement(nodes_el, "node", dict(node.attrib))
            new_node.set("id", str(global_id))

        for edge in tree.edges:
            src = edge.get("source", "")
            tgt = edge.get("target", "")
            if src in local_to_global and tgt in local_to_global:
                ET.SubElement(edges_el, "edge", {
                    "source": str(local_to_global[src]),
                    "target": str(local_to_global[tgt]),
                })

    ET.SubElement(root, "branchpoints")
    ET.SubElement(root, "comments")

    out_path.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))


def _clone(el: ET.Element) -> ET.Element:
    new = ET.Element(el.tag, dict(el.attrib))
    new.text = el.text
    new.tail = el.tail
    for child in el:
        new.append(_clone(child))
    return new


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("dataset", help="dataset_directory_name (e.g. ac3synapse_zarr)")
    p.add_argument("layer", help="segmentation layer name (e.g. ac3synapse)")
    p.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "skeletonization_output",
        help="root of skeletonization_output (default: <repo>/skeletonization_output)",
    )
    p.add_argument("--out", type=Path, default=None, help="output .nml path (default: <layer-dir>/merged_<dataset>_<layer>.nml)")
    return p.parse_args()


def setup_logging() -> None:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(message)s"))
    logger.addHandler(h)
    logger.setLevel(logging.INFO)


if __name__ == "__main__":
    sys.exit(main())
