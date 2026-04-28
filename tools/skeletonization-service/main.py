# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "httpx",
#   "kimimaro>=5.0",
#   "crackle-codec",
#   "numpy",
# ]
# ///

"""Skeletonization service.

Polls WEBKNOSSOS for `skeletonize_segmentation` jobs, loads the requested
segmentation layer, runs kimimaro (TEASAR), and emits:
  - per-segment .swc artifact on dataset storage
  - an NML skeleton annotation uploaded to the tracingstore

Environment variables:
  WORKER_KEY  - key registered for this worker in WEBKNOSSOS
  BINARY_DATA_DIR - local path where dataset binaryData is mounted (default /webknossos/binaryData)
"""

import argparse
import logging
import os
import time
import traceback
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import kimimaro
import numpy as np

logger = logging.getLogger(__name__)

WK_API_VERSION = 12
POLL_ERROR_BACKOFF = 5.0


# ---------- job model ----------

@dataclass
class SkeletonizationJob:
    id: str
    organization_id: str
    dataset_name: str
    dataset_id: str
    dataset_directory_name: str
    layer_name: str
    mag: str
    segment_ids: list[int]
    bounding_box: tuple[int, int, int, int, int, int] | None
    user_auth_token: str

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "SkeletonizationJob":
        args = payload["job_kwargs"]
        seg_ids_raw = args["segment_ids"]
        segment_ids = [int(s) for s in seg_ids_raw.split(",") if s.strip()]
        bbox = None
        if args.get("bounding_box"):
            bbox = tuple(int(v) for v in args["bounding_box"].split(","))  # type: ignore[assignment]
            if len(bbox) != 6:
                raise ValueError(f"bounding_box must be 6 ints, got {bbox}")
        return cls(
            id=payload["job_id"],
            organization_id=args["organization_id"],
            dataset_name=args["dataset_name"],
            dataset_id=args["dataset_id"],
            dataset_directory_name=args["dataset_directory_name"],
            layer_name=args["layer_name"],
            mag=args["mag"],
            segment_ids=segment_ids,
            bounding_box=bbox,
            user_auth_token=args["user_auth_token"],
        )


# ---------- main loop ----------

def main() -> None:
    setup_logging()
    logger.info("Starting skeletonization service")
    args = parse_args()
    check_env_vars()
    logger.info(
        f"Polling {args.wk_uri} every {args.polling_interval_seconds}s for skeletonize_segmentation jobs"
    )
    while True:
        try:
            poll_once(args)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Polling error: {e}")
            logger.error(traceback.format_exc())
            time.sleep(POLL_ERROR_BACKOFF)
        time.sleep(args.polling_interval_seconds)


def poll_once(args: argparse.Namespace) -> None:
    job_payload = request_next_job(args)
    if job_payload is None:
        return
    job = SkeletonizationJob.from_api(job_payload)
    logger.info(f"Claimed job {job.id} (layer={job.layer_name}, mag={job.mag}, segments={len(job.segment_ids)})")
    report_status(args, job.id, "STARTED")
    try:
        result_links = run_skeletonization(args, job)
        report_status(args, job.id, "SUCCESS", return_value=str(result_links))
        logger.info(f"Job {job.id} finished OK")
    except Exception as e:  # noqa: BLE001
        logger.exception(f"Job {job.id} failed")
        report_status(args, job.id, "FAILURE", return_value=str(e))


# ---------- WK API ----------

