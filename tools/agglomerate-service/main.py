# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "httpx",
#   "h5py",
#   "numpy",
# ]
# ///

"""Agglomerate graph builder service.

Polls WEBKNOSSOS for `build_agglomerate_graph` jobs. Reads the requested
segmentation layer, scans for unique segment ids, and writes a WK-compatible
HDF5 agglomerate file at:

    binaryData/<org>/<datasetDir>/<layer>/agglomerates/<agglomerateName>.hdf5

Default mapping is identity (each segment -> its own agglomerate). External
graph builders (FFN, GASP, etc.) can later overwrite the same file with a real
clustering — only the three datasets below need to be valid for the WK
proofreading UI to mount the file:

    /segment_to_agglomerate            shape: (max_seg_id + 1,)  uint64
    /agglomerate_to_segments_offsets   shape: (n_agglom + 1,)    uint64  (CSR-style offsets)
    /agglomerate_to_segments           shape: (n_segments,)      uint64

Environment variables:
  WORKER_KEY          worker key registered in WEBKNOSSOS
  BINARY_DATA_DIR     local path where dataset binaryData is mounted
"""

import argparse
import logging
import os
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import h5py
import numpy as np

logger = logging.getLogger(__name__)

WK_API_VERSION = 12
POLL_ERROR_BACKOFF = 5.0
WORKER_VERSION = "agglomerate-service-0.1"
JOB_COMMAND = "build_agglomerate_graph"


@dataclass
class AgglomerateJob:
    id: str
    organization_id: str
    dataset_name: str
    dataset_id: str
    dataset_directory_name: str
    layer_name: str
    mag: str
    agglomerate_name: str

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "AgglomerateJob":
        a = payload["job_kwargs"]
        return cls(
            id=payload["job_id"],
            organization_id=a["organization_id"],
            dataset_name=a["dataset_name"],
            dataset_id=a["dataset_id"],
            dataset_directory_name=a["dataset_directory_name"],
            layer_name=a["layer_name"],
            mag=a["mag"],
            agglomerate_name=a["agglomerate_name"],
        )


def main() -> None:
    setup_logging()
    args = parse_args()
    check_env_vars()
    logger.info(f"Polling {args.wk_uri} every {args.polling_interval_seconds}s for {JOB_COMMAND} jobs")
    while True:
        try:
            poll_once(args)
        except Exception as e:
            logger.error(f"Polling error: {e}")
            logger.error(traceback.format_exc())
            time.sleep(POLL_ERROR_BACKOFF)
        time.sleep(args.polling_interval_seconds)


def poll_once(args: argparse.Namespace) -> None:
    job_payload = request_next_job(args)
    if job_payload is None:
        return
    job = AgglomerateJob.from_api(job_payload)
    logger.info(f"Claimed job {job.id} (layer={job.layer_name}, name={job.agglomerate_name})")
    report_status(args, job.id, "STARTED")
    try:
        out = run_build_agglomerate(args, job)
        report_status(args, job.id, "SUCCESS", return_value=str(out))
        logger.info(f"Job {job.id} finished OK -> {out}")
    except Exception as e:
        logger.exception(f"Job {job.id} failed")
        report_status(args, job.id, "FAILURE", return_value=str(e))


def request_next_job(args: argparse.Namespace) -> dict[str, Any] | None:
    key = os.environ["WORKER_KEY"]
    r = httpx.get(
        f"{args.wk_uri}/api/v{WK_API_VERSION}/jobs/request",
        params={"key": key, "workerVersion": WORKER_VERSION},
        timeout=30.0,
    )
    if r.status_code == 204:
        return None
    r.raise_for_status()
    data = r.json()
    to_run = data.get("to_run") if isinstance(data, dict) else None
    if not to_run:
        return None
    for j in to_run:
        if j.get("command") == JOB_COMMAND:
            return j
    return None


def report_status(args: argparse.Namespace, job_id: str, state: str, *, return_value: str | None = None) -> None:
    key = os.environ["WORKER_KEY"]
    body: dict[str, Any] = {"state": state}
    if return_value is not None:
        body["returnValue"] = return_value
    r = httpx.post(
        f"{args.wk_uri}/api/v{WK_API_VERSION}/jobs/{job_id}/status",
        params={"key": key},
        json=body,
        timeout=30.0,
    )
    r.raise_for_status()


