#!/usr/bin/env python3
"""Batch auto-skeletonize an entire segmentation dataset.

Reads a segmentation layer from a WebKnossos-compatible Zarr (or WKW) dataset,
discovers all unique segment IDs, skeletonizes every segment via Kimimaro TEASAR,
and writes per-segment .swc files plus a combined .nml skeleton annotation.

Usage:
    python batch_skeletonize.py \
        --input ./ac3synapse_zarr \
        --layer ac3synapse \
        --output ./skeletonization_output/ac3synapse_batch \
        --voxel-size 4,4,40 \
        --parallel 4

The output directory will contain:
    <segment_id>.swc   — one SWC file per segment
    all_skeletons.nml  — combined NML importable into WebKnossos
    manifest.json      — metadata about the run
"""

import argparse
import json
import logging
import os
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import kimimaro
import numpy as np

try:
    from tqdm import tqdm
except ImportError:
    # Fallback if tqdm is not installed
    def tqdm(iterable, **kwargs):
        return iterable

logger = logging.getLogger("batch_skeletonize")


# ---------- CLI ----------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch-skeletonize all segments in a dataset.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--input", "-i", required=True,
        help="Path to dataset directory (Zarr or WKW with datasource-properties.json)",
    )
    parser.add_argument(
        "--layer", "-l", required=True,
        help="Name of the segmentation layer to skeletonize",
    )
    parser.add_argument(
        "--output", "-o", required=True,
        help="Output directory for SWC and NML files",
    )
    parser.add_argument(
        "--voxel-size", default=None,
        help="Voxel size in nm as x,y,z (e.g. '4,4,40'). "
             "Auto-detected from dataset if not provided.",
    )
    parser.add_argument(
        "--mag", default="1",
        help="Magnification level to read (default: '1')",
    )
    parser.add_argument(
        "--parallel", type=int, default=1,
        help="Number of parallel workers for Kimimaro (default: 1)",
    )
    parser.add_argument(
        "--dust-threshold", type=int, default=500,
        help="Skip segments with fewer voxels than this (default: 500)",
    )
    parser.add_argument(
        "--batch-size", type=int, default=0,
        help="Process segments in batches of this size. 0 = all at once (default: 0)",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Skip segment IDs that already have .swc files in the output directory",
    )
    parser.add_argument(
        "--teasar-scale", type=float, default=4.0,
        help="TEASAR scale parameter (default: 4.0)",
    )
    parser.add_argument(
        "--teasar-const", type=float, default=500.0,
        help="TEASAR const parameter (default: 500.0)",
    )
    parser.add_argument(
        "--segment-ids", default=None,
        help="Comma-separated list of specific segment IDs to process (default: all)",
    )
    return parser.parse_args()


# ---------- Data loading ----------

def load_volume_from_zarr(dataset_path: str, layer_name: str, mag: str) -> tuple[np.ndarray, tuple[float, float, float]]:
    """Load the full segmentation volume from a WebKnossos Zarr dataset."""
    import webknossos as wk

    ds = wk.Dataset.open(dataset_path)
    layer = ds.get_layer(layer_name)
    voxel_size = tuple(ds.voxel_size)

    mag_view = layer.get_mag(mag)
    bbox = layer.bounding_box

    logger.info(f"Loading volume: bbox={bbox}, mag={mag}, dtype={layer.dtype_per_channel}")
    logger.info(f"Voxel size: {voxel_size} nm")

    # Read the full volume — shape is (C, X, Y, Z)
    data = mag_view.read()

    # Squeeze channel dimension → (X, Y, Z)
    if data.ndim == 4 and data.shape[0] == 1:
        data = data[0]

    logger.info(f"Volume loaded: shape={data.shape}, dtype={data.dtype}, "
                f"non-zero voxels={int((data != 0).sum())}")

    return data, voxel_size


def discover_segment_ids(volume: np.ndarray, dust_threshold: int) -> list[int]:
    """Find all unique non-zero segment IDs, filtering by voxel count."""
    logger.info("Scanning volume for segment IDs...")
    unique_ids, counts = np.unique(volume, return_counts=True)

    # Build {id: voxel_count} mapping, excluding background (0)
    id_counts = {int(uid): int(cnt) for uid, cnt in zip(unique_ids, counts) if uid != 0}

    total = len(id_counts)
    kept = {uid: cnt for uid, cnt in id_counts.items() if cnt >= dust_threshold}
    skipped = total - len(kept)

    logger.info(f"Found {total} segment IDs total")
    if skipped > 0:
        logger.info(f"Skipping {skipped} segments with < {dust_threshold} voxels")
    logger.info(f"Will skeletonize {len(kept)} segments")

    return sorted(kept.keys())


