# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "httpx",
#   "torch",
#   "tifffile",
#   "numpy",
# ]
# ///

"""Train worker.

Polls WEBKNOSSOS for `train_glia_model` jobs.
Loads training data, trains 3D U-Net, saves checkpoint.
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
import tifffile
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader

from model.unet3d import UNet3D

logger = logging.getLogger(__name__)

WK_API_VERSION = 12
POLL_ERROR_BACKOFF = 5.0

@dataclass
class TrainJob:
    id: str
    organization_id: str
    training_data_dir: str
    output_model_path: str
    epochs: int

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "TrainJob":
        args = payload["job_kwargs"]
        return cls(
            id=payload["job_id"],
            organization_id=args["organization_id"],
            training_data_dir=args["training_data_dir"],
            output_model_path=args["output_model_path"],
            epochs=int(args.get("epochs", 10)),
        )

class GliaDataset(Dataset):
    def __init__(self, data_dir):
        self.raw_dir = Path(data_dir) / "raw"
        self.labels_dir = Path(data_dir) / "labels"
        self.files = list(self.raw_dir.glob("*.tif"))
        
    def __len__(self):
        return len(self.files)

    def __getitem__(self, idx):
        raw_path = self.files[idx]
        label_path = self.labels_dir / raw_path.name.replace("_raw.tif", "_label.tif")
        
        raw_vol = tifffile.imread(raw_path).astype(np.float32) / 255.0
        label_vol = tifffile.imread(label_path).astype(np.float32)
        
        # Binary segmentation
        label_vol = (label_vol > 0).astype(np.float32)
        
        # Add channel dim: (C, Z, Y, X)
        return torch.from_numpy(raw_vol).unsqueeze(0), torch.from_numpy(label_vol).unsqueeze(0)

def main() -> None:
    setup_logging()
    args = parse_args()
    check_env_vars()
    logger.info(f"Polling {args.wk_uri} for train_glia_model jobs")
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
    job = TrainJob.from_api(job_payload)
    logger.info(f"Claimed job {job.id}")
    report_status(args, job.id, "STARTED")
    try:
        run_train(args, job)
        report_status(args, job.id, "SUCCESS", return_value=job.output_model_path)
        logger.info(f"Job {job.id} finished OK")
    except Exception as e:
        logger.exception(f"Job {job.id} failed")
        report_status(args, job.id, "FAILURE", return_value=str(e))

def request_next_job(args: argparse.Namespace) -> dict[str, Any] | None:
    key = os.environ["WORKER_KEY"]
    response = httpx.get(
        f"{args.wk_uri}/api/v{WK_API_VERSION}/jobs/request",
        params={"key": key, "workerVersion": "train-glia-worker-0.1"},
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
        if j.get("command") == "train_glia_model":
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

def run_train(args: argparse.Namespace, job: TrainJob) -> None:
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info(f"Training on device: {device}")

    dataset = GliaDataset(job.training_data_dir)
    if len(dataset) == 0:
        raise ValueError(f"No training data found in {job.training_data_dir}")
        
    loader = DataLoader(dataset, batch_size=1, shuffle=True)
    
    model = UNet3D(in_channels=1, out_channels=1).to(device)
    criterion = nn.BCEWithLogitsLoss()
    optimizer = optim.Adam(model.parameters(), lr=1e-3)
    
    model.train()
    for epoch in range(job.epochs):
        epoch_loss = 0.0
        for i, (inputs, targets) in enumerate(loader):
            inputs, targets = inputs.to(device), targets.to(device)
            
            optimizer.zero_grad()
            outputs = model(inputs)
            loss = criterion(outputs, targets)
            loss.backward()
            optimizer.step()
            
            epoch_loss += loss.item()
            
        avg_loss = epoch_loss / len(loader)
        logger.info(f"Epoch {epoch+1}/{job.epochs}, Loss: {avg_loss:.4f}")
        report_status(args, job.id, "STARTED", return_value=f"Epoch {epoch+1}/{job.epochs}, Loss: {avg_loss:.4f}")

    out_path = Path(job.output_model_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), out_path)
    logger.info(f"Model saved to {out_path}")

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
