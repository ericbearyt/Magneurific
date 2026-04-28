# Glia Segmentation Pipeline Workers

This directory contains the custom worker scripts for the glia segmentation pipeline.

## Overview

The pipeline consists of 6 stages. Stages 5 and 6 use existing WebKnossos features. Stages 1-4 use the scripts in this directory.

1. **`1_export_exemplars.py`**: CLI script to export manual annotations from WebKnossos to TIFF training data.
2. **`2_train_worker.py`**: Background worker that polls for `train_glia_model` jobs and trains a 3D U-Net.
3. **`3_infer_worker.py`**: Background worker that polls for `infer_glia` jobs and runs the trained model on EM volume chunks.
4. **`4_push_worker.py`**: Background worker that polls for `push_glia_segmentation` jobs, runs connected components, and uploads the results as a new WebKnossos layer.

## Setup

```bash
cd tools/glia-pipeline
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Running the Workers

You must have a `WORKER_KEY` configured in WebKnossos and passed as an environment variable.

```bash
export WORKER_KEY="your-worker-key"

# Run a worker in the background (e.g. using nohup or systemd)
python 2_train_worker.py --wk-uri http://localhost:9000
python 3_infer_worker.py --wk-uri http://localhost:9000
python 4_push_worker.py --wk-uri http://localhost:9000
```
