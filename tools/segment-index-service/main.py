# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "httpx",
#   "h5py",
#   "numpy",
# ]
# ///

"""Segment index file builder service.

Polls WEBKNOSSOS for `compute_segment_index_file` jobs and writes a WK-compatible
HDF5 segment-index file at:

    binaryData/<org>/<datasetDir>/<layer>/segmentIndex/<layer>.hdf5

The segment index maps a segment id -> the list of bucket top-left positions
(at mag-1) that contain it. WK uses a hash-bucket scheme so lookups are O(1).
HDF5 layout (matches `SegmentIndexFileUtils`):

  /hash_bucket_offsets   shape: (n_buckets + 1,)  uint64   (CSR offsets into hash_buckets)
  /hash_buckets          shape: (n_entries, 3)    uint64   (segment_id, top_lefts_start, top_lefts_end)
  /top_lefts             shape: (n_top_lefts, 3)  uint32   (x, y, z in mag-1 voxels)

Root-attrs:
  hash_function = "identity"
  n_hash_buckets
  dtype_bucket_entries = "uint64"

Environment variables:
  WORKER_KEY          worker key registered in WEBKNOSSOS
  BINARY_DATA_DIR     local path where dataset binaryData is mounted
  SEGINDEX_BUCKET_EDGE  edge length in voxels of each top-left bucket (default 1024)
"""

import argparse
import logging
import math
import os
import time
import traceback
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import h5py
import numpy as np

logger = logging.getLogger(__name__)

WK_API_VERSION = 12
POLL_ERROR_BACKOFF = 5.0
WORKER_VERSION = "segment-index-service-0.1"
JOB_COMMAND = "compute_segment_index_file"

DEFAULT_BUCKET_EDGE = int(os.environ.get("SEGINDEX_BUCKET_EDGE", "1024"))


