# WebKnossos Workers Overview

This repository contains several background workers and services designed to process WebKnossos datasets, scale out operations like skeletonization, and train/run machine learning models for segmentation.

All of these are located in the `tools/` directory.

## 1. Skeletonization & Skeleton Processing
**Location:** `tools/skeletonization-service/`, `tools/batch-skeletonize/`, `tools/skeleton-merge/`

Handles the conversion of 3D segmentations into 1D graph representations (skeletons/trees).
- **Skeletonization Service:** A background task worker integrated directly with WebKnossos. It listens for skeletonization jobs dispatched by the WebKnossos backend, runs the Kimimaro algorithm, and pushes the resulting `.nml` skeleton back to the instance.
- **Batch Skeletonize:** A local script to auto-skeletonize entire datasets in parallel.
- **Skeleton Merger (`tools/skeleton-merge/merge_skeletons.py`):** Combines all per-job NMLs produced by the Skeletonization Service for one dataset/layer into a single merged NML you can drop into the WebKnossos NML 3D viewer. Renumbers node ids globally, deduplicates trees by segment id (concatenating fragments), and preserves the source `<parameters>` (scale, experiment). Run via `uv run tools/skeleton-merge/merge_skeletons.py <dataset_directory_name> <layer_name>`.
- **Skeleton Post-Processor (Planned):** A worker that analyzes raw Kimimaro outputs to prune spurs, smooth paths, and compute branch/path length stats (`analyze_skeleton` job). Distinct from the Skeleton Merger above, which only concatenates without geometric cleanup.

## 2. Segmentation & ML Inference Pipelines
**Location:** `tools/glia-pipeline/`

A distributed agent-based pipeline for segmenting glia, organelles, and sub-cellular structures.
- **Glia Segmentation:** A suite of scripts to export exemplars, train 3D U-Net models (`train_glia_model` job), infer glia (`infer_glia` job), and push connected components back as segmentation layers (`push_glia_segmentation` job).
- **Soma / Cell-Body Detector (Planned):** A full-volume soma classifier (CNN on downsampled mag) that complements TEASAR thresholds for more robust detection (`detect_somas` job).
- **Synapse Detector Worker (Planned):** Infers synapses over a volume (`infer_synapses` job) to generate presynaptic/postsynaptic blob masks and partner pairings. Pairs directly with the skeleton output for connectomics.
- **Mitochondria Detector (Planned):** A model worker to fulfill the `infer_mitochondria` job enum for automated organelle detection.

## 3. Connectome Builder (Planned)
**Location:** TBD

A pipeline component that joins skeleton logic (`skeleton.swc`) and synapse pairings to generate a formal connectome graph (e.g. CSV or SQLite format) via the `build_connectome` job. Depends on outputs from both Skeletonization and the Synapse Detector.

## 4. Mesh Services
**Location:** `tools/mesh-format/`, `tools/obj_models/`, `tools/mesh-service/`

Utilities and background workers for generating, decimating, and migrating 3D meshes.
- **Mesh Generator (Planned):** A local worker implementation of the `compute_mesh_file` job using zmesh/marching-cubes to provide offline mesh generation.
- **Mesh Decimation (Planned):** Decimates 3D meshes for massive segments (`decimate_mesh` job) to improve frontend rendering performance.
- **Format Tools:** Scripts for migrating old mesh files and converting OBJ models to frontend-compatible formats (like THREE.js).

## 5. Agglomeration & Proofreading
**Location:** `tools/agglomerate-service/`, `tools/apply-mapping/`

Supports the WebKnossos proofreading UI and segment groupings.
- **Agglomerate Service:** Handles background tasks related to segment groupings. Includes the **Agglomeration Graph Builder** (`build_agglomerate_graph` job) which enables the primary WebKnossos proofreading UI workflows.
- **Apply Mapping:** Worker for applying segment ID mappings (from proofreading edits) across an entire dataset.

## 6. Indexing & Dataset Ingestion
**Location:** `tools/ingest-service/`, `tools/segment-index-service/`, `tools/merge-volume-into-data-layer/`

- **Ingest Service:** Handles the ingestion of large datasets into Zarr/WKW formats and prepares layer configurations.
- **Merge Volume:** Worker script to merge a 3D volume into an existing WebKnossos data layer.
- **Segment Index Service (Planned):** A local implementation of the `compute_segment_index_file` job required for fast segment lookups in massive datasets.

## 7. Data Migration & Format Tools
Various scripts for data conversion and migration:
- `tools/migration-unified-annotation-versioning/`: Handles migrating unified annotations to newer formats.
- `tools/migrate-editable-mappings/`: Migrates editable mapping files.
- `tools/migrate-axis-bounds/`: Migrates dataset axis bounds.
- `tools/skeleton-migration/`: Migration logic for skeleton action updates.
- `tools/nml/`: Contains `.nml` generators and volume fallback scripts.
- `tools/mapping-format/`: Tools for inspecting JSON ID mappings.
- `tools/segmentation-id-modification/`: Scripts for incrementing or modifying segmentation IDs in agglomerate files or layers.

## 8. Infrastructure, Deletion, & Statistics
- **Orchestrator Service (`tools/orchestrator-service/`):** Coordinates multi-stage distributed workflows.
- **Deletion Service (`tools/deletion-service/`):** Safely deletes large datasets asynchronously.
- **Dataset Stats Worker (`tools/statistics/`):** Scripting to extract server and usage statistics. Will be expanded to compute volume-wide voxel histograms and segment size distributions.
- **Infrastructure:** `tools/hosting/` (Docker Compose hosting), `tools/dev_deployment/` (refresh schemas), `tools/postgres/` (`dbtool.js` job routing).
- **Testing & Debugging:** `tools/volume-stress-test/` (stress-test bash scripts), `tools/debugging/` (e.g. `version-visualizer`).

---

### Starting WebKnossos Workers

Most background workers require a valid `WORKER_KEY` defined in WebKnossos to authenticate with the API. 
Workers polling for jobs typically require passing the WebKnossos instance URL:

```bash
export WORKER_KEY="your-secret-worker-key"
uv run tools/worker-directory/main.py --wk-uri http://localhost:9000
```