# ---------- Skeletonization ----------

def skeletonize_batch(
    volume: np.ndarray,
    segment_ids: list[int],
    voxel_size: tuple[float, float, float],
    teasar_scale: float,
    teasar_const: float,
    dust_threshold: int,
    parallel: int,
) -> dict[int, any]:
    """Run Kimimaro on the volume for the given segment IDs."""
    logger.info(f"Running Kimimaro on {len(segment_ids)} segments "
                f"(parallel={parallel}, teasar_scale={teasar_scale}, teasar_const={teasar_const})...")

    start = time.time()

    skels = kimimaro.skeletonize(
        volume,
        teasar_params={
            "scale": teasar_scale,
            "const": teasar_const,
            "pdrf_scale": 100000,
            "pdrf_exponent": 4,
            "soma_acceptance_threshold": 3500,
            "soma_detection_threshold": 750,
            "soma_invalidation_scale": 1.0,
            "soma_invalidation_const": 300,
        },
        object_ids=segment_ids,
        anisotropy=voxel_size,
        dust_threshold=dust_threshold,
        progress=True,
        parallel=parallel,
    )

    elapsed = time.time() - start
    logger.info(f"Kimimaro finished in {elapsed:.1f}s — produced {len(skels)} skeletons")

    return skels


# ---------- Output ----------

def save_swc_files(skels: dict, output_dir: Path) -> list[Path]:
    """Save each skeleton as a .swc file."""
    paths = []
    for seg_id, skel in skels.items():
        swc_path = output_dir / f"{seg_id}.swc"
        swc_path.write_text(skel.to_swc())
        paths.append(swc_path)
    return paths


