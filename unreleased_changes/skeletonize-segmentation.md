### Added
- Added a new `skeletonize_segmentation` long-running job that runs [kimimaro](https://github.com/seung-lab/kimimaro) (TEASAR) on a segmentation layer to produce skeletons for selected segment IDs. Results are written as per-segment `.swc` files alongside dataset mesh output and uploaded as an NML annotation. Triggerable from the Segments tab context menu ("Skeletonize Segments"). Requires the new `skeletonization-service` worker (see `tools/skeletonization-service/`).

### Migration
- Operators running on-prem deployments must deploy the new `skeletonization-service` alongside their existing workers and register a worker whose `supportedJobCommands` includes `skeletonize_segmentation`. See `tools/hosting/docker-compose.yml` and `tools/postgres/dbtool.js` (`enable-jobs` command, updated).
