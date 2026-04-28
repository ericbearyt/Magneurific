# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "httpx",
#   "torch",
#   "numpy",
#   "zarr",
# ]
# ///

"""Infer worker.

Polls WEBKNOSSOS for `infer_glia` jobs.
Downloads chunk, runs 3D U-Net, saves Zarr.
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
import torch
import zarr

from model.unet3d import UNet3D

logger = logging.getLogger(__name__)

WK_API_VERSION = 12
POLL_ERROR_BACKOFF = 5.0

@dataclass
class InferJob:
    id: str
    organization_id: str
    dataset_name: str
    dataset_directory_name: str
    layer_name: str
    model_path: str
    bounding_box: tuple[int, int, int, int, int, int]
    chunk_size: tuple[int, int, int]
    output_zarr_path: str
    user_auth_token: str

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "InferJob":
        args = payload["job_kwargs"]
        bbox_raw = args["bounding_box"]
        bbox = tuple(int(v) for v in bbox_raw.split(","))
        
        csize_raw = args.get("chunk_size", "64,64,64")
        csize = tuple(int(v) for v in csize_raw.split(","))

        return cls(
            id=payload["job_id"],
            organization_id=args["organization_id"],
            dataset_name=args["dataset_name"],
            dataset_directory_name=args["dataset_directory_name"],
            layer_name=args["layer_name"],
            model_path=args["model_path"],
            bounding_box=bbox,
            chunk_size=csize,
            output_zarr_path=args["output_zarr_path"],
            user_auth_token=args.get("user_auth_token", ""),
        )

def main() -> None:
    setup_logging()
    args = parse_args()
    check_env_vars()
    logger.info(f"Polling {args.wk_uri} for infer_glia jobs")
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
    job = InferJob.from_api(job_payload)
    logger.info(f"Claimed job {job.id}")
    report_status(args, job.id, "STARTED")
    try:
        run_infer(args, job)
        report_status(args, job.id, "SUCCESS", return_value=job.output_zarr_path)
        logger.info(f"Job {job.id} finished OK")
    except Exception as e:
        logger.exception(f"Job {job.id} failed")
        report_status(args, job.id, "FAILURE", return_value=str(e))

def request_next_job(args: argparse.Namespace) -> dict[str, Any] | None:
    key = os.environ["WORKER_KEY"]
    response = httpx.get(
        f"{args.wk_uri}/api/v{WK_API_VERSION}/jobs/request",
        params={"key": key, "workerVersion": "infer-glia-worker-0.1"},
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
        if j.get("command") == "infer_glia":
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

def run_infer(args: argparse.Namespace, job: InferJob) -> None:
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info(f"Inferencing on device: {device}")

    model = UNet3D(in_channels=1, out_channels=1).to(device)
    model.load_state_dict(torch.load(job.model_path, map_location=device))
    model.eval()

    x, y, z, w, h, d = job.bounding_box
    out_path = Path(job.output_zarr_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Create output zarr
    z_out = zarr.open(str(out_path), mode='w', shape=(w, h, d), chunks=job.chunk_size, dtype='uint8')

    cw, ch, cd = job.chunk_size
    total_chunks = ((w + cw - 1) // cw) * ((h + ch - 1) // ch) * ((d + cd - 1) // cd)
    chunk_idx = 0

    # Sliding window inference
    with torch.no_grad():
        for z0 in range(0, d, cd):
            for y0 in range(0, h, ch):
                for x0 in range(0, w, cw):
                    chunk_idx += 1
                    report_status(args, job.id, "STARTED", return_value=f"Chunk {chunk_idx}/{total_chunks}")
                    
                    z1 = min(z0 + cd, d)
                    y1 = min(y0 + ch, h)
                    x1 = min(x0 + cw, w)
                    
                    cw_actual = x1 - x0
                    ch_actual = y1 - y0
                    cd_actual = z1 - z0
                    
                    # Fetch raw EM
                    url = f"{args.wk_uri}/data/v9/datasets/{job.organization_id}/{job.dataset_directory_name}/layers/{job.layer_name}/data"
                    params = {
                        "x": x + x0, "y": y + y0, "z": z + z0,
                        "width": cw_actual, "height": ch_actual, "depth": cd_actual,
                        "mag": "1-1-1",
                        "token": job.user_auth_token,
                    }
                    r = httpx.get(url, params=params, timeout=120.0)
                    r.raise_for_status()
                    
                    raw_chunk = np.frombuffer(r.content, dtype=np.uint8).reshape((cw_actual, ch_actual, cd_actual), order="F")
                    
                    # Pad to chunk_size if needed to match model input requirements or just run dynamically if model supports it
                    # U-Net needs shapes to be multiples of 16 (2^4 downsamples)
                    
                    # Transpose to (C, Z, Y, X) for model
                    input_tensor = torch.from_numpy(raw_chunk.astype(np.float32) / 255.0).transpose(0, 2).unsqueeze(0).unsqueeze(0).to(device)
                    
                    # Simple padding to avoid dimension errors
                    pz = (16 - input_tensor.shape[2] % 16) % 16
                    py = (16 - input_tensor.shape[3] % 16) % 16
                    px = (16 - input_tensor.shape[4] % 16) % 16
                    if pz > 0 or py > 0 or px > 0:
                        input_tensor = torch.nn.functional.pad(input_tensor, (0, px, 0, py, 0, pz))
                    
                    output_tensor = model(input_tensor)
                    prob = torch.sigmoid(output_tensor).squeeze().cpu().numpy()
                    
                    # unpad
                    if pz > 0 or py > 0 or px > 0:
                        prob = prob[:-pz if pz > 0 else None, :-py if py > 0 else None, :-px if px > 0 else None]
                    
                    # Transpose back to (X, Y, Z)
                    prob = np.transpose(prob, (2, 1, 0))
                    
                    # Binary mask
                    mask = (prob > 0.5).astype(np.uint8)
                    
                    z_out[x0:x1, y0:y1, z0:z1] = mask

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