def build_nml(
    skels: dict,
    voxel_size_nm: tuple[float, float, float],
    dataset_name: str,
) -> bytes:
    """Build a combined NML file from all skeletons."""
    root = ET.Element("things")
    params = ET.SubElement(root, "parameters")
    ET.SubElement(params, "experiment", {"name": dataset_name})
    ET.SubElement(params, "scale", {
        "x": str(voxel_size_nm[0]),
        "y": str(voxel_size_nm[1]),
        "z": str(voxel_size_nm[2]),
    })
    ET.SubElement(params, "offset", {"x": "0", "y": "0", "z": "0"})

    next_node_id = 1
    for tree_idx, (seg_id, skel) in enumerate(skels.items(), start=1):
        thing = ET.SubElement(root, "thing", {
            "id": str(tree_idx),
            "name": f"segment_{seg_id}",
            "color.r": "1.0", "color.g": "0.5", "color.b": "0.0", "color.a": "1.0",
        })
        nodes_el = ET.SubElement(thing, "nodes")
        edges_el = ET.SubElement(thing, "edges")

        vertices = skel.vertices  # in world nm (from anisotropy)
        edges = skel.edges
        radii = getattr(skel, "radii", None)

        local_to_global = []
        for i, v in enumerate(vertices):
            node_id = next_node_id + i
            local_to_global.append(node_id)
            # Convert from nm back to voxel coordinates
            vx = v[0] / voxel_size_nm[0]
            vy = v[1] / voxel_size_nm[1]
            vz = v[2] / voxel_size_nm[2]
            r = float(radii[i]) if radii is not None else 1.0
            ET.SubElement(nodes_el, "node", {
                "id": str(node_id),
                "radius": f"{r:.2f}",
                "x": f"{vx:.2f}", "y": f"{vy:.2f}", "z": f"{vz:.2f}",
                "inMag": "1", "bitDepth": "8", "interpolation": "false",
            })
        for a, b in edges:
            ET.SubElement(edges_el, "edge", {
                "source": str(local_to_global[int(a)]),
                "target": str(local_to_global[int(b)]),
            })
        next_node_id += len(vertices)

    ET.SubElement(root, "branchpoints")
    ET.SubElement(root, "comments")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def save_manifest(
    output_dir: Path,
    args: argparse.Namespace,
    segment_ids: list[int],
    skels: dict,
    elapsed: float,
) -> None:
    """Save a JSON manifest with run metadata."""
    manifest = {
        "input": str(args.input),
        "layer": args.layer,
        "voxel_size": args.voxel_size,
        "mag": args.mag,
        "teasar_scale": args.teasar_scale,
        "teasar_const": args.teasar_const,
        "dust_threshold": args.dust_threshold,
        "parallel": args.parallel,
        "total_segments_found": len(segment_ids),
        "segments_skeletonized": len(skels),
        "segment_ids_processed": sorted(skels.keys()),
        "elapsed_seconds": round(elapsed, 2),
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    logger.info(f"Manifest saved: {manifest_path}")


# ---------- Main ----------

def main() -> None:
    setup_logging()
    args = parse_args()

    logger.info("=" * 60)
    logger.info("Batch Skeletonization Worker")
    logger.info("=" * 60)
    logger.info(f"Input:  {args.input}")
    logger.info(f"Layer:  {args.layer}")
    logger.info(f"Output: {args.output}")

    # Create output directory
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load volume
    volume, auto_voxel_size = load_volume_from_zarr(args.input, args.layer, args.mag)

    # Resolve voxel size
    if args.voxel_size:
        voxel_size = tuple(float(v) for v in args.voxel_size.split(","))
    else:
        voxel_size = auto_voxel_size
    logger.info(f"Using voxel size: {voxel_size} nm")

    # Discover segment IDs
    if args.segment_ids:
        segment_ids = [int(s) for s in args.segment_ids.split(",")]
        logger.info(f"Using user-specified segment IDs: {len(segment_ids)} segments")
    else:
        segment_ids = discover_segment_ids(volume, args.dust_threshold)

    if not segment_ids:
        logger.warning("No segment IDs found. Exiting.")
        return

    # Resume support: skip already-processed segments
    if args.resume:
        existing = {int(p.stem) for p in output_dir.glob("*.swc") if p.stem.isdigit()}
        before = len(segment_ids)
        segment_ids = [sid for sid in segment_ids if sid not in existing]
        skipped = before - len(segment_ids)
        if skipped > 0:
            logger.info(f"Resuming: skipping {skipped} already-processed segments")

    if not segment_ids:
        logger.info("All segments already processed. Nothing to do.")
        return

    # Run skeletonization
    start_time = time.time()

    if args.batch_size > 0 and len(segment_ids) > args.batch_size:
        # Process in batches
        all_skels = {}
        batches = [segment_ids[i:i + args.batch_size]
                    for i in range(0, len(segment_ids), args.batch_size)]
        for batch_idx, batch in enumerate(batches, 1):
            logger.info(f"--- Batch {batch_idx}/{len(batches)} ({len(batch)} segments) ---")
            batch_skels = skeletonize_batch(
                volume, batch, voxel_size,
                args.teasar_scale, args.teasar_const,
                args.dust_threshold, args.parallel,
            )
            # Save incrementally
            save_swc_files(batch_skels, output_dir)
            all_skels.update(batch_skels)
            logger.info(f"Batch {batch_idx} done: {len(batch_skels)} skeletons")
    else:
        all_skels = skeletonize_batch(
            volume, segment_ids, voxel_size,
            args.teasar_scale, args.teasar_const,
            args.dust_threshold, args.parallel,
        )
        save_swc_files(all_skels, output_dir)

    elapsed = time.time() - start_time

    # Build combined NML
    dataset_name = Path(args.input).stem
    # If resuming, also load previously saved skeletons for NML rebuild
    if args.resume:
        # Re-run on full set for NML? No — just build NML from what we have this run.
        # User can re-run without --resume to get the full NML.
        pass

    nml_path = output_dir / "all_skeletons.nml"
    nml_bytes = build_nml(all_skels, voxel_size, dataset_name)
    nml_path.write_bytes(nml_bytes)

    # Save manifest
    save_manifest(output_dir, args, segment_ids, all_skels, elapsed)

    # Summary
    banner = "=" * 60
    logger.info(banner)
    logger.info("BATCH SKELETONIZATION COMPLETE")
    logger.info(f"  Segments processed: {len(all_skels)}")
    logger.info(f"  SWC files:          {output_dir}/*.swc")
    logger.info(f"  Combined NML:       {nml_path}")
    logger.info(f"  Manifest:           {output_dir / 'manifest.json'}")
    logger.info(f"  Total time:         {elapsed:.1f}s")
    logger.info(banner)
    logger.info(f"Import {nml_path} into WebKnossos to view all skeletons.")


def setup_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


if __name__ == "__main__":
    main()
