### Added
- Added three offline workers for WK feature parity:
  - `mesh-service` (`compute_mesh_file`): generates per-segment STL meshes via zmesh into `binaryData/<org>/<dir>/<layer>/meshes/<jobId>/`. NOTE: full WK HDF5 mesh format (bucket-keyed neuroglancer chunks) is not yet emitted; STL output is for download, not auto-load. See TODO at the bottom of `tools/mesh-service/main.py` for the chunk-packing path.
  - `segment-index-service` (`compute_segment_index_file`): writes a WK-compatible HDF5 segment index file (`hash_bucket_offsets`, `hash_buckets`, `top_lefts` + `hash_function`/`n_hash_buckets`/`dtype_bucket_entries` attrs) at `<layer>/segmentIndex/<layer>.hdf5`. Uses identity hash; chunk discovery is heuristic and works on uncompressed cubic chunks.
  - `agglomerate-service` (new `build_agglomerate_graph` command): writes a WK-compatible HDF5 agglomerate file (`segment_to_agglomerate`, `agglomerate_to_segments`, `agglomerate_to_segments_offsets`) at `<layer>/agglomerates/<name>.hdf5`. Default mapping is identity; intended as a starting point that real proofreading classifiers can overwrite.
- New `JobCommand.build_agglomerate_graph` plus `POST /jobs/run/buildAgglomerateGraph/:datasetId` endpoint and `startBuildAgglomerateGraphJob` API helper.
- Result links now redirect `compute_segment_index_file`, `build_agglomerate_graph`, and `ingest_large_dataset` jobs to the dataset view on success.

### Migration
- On-prem operators must deploy the new `mesh-service`, `segment-index-service`, and `agglomerate-service` containers (entries added to `tools/hosting/docker-compose.yml`) and ensure the registered worker's `supportedJobCommands` includes `compute_mesh_file`, `compute_segment_index_file`, and `build_agglomerate_graph`. `tools/postgres/dbtool.js enable-jobs` already registers all three for local dev.