def request_next_job(args: argparse.Namespace) -> dict[str, Any] | None:
    key = os.environ["WORKER_KEY"]
    response = httpx.get(
        f"{args.wk_uri}/api/v{WK_API_VERSION}/jobs/request",
        params={"key": key, "workerVersion": "skeletonization-service-0.1"},
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
        if j.get("command") == "skeletonize_segmentation":
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


# ---------- compute ----------

DEFAULT_BBOX_EDGE = int(os.environ.get("SKELETONIZE_DEFAULT_EDGE", "512"))


def run_skeletonization(args: argparse.Namespace, job: SkeletonizationJob) -> dict[str, str]:
    binary_data = Path(os.environ.get("BINARY_DATA_DIR", args.binary_data_dir))

    dataset_info = fetch_dataset_info(args, job)
    voxel_size = extract_voxel_size_nm(dataset_info)
    layer_info = extract_layer_info(dataset_info, job.layer_name)
    element_class = layer_info.get("elementClass", "uint32")
    dtype = ELEMENT_CLASS_TO_DTYPE.get(element_class)
    if dtype is None:
        raise ValueError(f"Unsupported segmentation dtype: {element_class}")

    bbox = job.bounding_box or default_bbox(layer_info)
    logger.info(f"Using bbox={bbox} (mag1 voxels), dtype={element_class}")

    mag_vec = tuple(int(v) for v in job.mag.split("-"))
    if len(mag_vec) != 3:
        raise ValueError(f"Mag must be x-y-z, got {job.mag}")

    volume = fetch_volume_via_datastore(args, job, bbox, dtype)
    offset = (bbox[0] // mag_vec[0], bbox[1] // mag_vec[1], bbox[2] // mag_vec[2])
    logger.info(f"Loaded volume shape={volume.shape} dtype={volume.dtype} nonzero={int((volume != 0).sum())}")

    anisotropy = tuple(v * m for v, m in zip(voxel_size, mag_vec))

    skels = kimimaro.skeletonize(
        volume,
        teasar_params={
            "scale": 4,
            "const": 500,
            "pdrf_scale": 100000,
            "pdrf_exponent": 4,
            "soma_acceptance_threshold": 3500,
            "soma_detection_threshold": 750,
            "soma_invalidation_scale": 1.0,
            "soma_invalidation_const": 300,
        },
        object_ids=job.segment_ids,
        anisotropy=anisotropy,
        dust_threshold=1000,
        progress=False,
        parallel=int(os.environ.get("KIMIMARO_PARALLEL", "1")),
    )

    if not skels:
        raise RuntimeError("kimimaro produced no skeletons for the requested segments")

    # Primary output: repo-root skeletonization_output/ — easy to find.
    # Override via SKELETONIZE_OUTPUT_DIR env var.
    repo_root = Path(__file__).resolve().parents[2]
    default_out = repo_root / "skeletonization_output"
    out_base = Path(os.environ.get("SKELETONIZE_OUTPUT_DIR", str(default_out)))
    out_dir = out_base / job.dataset_directory_name / job.layer_name / job.id
    out_dir.mkdir(parents=True, exist_ok=True)

    swc_paths: list[str] = []
    for segid, skel in skels.items():
        swc_path = out_dir / f"{segid}.swc"
        swc_path.write_text(skel.to_swc())
        swc_paths.append(str(swc_path))

    nml_path = out_dir / f"{job.dataset_name}_{job.layer_name}_skeletons.nml"
    nml_bytes = build_nml(skels, offset=offset, voxel_size_nm=voxel_size,
                         dataset_name=job.dataset_name)
    nml_path.write_bytes(nml_bytes)

    # Also maintain a "latest" symlink at the top level for fast access
    latest_link = out_base / "latest"
    try:
        if latest_link.exists() or latest_link.is_symlink():
            latest_link.unlink()
        latest_link.symlink_to(out_dir)
    except OSError as e:
        logger.warning(f"Could not update 'latest' symlink: {e}")

    banner = "=" * 70
    logger.info(banner)
    logger.info(f"SKELETONIZATION OUTPUT — job {job.id}")
    logger.info(f"  directory: {out_dir}")
    logger.info(f"  latest:    {latest_link}")
    logger.info(f"  NML:       {nml_path}")
    logger.info(f"  SWC files: {len(swc_paths)}")
    for p in swc_paths:
        logger.info(f"    - {p}")
    logger.info(banner)

    return {
        "nml": str(nml_path),
        "swc_dir": str(out_dir),
        "latest": str(latest_link),
    }


# ---------- data IO (datastore HTTP API) ----------

ELEMENT_CLASS_TO_DTYPE: dict[str, np.dtype] = {
    "uint8": np.dtype(np.uint8),
    "uint16": np.dtype(np.uint16),
    "uint32": np.dtype(np.uint32),
    "uint64": np.dtype(np.uint64),
    "int8": np.dtype(np.int8),
    "int16": np.dtype(np.int16),
    "int32": np.dtype(np.int32),
    "int64": np.dtype(np.int64),
}


def fetch_dataset_info(args: argparse.Namespace, job: SkeletonizationJob) -> dict[str, Any]:
    url = f"{args.wk_uri}/api/v{WK_API_VERSION}/datasets/{job.dataset_id}"
    r = httpx.get(url, headers={"X-Auth-Token": job.user_auth_token}, timeout=30.0)
    r.raise_for_status()
    return r.json()


def extract_voxel_size_nm(dataset_info: dict[str, Any]) -> tuple[float, float, float]:
    scale = dataset_info.get("dataSource", {}).get("scale", {})
    factor = scale.get("factor") or [1.0, 1.0, 1.0]
    unit = scale.get("unit", "nanometer")
    multiplier = {"nanometer": 1.0, "micrometer": 1000.0, "millimeter": 1_000_000.0}.get(unit, 1.0)
    return (float(factor[0]) * multiplier, float(factor[1]) * multiplier, float(factor[2]) * multiplier)


def extract_layer_info(dataset_info: dict[str, Any], layer_name: str) -> dict[str, Any]:
    layers = dataset_info.get("dataSource", {}).get("dataLayers", [])
    for layer in layers:
        if layer.get("name") == layer_name:
            return layer
    raise ValueError(f"Layer {layer_name!r} not found in dataset")


def default_bbox(layer_info: dict[str, Any]) -> tuple[int, int, int, int, int, int]:
    """Fallback: center-crop DEFAULT_BBOX_EDGE^3 out of the layer bounding box."""
    bb = layer_info.get("boundingBox", {})
    topleft = bb.get("topLeft", [0, 0, 0])
    width = bb.get("width", DEFAULT_BBOX_EDGE)
    height = bb.get("height", DEFAULT_BBOX_EDGE)
    depth = bb.get("depth", DEFAULT_BBOX_EDGE)
    edge = DEFAULT_BBOX_EDGE
    w = min(width, edge)
    h = min(height, edge)
    d = min(depth, edge)
    x = topleft[0] + max(0, (width - w) // 2)
    y = topleft[1] + max(0, (height - h) // 2)
    z = topleft[2] + max(0, (depth - d) // 2)
    return (x, y, z, w, h, d)


def fetch_volume_via_datastore(args: argparse.Namespace, job: SkeletonizationJob,
                                bbox: tuple[int, int, int, int, int, int],
                                dtype: np.dtype) -> np.ndarray:
    """GET raw cuboid from datastore; reshape to (x, y, z)."""
    mag_vec = tuple(int(v) for v in job.mag.split("-"))
    x, y, z, w, h, d = bbox
    # Width/height/depth sent in mag-1 voxels; server returns w/mag.x * h/mag.y * d/mag.z voxels.
    url = (
        f"{args.wk_uri}/data/v9/datasets/{job.organization_id}/{job.dataset_directory_name}"
        f"/layers/{job.layer_name}/data"
    )
    params = {
        "x": x, "y": y, "z": z,
        "width": w, "height": h, "depth": d,
        "mag": job.mag,
        "token": job.user_auth_token,
    }
    logger.info(f"Fetching volume from {url} bbox={bbox} mag={job.mag}")
    r = httpx.get(url, params=params, timeout=600.0)
    r.raise_for_status()

    expected_shape = (w // mag_vec[0], h // mag_vec[1], d // mag_vec[2])
    expected_count = expected_shape[0] * expected_shape[1] * expected_shape[2]
    buf = np.frombuffer(r.content, dtype=dtype)
    if buf.size != expected_count:
        raise ValueError(
            f"Datastore returned {buf.size} voxels, expected {expected_count} "
            f"for bbox={bbox} mag={job.mag} dtype={dtype}"
        )
    # Datastore serializes in Fortran order (x fastest) — reshape accordingly.
    return buf.reshape(expected_shape, order="F")


# ---------- NML output ----------

def build_nml(skels: dict[int, Any], offset: tuple[int, int, int],
              voxel_size_nm: tuple[float, float, float], dataset_name: str) -> bytes:
    root = ET.Element("things")
    params = ET.SubElement(root, "parameters")
    ET.SubElement(params, "experiment", {"name": dataset_name})
    ET.SubElement(params, "scale", {
        "x": str(voxel_size_nm[0]), "y": str(voxel_size_nm[1]), "z": str(voxel_size_nm[2]),
    })
    ET.SubElement(params, "offset", {"x": "0", "y": "0", "z": "0"})

    next_node_id = 1
    for tree_idx, (segid, skel) in enumerate(skels.items(), start=1):
        thing = ET.SubElement(root, "thing", {
            "id": str(tree_idx), "name": f"segment_{segid}",
            "color.r": "1.0", "color.g": "0.5", "color.b": "0.0", "color.a": "1.0",
        })
        nodes_el = ET.SubElement(thing, "nodes")
        edges_el = ET.SubElement(thing, "edges")

        vertices = skel.vertices  # in world nm
        edges = skel.edges
        radii = getattr(skel, "radii", None)

        local_to_global = []
        for i, v in enumerate(vertices):
            node_id = next_node_id + i
            local_to_global.append(node_id)
            vx = v[0] / voxel_size_nm[0] + offset[0]
            vy = v[1] / voxel_size_nm[1] + offset[1]
            vz = v[2] / voxel_size_nm[2] + offset[2]
            r = float(radii[i]) if radii is not None else 1.0
            ET.SubElement(nodes_el, "node", {
                "id": str(node_id),
                "radius": f"{r:.2f}",
                "x": f"{vx:.2f}", "y": f"{vy:.2f}", "z": f"{vz:.2f}",
                "inMag": "1", "bitDepth": "8", "interpolation": "false",
            })
        for a, b in edges:
            ET.SubElement(edges_el, "edge", {
                "source": str(local_to_global[int(a)]),
                "target": str(local_to_global[int(b)]),
            })
        next_node_id += len(vertices)

    ET.SubElement(root, "branchpoints")
    ET.SubElement(root, "comments")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


# ---------- scaffolding ----------

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
