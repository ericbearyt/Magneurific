# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "httpx",
#   "webknossos",
# ]
# ///

"""Orchestrator service.

Polls WEBKNOSSOS for `batch_skeletonize` jobs.
For each job, it uses the WK python library to load the dataset layer,
find all unique segment IDs, and dispatches individual `skeletonize_segmentation` jobs
for them. It then tracks progress and updates the main job.

Environment variables:
  WORKER_KEY  - key registered for this worker in WEBKNOSSOS
  BINARY_DATA_DIR - local path where dataset binaryData is mounted (default /webknossos/binaryData)
"""

import argparse
import logging
import os
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import numpy as np

import httpx
import webknossos as wk

logger = logging.getLogger(__name__)

WK_API_VERSION = 12
POLL_ERROR_BACKOFF = 5.0

@dataclass
class BatchSkeletonizeJob:
    id: str
    organization_id: str
    dataset_name: str
    dataset_id: str
    dataset_directory_name: str
    layer_name: str
    mag: str
    user_auth_token: str

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "BatchSkeletonizeJob":
        args = payload["job_kwargs"]
        return cls(
            id=payload["job_id"],
            organization_id=args["organization_id"],
            dataset_name=args["dataset_name"],
            dataset_id=args["dataset_id"],
            dataset_directory_name=args["dataset_directory_name"],
            layer_name=args["layer_name"],
            mag=args["mag"],
            user_auth_token=args.get("user_auth_token", ""),
        )

def main() -> None:
    setup_logging()
    logger.info("Starting orchestrator service")
    args = parse_args()
    check_env_vars()
    logger.info(
        f"Polling {args.wk_uri} every {args.polling_interval_seconds}s for batch_skeletonize jobs"
    )
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
    job = BatchSkeletonizeJob.from_api(job_payload)
    logger.info(f"Claimed job {job.id} (dataset={job.dataset_name}, layer={job.layer_name})")
    report_status(args, job.id, "STARTED")
    try:
        run_orchestrator(args, job)
        report_status(args, job.id, "SUCCESS", return_value="100% complete")
        logger.info(f"Job {job.id} finished OK")
    except Exception as e:
        logger.exception(f"Job {job.id} failed")
        report_status(args, job.id, "FAILURE", return_value=str(e))

def request_next_job(args: argparse.Namespace) -> dict[str, Any] | None:
    key = os.environ["WORKER_KEY"]
    response = httpx.get(
        f"{args.wk_uri}/api/v{WK_API_VERSION}/jobs/request",
        params={"key": key, "workerVersion": "orchestrator-service-0.1"},
        timeout=30.0,
    )
    if response.status_code == 204:
        return None
    response.raise_for_status()
    data = response.json()
    to_run = data.get("to_run") if isinstance(data, dict) else None
    if not to_run:
        return None
    for j in to_run:
        if j.get("command") == "batch_skeletonize":
            return j
    return None

def report_status(args: argparse.Namespace, job_id: str, state: str, *,
                  return_value: str | None = None) -> None:
    key = os.environ["WORKER_KEY"]
    body: dict[str, Any] = {"state": state}
    if return_value is not None:
        body["returnValue"] = return_value
    response = httpx.post(
        f"{args.wk_uri}/api/v{WK_API_VERSION}/jobs/{job_id}/status",
        params={"key": key},
        json=body,
        timeout=30.0,
    )
    response.raise_for_status()

def run_orchestrator(args: argparse.Namespace, job: BatchSkeletonizeJob) -> None:
    binary_data = Path(os.environ.get("BINARY_DATA_DIR", args.binary_data_dir))
    dataset_path = binary_data / job.organization_id / job.dataset_directory_name
    
    with wk.webknossos_context(url=args.wk_uri, token=job.user_auth_token):
        # Fallback to local dataset reading if wk api token isn't fully working in worker
        ds = wk.Dataset.open(dataset_path)
        layer = ds.get_layer(job.layer_name)
        mag = layer.get_mag(job.mag)
        
        # Read a subset or the whole volume to find unique IDs
        # For huge datasets, this should ideally use the segment index.
        # But for this worker, we'll read the bounding box
        bbox = mag.bounding_box
        logger.info(f"Reading volume for {job.layer_name} at {job.mag}...")
        
        # We read chunk by chunk to avoid OOM
        unique_ids = set()
        chunk_size = 512
        for z in range(0, bbox.size.z, chunk_size):
            for y in range(0, bbox.size.y, chunk_size):
                for x in range(0, bbox.size.x, chunk_size):
                    w = min(chunk_size, bbox.size.x - x)
                    h = min(chunk_size, bbox.size.y - y)
                    d = min(chunk_size, bbox.size.z - z)
                    
                    data = mag.read(
                        absolute_offset=(bbox.topleft.x + x, bbox.topleft.y + y, bbox.topleft.z + z),
                        size=(w, h, d)
                    )
                    unique_ids.update(np.unique(data))
                    
        # Remove 0 (background)
        unique_ids.discard(0)
        
        id_list = sorted(list(unique_ids))
        logger.info(f"Found {len(id_list)} unique segment IDs to skeletonize.")
        
        # Batch IDs into chunks of 10
        chunk_size_ids = 10
        id_chunks = [id_list[i:i + chunk_size_ids] for i in range(0, len(id_list), chunk_size_ids)]
        
        # Submit skeletonization jobs
        key = os.environ["WORKER_KEY"]
        for idx, chunk in enumerate(id_chunks):
            chunk_str = ",".join(map(str, chunk))
            
            # Use the run skeletonize job endpoint
            logger.info(f"Submitting skeletonize job for chunk {idx+1}/{len(id_chunks)}")
            
            # WebKnossos uses cookies for auth on the UI, but we can pass the auth token.
            # Here we POST to the frontend endpoint, but as a worker we might need to use the token
            run_url = f"{args.wk_uri}/api/jobs/run/skeletonizeSegmentation/{job.dataset_id}?layerName={job.layer_name}&mag={job.mag}&segmentIds={chunk_str}"
            response = httpx.post(
                run_url,
                headers={"X-Auth-Token": job.user_auth_token},
                timeout=30.0
            )
            
            # Progress update
            progress = f"{idx+1} / {len(id_chunks)} batches submitted"
            report_status(args, job.id, "STARTED", return_value=progress)
            time.sleep(1) # Small delay to not overwhelm the server

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wk-uri", default="http://localhost:9000",
                        help="Base URI of the WEBKNOSSOS instance")
    parser.add_argument("--polling-interval-seconds", type=int, default=10,
                        help="Seconds between polls")
    parser.add_argument("--binary-data-dir", default="/webknossos/binaryData",
                        help="Path where WK binaryData is mounted")
    return parser.parse_args()

def check_env_vars() -> None:
    if not os.environ.get("WORKER_KEY"):
        raise KeyError("WORKER_KEY environment variable must be set")

def setup_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

if __name__ == "__main__":
    main()