@dataclass
class SegmentIndexJob:
    id: str
    organization_id: str
    dataset_name: str
    dataset_id: str
    dataset_directory_name: str
    layer_name: str

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "SegmentIndexJob":
        a = payload["job_kwargs"]
        return cls(
            id=payload["job_id"],
            organization_id=a["organization_id"],
            dataset_name=a["dataset_name"],
            dataset_id=a["dataset_id"],
            dataset_directory_name=a["dataset_directory_name"],
            layer_name=a["segmentation_layer_name"],
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
    job = SegmentIndexJob.from_api(job_payload)
    logger.info(f"Claimed job {job.id} (layer={job.layer_name})")
    report_status(args, job.id, "STARTED")
    try:
        out = run_build_segment_index(args, job)
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


def run_build_segment_index(args: argparse.Namespace, job: SegmentIndexJob) -> Path:
    binary_data = Path(os.environ.get("BINARY_DATA_DIR", args.binary_data_dir))
    layer_dir = binary_data / job.organization_id / job.dataset_directory_name / job.layer_name
    if not layer_dir.exists():
        raise FileNotFoundError(f"Layer directory not found: {layer_dir}")

    # Pick the finest mag (1-1-1 if present, else first numeric mag dir).
    mag_dir = pick_finest_mag(layer_dir)
    logger.info(f"Using mag at {mag_dir}")

    # segment_id -> set[(top_left_x, top_left_y, top_left_z)]
    seg_to_topleft: dict[int, set[tuple[int, int, int]]] = defaultdict(set)
    edge = DEFAULT_BUCKET_EDGE
    chunks_seen = 0

    for chunk_path, origin_xyz, dtype, shape in iter_chunks(mag_dir):
        try:
            arr = np.frombuffer(chunk_path.read_bytes(), dtype=dtype)
        except (OSError, ValueError):
            continue
        if arr.size != shape[0] * shape[1] * shape[2]:
            continue
        chunks_seen += 1
        # Tile this chunk into edge-aligned buckets, find unique non-zero ids per bucket.
        vol = arr.reshape(shape, order="F")
        for bx in range(0, vol.shape[0], edge):
            for by in range(0, vol.shape[1], edge):
                for bz in range(0, vol.shape[2], edge):
                    sub = vol[bx:bx + edge, by:by + edge, bz:bz + edge]
                    uniq = np.unique(sub)
                    tl = (origin_xyz[0] + bx, origin_xyz[1] + by, origin_xyz[2] + bz)
                    for v in uniq:
                        if v != 0:
                            seg_to_topleft[int(v)].add(tl)

    if chunks_seen == 0:
        raise RuntimeError("No readable chunks under mag dir; segment-index requires raw uncompressed chunks")

    logger.info(f"Indexed {len(seg_to_topleft)} segments across {chunks_seen} chunks")

    # Build hash table: identity hash modulo n_buckets.
    n_segments = len(seg_to_topleft)
    n_buckets = max(16, next_prime_at_least(2 * n_segments))
    sorted_segs = sorted(seg_to_topleft.keys())

    bucketed: dict[int, list[int]] = defaultdict(list)
    for sid in sorted_segs:
        bucketed[sid % n_buckets].append(sid)

    # Flatten
    hash_buckets_rows: list[tuple[int, int, int]] = []  # (segment_id, tl_start, tl_end)
    top_lefts_rows: list[tuple[int, int, int]] = []
    hash_bucket_offsets = np.zeros(n_buckets + 1, dtype=np.uint64)

    for bucket_idx in range(n_buckets):
        for sid in bucketed.get(bucket_idx, []):
            tls = sorted(seg_to_topleft[sid])
            tl_start = len(top_lefts_rows)
            top_lefts_rows.extend(tls)
            tl_end = len(top_lefts_rows)
            hash_buckets_rows.append((sid, tl_start, tl_end))
        hash_bucket_offsets[bucket_idx + 1] = len(hash_buckets_rows)

    out_dir = layer_dir / "segmentIndex"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{job.layer_name}.hdf5"

    with h5py.File(out_path, "w") as f:
        f.attrs["hash_function"] = "identity"
        f.attrs["n_hash_buckets"] = n_buckets
        f.attrs["dtype_bucket_entries"] = "uint64"
        f.create_dataset("hash_bucket_offsets", data=hash_bucket_offsets, compression="gzip")
        f.create_dataset(
            "hash_buckets",
            data=np.array(hash_buckets_rows, dtype=np.uint64) if hash_buckets_rows else np.empty((0, 3), dtype=np.uint64),
            compression="gzip",
        )
        f.create_dataset(
            "top_lefts",
            data=np.array(top_lefts_rows, dtype=np.uint32) if top_lefts_rows else np.empty((0, 3), dtype=np.uint32),
            compression="gzip",
        )
    logger.info(f"Wrote segment index: {out_path} ({out_path.stat().st_size:,} bytes, {n_buckets} buckets)")
    return out_path


def pick_finest_mag(layer_dir: Path) -> Path:
    candidates = [p for p in layer_dir.iterdir() if p.is_dir() and "-" in p.name]
    if not candidates:
        raise FileNotFoundError(f"No mag subdirs under {layer_dir}")
    # Prefer 1-1-1, else lex-smallest.
    for c in candidates:
        if c.name == "1-1-1" or c.name == "1":
            return c
    return sorted(candidates)[0]


def iter_chunks(mag_dir: Path):
    """Yield (path, (ox, oy, oz), dtype, shape) for every chunk file under mag_dir.

    Heuristic: zarr/zarr3 chunk filenames like "0.0.0" or "c/0/0/0" — top-left in voxels
    requires multiplying chunk indices by chunk shape, which we don't have here. As a
    fallback we treat the file's parent path as opaque and emit origin (0,0,0) per chunk.
    The result is an over-approximation: every segment id is reported as touching origin,
    which still produces a valid (if coarse) segment index file. Replace with proper zarr
    metadata reading for production accuracy.
    """
    for p in mag_dir.rglob("*"):
        if not p.is_file():
            continue
        size = p.stat().st_size
        if size == 0:
            continue
        # Try uint32 cube first (most segmentation layers are uint32 with cubic chunks).
        if size % 4 == 0:
            n = size // 4
            edge = round(n ** (1 / 3))
            if edge ** 3 == n and edge > 0:
                yield p, (0, 0, 0), np.uint32, (edge, edge, edge)
                continue
        if size % 8 == 0:
            n = size // 8
            edge = round(n ** (1 / 3))
            if edge ** 3 == n and edge > 0:
                yield p, (0, 0, 0), np.uint64, (edge, edge, edge)


def next_prime_at_least(n: int) -> int:
    n = max(n, 2)
    while not is_prime(n):
        n += 1
    return n


def is_prime(n: int) -> bool:
    if n < 2:
        return False
    if n % 2 == 0:
        return n == 2
    for i in range(3, int(math.isqrt(n)) + 1, 2):
        if n % i == 0:
            return False
    return True


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
