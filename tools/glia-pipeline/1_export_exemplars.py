# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "httpx",
#   "webknossos",
#   "numpy",
#   "tifffile",
# ]
# ///

"""CLI to export manual annotations from WebKnossos as training pairs.

This script takes a Volume Annotation ID and exports the raw EM data 
and the segmentation mask into a set of paired TIFF files.

Usage:
  python 1_export_exemplars.py --annotation-id <id> --dataset-name <ds> --layer-name <layer> --output-dir <path>
"""

import argparse
import os
from pathlib import Path

import httpx
import numpy as np
import tifffile
import webknossos as wk

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wk-uri", default="http://localhost:9000", help="WebKnossos URI")
    parser.add_argument("--token", required=True, help="WebKnossos user auth token")
    parser.add_argument("--annotation-id", required=True, help="ID of the volume annotation")
    parser.add_argument("--dataset-name", required=True, help="Name of the dataset (e.g. ac3synapse)")
    parser.add_argument("--organization-id", required=True, help="Organization ID")
    parser.add_argument("--layer-name", required=True, help="Name of the raw EM layer")
    parser.add_argument("--output-dir", default="./training_data", help="Output directory")
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    raw_dir = out_dir / "raw"
    labels_dir = out_dir / "labels"
    raw_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)

    with wk.webknossos_context(url=args.wk_uri, token=args.token):
        # 1. Download the annotation using WK library
        print(f"Downloading annotation {args.annotation_id}...")
        annotation = wk.Annotation.download(args.annotation_id)
        
        # 2. Open the dataset to get raw EM
        # Note: In a real environment, you'd use the correct dataset path or pull from API
        # For this script we will use the datastore API directly for the raw EM
        
        bbox = annotation.bounding_box
        print(f"Annotation bounding box: {bbox}")
        
        # Pull raw EM chunk
        mag = "1-1-1" # Assuming mag 1
        url = f"{args.wk_uri}/data/v9/datasets/{args.organization_id}/{args.dataset_name}/layers/{args.layer_name}/data"
        params = {
            "x": bbox.top_left.x, "y": bbox.top_left.y, "z": bbox.top_left.z,
            "width": bbox.width, "height": bbox.height, "depth": bbox.depth,
            "mag": mag,
            "token": args.token,
        }
        print(f"Fetching raw EM volume...")
        r = httpx.get(url, params=params, timeout=120.0)
        r.raise_for_status()
        
        # Parse raw EM (assuming uint8 for raw EM, adjust if uint16)
        raw_vol = np.frombuffer(r.content, dtype=np.uint8).reshape((bbox.width, bbox.height, bbox.depth), order="F")
        
        # Parse annotation volume
        # Annotation may have multiple segments, we just merge them to binary for binary glia seg,
        # or keep them separate for instance seg
        # wk.Annotation doesn't directly expose dense volume reading easily without the backend filesystem
        # but we can read it from the zip it downloaded.
        print(f"Processing annotation data...")
        vol_layer = list(annotation.volume_layers)[0]
        seg_vol = vol_layer.read(
            absolute_offset=bbox.top_left,
            size=bbox.shape,
            mag=wk.Mag(1)
        )

        # Transpose to Z, Y, X for standard TIFF saving and ML training
        raw_vol_zyx = np.transpose(raw_vol, (2, 1, 0))
        seg_vol_zyx = np.transpose(seg_vol, (2, 1, 0))

        # Save as single multi-page TIFFs or sequence of TIFFs. 
        # We save as a single multi-page 3D TIFF stack.
        raw_path = raw_dir / f"annot_{args.annotation_id}_raw.tif"
        label_path = labels_dir / f"annot_{args.annotation_id}_label.tif"
        
        tifffile.imwrite(raw_path, raw_vol_zyx)
        tifffile.imwrite(label_path, seg_vol_zyx)
        
        print(f"Export complete!")
        print(f"Raw: {raw_path} shape: {raw_vol_zyx.shape}")
        print(f"Labels: {label_path} shape: {seg_vol_zyx.shape}")

if __name__ == "__main__":
    main()
