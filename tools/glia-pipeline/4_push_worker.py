# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "httpx",
#   "webknossos",
#   "scipy",
#   "zarr",
#   "numpy",
# ]
# ///

"""Push worker.

Polls WEBKNOSSOS for `push_glia_segmentation` jobs.
Reads the Zarr produced by Worker 3, runs connected components,
and uploads to WK as a new layer.
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
import numpy as np
import webknossos as wk
import zarr
from scipy.ndimage import label

logger = logging.getLogger(__name__)

WK_API_VERSION = 12
POLL_ERROR_BACKOFF = 5.0

@dataclass
class PushJob:
    id: str
    organization_id: str
    dataset_name: str
    dataset_id: str
    dataset_directory_name: str
    segmentation_zarr_path: str
    new_layer_name: str
    user_auth_token: str

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "PushJob":
        args = payload["job_kwargs"]
        return cls(
            id=payload["job_id"],
            organization_id=args["organization_id"],
            dataset_name=args["dataset_name"],
            dataset_id=args["dataset_id"],
            dataset_directory_name=args["dataset_directory_name"],
            segmentation_zarr_path=args["segmentation_zarr_path"],
            new_layer_name=args["new_layer_name"],
            user_auth_token=args.get("user_auth_token", ""),
        )

def main() -> None:
    setup_logging()
    args = parse_args()
    check_env_vars()
    logger.info(f"Polling {args.wk_uri} for push_glia_segmentation jobs")
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
    job = PushJob.from_api(job_payload)
    logger.info(f"Claimed job {job.id}")
    report_status(args, job.id, "STARTED")
    try:
        run_push(args, job)
        result_link = f"{args.wk_uri}/datasets/{job.organization_id}/{job.dataset_name}"
        report_status(args, job.id, "SUCCESS", return_value=result_link)
        logger.info(f"Job {job.id} finished OK")
    except Exception as e:
        logger.exception(f"Job {job.id} failed")
        report_status(args, job.id, "FAILURE", return_value=str(e))

def request_next_job(args: argparse.Namespace) -> dict[str, Any] | None:
    key = os.environ["WORKER_KEY"]
    response = httpx.get(
        f"{args.wk_uri}/api/v{WK_API_VERSION}/jobs/request",
        params={"key": key, "workerVersion": "push-glia-worker-0.1"},
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
        if j.get("command") == "push_glia_segmentation":
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

def run_push(args: argparse.Namespace, job: PushJob) -> None:
    logger.info(f"Loading inference results from {job.segmentation_zarr_path}")
    z_in = zarr.open(job.segmentation_zarr_path, mode='r')
    vol = np.array(z_in)
    
    logger.info("Running connected components...")
    # Connected components to generate unique segment IDs
    labeled_vol, num_features = label(vol)
    labeled_vol = labeled_vol.astype(np.uint32)
    logger.info(f"Found {num_features} unique segments")

    with wk.webknossos_context(url=args.wk_uri, token=job.user_auth_token):
        logger.info("Downloading dataset metadata...")
        dataset = wk.Dataset.download(job.dataset_name, organization_id=job.organization_id)
        
        logger.info(f"Creating new segmentation layer: {job.new_layer_name}")
        # Create new layer (defaulting to largest segment ID found + some padding for mag)
        new_layer = dataset.add_layer(
            job.new_layer_name,
            category=wk.LayerCategory.SEGMENTATION,
            dtype=np.uint32,
            largest_segment_id=num_features
        )
        
        # Add mag 1
        mag1 = new_layer.add_mag("1-1-1")
        mag1.write(absolute_offset=(0, 0, 0), data=labeled_vol)
        
        logger.info("Uploading layer to WebKnossos...")
        dataset.upload()
        
    logger.info("Upload complete!")

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wk-uri", default="http://localhost:9000", help="WebKnossos URI")
    parser.add_argument("--polling-interval-seconds", type=int, default=10)
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
