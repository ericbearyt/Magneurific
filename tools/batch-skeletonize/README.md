# Batch Skeletonization Worker

Auto-skeletonize every segment in a dataset using [Kimimaro](https://github.com/seung-lab/kimimaro).

## Quick Start

```bash
# From the MagNeurific root directory:
source venv/bin/activate

# Skeletonize all 329 segments in ac3synapse
python tools/batch-skeletonize/batch_skeletonize.py \
    --input ./ac3synapse_zarr_v2 \
    --layer ac3synapse \
    --output ./skeletonization_output/ac3synapse_batch \
    --parallel 4
```

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--input, -i` | *required* | Path to dataset directory (Zarr/WKW) |
| `--layer, -l` | *required* | Name of segmentation layer |
| `--output, -o` | *required* | Output directory for SWC + NML files |
| `--voxel-size` | auto-detect | Voxel size in nm (e.g. `4,4,40`) |
| `--mag` | `1` | Magnification level |
| `--parallel` | `1` | Kimimaro parallel workers |
| `--dust-threshold` | `500` | Skip segments with fewer voxels |
| `--batch-size` | `0` (all) | Process segments in batches |
| `--resume` | off | Skip already-processed segment IDs |
| `--teasar-scale` | `4.0` | TEASAR scale parameter |
| `--teasar-const` | `500.0` | TEASAR const parameter |
| `--segment-ids` | all | Comma-separated list of specific IDs |

## Output

```
output_dir/
├── 1.swc              # One SWC per segment
├── 2.swc
├── ...
├── 329.swc
├── all_skeletons.nml  # Combined NML for WebKnossos import
└── manifest.json      # Run metadata
```

## Importing into WebKnossos

1. Open your dataset in WebKnossos
2. Click **Upload Annotation** (or drag-and-drop)
3. Select `all_skeletons.nml`
4. All 329 skeleton trees will appear overlaid on the segmentation