def run_build_agglomerate(args: argparse.Namespace, job: AgglomerateJob) -> Path:
    binary_data = Path(os.environ.get("BINARY_DATA_DIR", args.binary_data_dir))
    layer_dir = binary_data / job.organization_id / job.dataset_directory_name / job.layer_name
    if not layer_dir.exists():
        raise FileNotFoundError(f"Layer directory not found: {layer_dir}")

    # Discover unique segment ids by scanning the highest-resolution mag chunks of the layer.
    # Works for zarr/zarr3/wkw layouts: every segmentation file under the mag dir holds chunk data.
    mag_dir = layer_dir / job.mag
    if not mag_dir.exists():
        raise FileNotFoundError(f"Mag directory not found: {mag_dir}")

    segment_ids = scan_unique_segment_ids(mag_dir)
    if len(segment_ids) == 0:
        raise RuntimeError("No non-zero segment ids found in layer")
    max_seg_id = int(segment_ids.max())
    n_segments = len(segment_ids)
    logger.info(f"Found {n_segments} unique segments, max id {max_seg_id}")

    # Identity mapping: agglomerate i contains exactly segment_ids[i].
    segment_to_agglomerate = np.zeros(max_seg_id + 1, dtype=np.uint64)
    for agglom_idx, seg_id in enumerate(segment_ids):
        segment_to_agglomerate[int(seg_id)] = np.uint64(agglom_idx)
    agglomerate_to_segments = segment_ids.astype(np.uint64)
    agglomerate_to_segments_offsets = np.arange(n_segments + 1, dtype=np.uint64)

    out_dir = layer_dir / "agglomerates"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{job.agglomerate_name}.hdf5"

    with h5py.File(out_path, "w") as f:
        f.create_dataset("segment_to_agglomerate", data=segment_to_agglomerate, compression="gzip")
        f.create_dataset("agglomerate_to_segments", data=agglomerate_to_segments, compression="gzip")
        f.create_dataset("agglomerate_to_segments_offsets", data=agglomerate_to_segments_offsets, compression="gzip")

    logger.info(f"Wrote agglomerate file: {out_path} ({out_path.stat().st_size:,} bytes)")
    return out_path


def scan_unique_segment_ids(mag_dir: Path) -> np.ndarray:
    """Iterate every regular file under mag_dir, attempt to interpret it as raw segment data,
    and union all unique non-zero values found.

    Heuristic: try uint32 first (most common), fall back to uint64. Reject other dtypes by
    skipping files whose size doesn't divide evenly. This works for raw chunked storage
    (zarr/wkw shards). For uncompressed chunks only — compressed chunks (gzip/blosc) are
    skipped with a warning.
    """
    unique: set[int] = set()
    skipped = 0
    for path in mag_dir.rglob("*"):
        if not path.is_file():
            continue
        size = path.stat().st_size
        try:
            buf = path.read_bytes()
        except OSError:
            skipped += 1
            continue
        # Heuristic: try uint32 if size divisible by 4, else uint64 if divisible by 8.
        if size % 4 == 0:
            try:
                arr = np.frombuffer(buf, dtype=np.uint32)
                unique.update(int(v) for v in np.unique(arr) if v != 0)
                continue
            except ValueError:
                pass
        if size % 8 == 0:
            try:
                arr = np.frombuffer(buf, dtype=np.uint64)
                unique.update(int(v) for v in np.unique(arr) if v != 0)
                continue
            except ValueError:
                pass
        skipped += 1
    if skipped:
        logger.warning(f"Skipped {skipped} files in {mag_dir} (unrecognized chunk format)")
    return np.array(sorted(unique), dtype=np.uint64)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--wk-uri", default="http://localhost:9000")
    p.add_argument("--polling-interval-seconds", type=int, default=10)
    p.add_argument("--binary-data-dir", default="/webknossos/binaryData")
    return p.parse_args()


def check_env_vars() -> None:
    if not os.environ.get("WORKER_KEY"):
        raise KeyError("WORKER_KEY environment variable must be set")


def setup_logging() -> None:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(message)s"))
    logger.addHandler(h)
    logger.setLevel(logging.INFO)


if __name__ == "__main__":
    main()
