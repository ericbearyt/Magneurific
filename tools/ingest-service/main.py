# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "httpx",
#   "webknossos",
# ]
# ///

"""Ingest service.

Polls WEBKNOSSOS for `ingest_large_dataset` jobs, reads the requested
source path (TIFF stack), converts it to a Zarr dataset, and emits
the results into the `binaryData/` directory.

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
import re

import httpx
import webknossos as wk

logger = logging.getLogger(__name__)

WK_API_VERSION = 12
POLL_ERROR_BACKOFF = 5.0

@dataclass
class IngestJob:
    id: str
    organization_id: str
    dataset_name: str
    dataset_id: str
    dataset_directory_name: str
    source_path: str

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "IngestJob":
        args = payload["job_kwargs"]
        return cls(
            id=payload["job_id"],
            organization_id=args["organization_id"],
            dataset_name=args["dataset_name"],
            dataset_id=args["dataset_id"],
            dataset_directory_name=args["dataset_directory_name"],
            source_path=args["source_path"],
        )

def main() -> None:
    setup_logging()
    logger.info("Starting ingest service")
    args = parse_args()
    check_env_vars()
    logger.info(
        f"Polling {args.wk_uri} every {args.polling_interval_seconds}s for ingest_large_dataset jobs"
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
    job = IngestJob.from_api(job_payload)
    logger.info(f"Claimed job {job.id} (dataset={job.dataset_name}, source={job.source_path})")
    report_status(args, job.id, "STARTED")
    try:
        run_ingest(args, job)
        report_status(args, job.id, "SUCCESS", return_value="Ingest complete")
        logger.info(f"Job {job.id} finished OK")
    except Exception as e:
        logger.exception(f"Job {job.id} failed")
        report_status(args, job.id, "FAILURE", return_value=str(e))

def request_next_job(args: argparse.Namespace) -> dict[str, Any] | None:
    key = os.environ["WORKER_KEY"]
    response = httpx.get(
        f"{args.wk_uri}/api/v{WK_API_VERSION}/jobs/request",
        params={"key": key, "workerVersion": "ingest-service-0.1"},
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
        if j.get("command") == "ingest_large_dataset":
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

def natural_sort_key(s):
    return [int(text) if text.isdigit() else text.lower()
            for text in re.split('([0-9]+)', str(s))]

def run_ingest(args: argparse.Namespace, job: IngestJob) -> None:
    binary_data = Path(os.environ.get("BINARY_DATA_DIR", args.binary_data_dir))
    output_dir = binary_data / job.organization_id / job.dataset_directory_name
    output_dir.mkdir(parents=True, exist_ok=True)
    
    input_dir = Path(job.source_path)
    if not input_dir.exists() or not input_dir.is_dir():
        raise FileNotFoundError(f"Source path {input_dir} is not a valid directory.")
        
    files = sorted([f for f in input_dir.glob("*.tiff")], key=natural_sort_key)
    if not files:
        raise FileNotFoundError(f"No TIFF files found in {input_dir}")
        
    # By default, checking if it's segmentation or color based on name heuristics
    layer_category = wk.SEGMENTATION_CATEGORY if "synapse" in job.dataset_name.lower() or "neuron" in job.dataset_name.lower() else wk.COLOR_CATEGORY
        
    logger.info(f"Converting {len(files)} files to {output_dir}")
    dataset = wk.Dataset.from_images(
        input_path=input_dir,
        output_path=output_dir,
        voxel_size=(4, 4, 40),
        name=job.dataset_name,
        layer_category=layer_category,
        compress=True,
        data_format="zarr"
    )
    
    logger.info(f"Downsampling dataset...")
    dataset.downsample()
    logger.info(f"Dataset downsampled successfully.")

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
