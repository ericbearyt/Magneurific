# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "httpx",
#   "zmesh",
#   "numpy",
#   "numpy-stl",
# ]
# ///

"""Mesh generation service.

Polls WEBKNOSSOS for `compute_mesh_file` jobs. For each requested segmentation
layer, generates a mesh per segment id using zmesh (marching cubes) and writes
STL files at:

    binaryData/<org>/<datasetDir>/<layer>/meshes/<jobId>/<segmentId>.stl

These STL files are a downloadable artifact, NOT a WK precomputed mesh file —
the full WK HDF5 mesh format requires bucket-keyed neuroglancer-precomputed
chunks (see Hdf5MeshFileService.scala). This worker provides the geometry; if
you need WK to auto-load meshes for 3D viewing without recompute, see the
TODO at the bottom of this file for the chunk packing path.

Environment variables:
  WORKER_KEY          worker key registered in WEBKNOSSOS
  BINARY_DATA_DIR     local path where dataset binaryData is mounted
  MESH_OUTPUT_DIR     override output base (default: <repo>/mesh_output)
  MESH_BBOX_EDGE      cube edge in mag-1 voxels to mesh (default 1024)
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

logger = logging.getLogger(__name__)

WK_API_VERSION = 12
POLL_ERROR_BACKOFF = 5.0
WORKER_VERSION = "mesh-service-0.1"
JOB_COMMAND = "compute_mesh_file"

DEFAULT_BBOX_EDGE = int(os.environ.get("MESH_BBOX_EDGE", "1024"))

ELEMENT_CLASS_TO_DTYPE: dict[str, np.dtype] = {
    "uint8": np.dtype(np.uint8),
    "uint16": np.dtype(np.uint16),
    "uint32": np.dtype(np.uint32),
    "uint64": np.dtype(np.uint64),
}


@dataclass
class MeshJob:
    id: str
    organization_id: str
    dataset_name: str
    dataset_id: str
    dataset_directory_name: str
    layer_name: str
    mag: str
    user_auth_token: str

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "MeshJob":
        a = payload["job_kwargs"]
        return cls(
            id=payload["job_id"],
            organization_id=a["organization_id"],
            dataset_name=a["dataset_name"],
            dataset_id=a["dataset_id"],
            dataset_directory_name=a["dataset_directory_name"],
            layer_name=a["layer_name"],
            mag=a["mag"],
            user_auth_token=a.get("user_auth_token", ""),
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
    job = MeshJob.from_api(job_payload)
    logger.info(f"Claimed job {job.id} (layer={job.layer_name}, mag={job.mag})")
    report_status(args, job.id, "STARTED")
    try:
        out_dir = run_mesh(args, job)
        report_status(args, job.id, "SUCCESS", return_value=str(out_dir))
        logger.info(f"Job {job.id} finished OK -> {out_dir}")
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


def run_mesh(args: argparse.Namespace, job: MeshJob) -> Path:
    import zmesh

    dataset_info = fetch_dataset_info(args, job)
    voxel_size = extract_voxel_size_nm(dataset_info)
    layer_info = extract_layer_info(dataset_info, job.layer_name)
    element_class = layer_info.get("elementClass", "uint32")
    dtype = ELEMENT_CLASS_TO_DTYPE.get(element_class)
    if dtype is None:
        raise ValueError(f"Unsupported segmentation dtype: {element_class}")

    mag_vec = tuple(int(v) for v in job.mag.split("-"))
    if len(mag_vec) != 3:
        raise ValueError(f"Mag must be x-y-z, got {job.mag}")

    bbox = center_bbox(layer_info, DEFAULT_BBOX_EDGE)
    logger.info(f"Meshing bbox={bbox} mag={job.mag}")

    volume = fetch_volume_via_datastore(args, job, bbox, dtype)
    nonzero = int((volume != 0).sum())
    logger.info(f"Volume shape={volume.shape} nonzero={nonzero}")
    if nonzero == 0:
        raise RuntimeError("No labels in requested bbox")

    anisotropy = tuple(v * m for v, m in zip(voxel_size, mag_vec))
    mesher = zmesh.Mesher(anisotropy)
    mesher.mesh(volume)

    base = Path(os.environ.get("MESH_OUTPUT_DIR")) if os.environ.get("MESH_OUTPUT_DIR") else _default_output_base()
    out_dir = base / job.dataset_directory_name / job.layer_name / job.id
    out_dir.mkdir(parents=True, exist_ok=True)

    bbox_origin_nm = (bbox[0] * voxel_size[0], bbox[1] * voxel_size[1], bbox[2] * voxel_size[2])
    written = 0
    for seg_id in mesher.ids():
        m = mesher.get(seg_id, normals=False)
        m = mesher.simplify(m, reduction_factor=10, max_error=40)
        write_stl(out_dir / f"{int(seg_id)}.stl", m, bbox_origin_nm)
        written += 1
    logger.info(f"Wrote {written} STL meshes to {out_dir}")

    # TODO: pack into WK HDF5 mesh file format. See:
    #   webknossos-datastore/.../mesh/Hdf5MeshFileService.scala  (keys: bucket_offsets, buckets, neuroglancer)
    # Required steps:
    #   1. Encode each per-segment mesh as a Neuroglancer precomputed chunk (DRACO).
    #   2. Compute bucket offsets via the WK hash function over segment ids.
    #   3. Write attrs: lod_scale_multiplier, transform (4x3 matrix), mesh_format.
    return out_dir


def _default_output_base() -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / "mesh_output"


def write_stl(path: Path, m, origin_nm: tuple[float, float, float]) -> None:
    """Write an ASCII STL of the mesh, offset by origin_nm so vertices are in dataset world nm."""
    vertices = np.asarray(m.vertices, dtype=np.float32) + np.asarray(origin_nm, dtype=np.float32)
    faces = np.asarray(m.faces, dtype=np.int64)
    with path.open("w") as f:
        f.write("solid mesh\n")
        for tri in faces:
            v0, v1, v2 = vertices[tri[0]], vertices[tri[1]], vertices[tri[2]]
            n = np.cross(v1 - v0, v2 - v0)
            norm = np.linalg.norm(n)
            if norm > 0:
                n = n / norm
            f.write(f"  facet normal {n[0]:.4f} {n[1]:.4f} {n[2]:.4f}\n")
            f.write("    outer loop\n")
            for v in (v0, v1, v2):
                f.write(f"      vertex {v[0]:.2f} {v[1]:.2f} {v[2]:.2f}\n")
            f.write("    endloop\n")
            f.write("  endfacet\n")
        f.write("endsolid mesh\n")


def fetch_dataset_info(args: argparse.Namespace, job: MeshJob) -> dict[str, Any]:
    url = f"{args.wk_uri}/api/v{WK_API_VERSION}/datasets/{job.dataset_id}"
    headers = {"X-Auth-Token": job.user_auth_token} if job.user_auth_token else {}
    r = httpx.get(url, headers=headers, timeout=30.0)
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


def center_bbox(layer_info: dict[str, Any], edge: int) -> tuple[int, int, int, int, int, int]:
    bb = layer_info.get("boundingBox", {})
    topleft = bb.get("topLeft", [0, 0, 0])
    width = bb.get("width", edge)
    height = bb.get("height", edge)
    depth = bb.get("depth", edge)
    w, h, d = min(width, edge), min(height, edge), min(depth, edge)
    x = topleft[0] + max(0, (width - w) // 2)
    y = topleft[1] + max(0, (height - h) // 2)
    z = topleft[2] + max(0, (depth - d) // 2)
    return (x, y, z, w, h, d)


def fetch_volume_via_datastore(args: argparse.Namespace, job: MeshJob,
                               bbox: tuple[int, int, int, int, int, int],
                               dtype: np.dtype) -> np.ndarray:
    mag_vec = tuple(int(v) for v in job.mag.split("-"))
    x, y, z, w, h, d = bbox
    url = (
        f"{args.wk_uri}/data/v9/datasets/{job.organization_id}/{job.dataset_directory_name}"
        f"/layers/{job.layer_name}/data"
    )
    params = {"x": x, "y": y, "z": z, "width": w, "height": h, "depth": d, "mag": job.mag}
    if job.user_auth_token:
        params["token"] = job.user_auth_token
    r = httpx.get(url, params=params, timeout=600.0)
    r.raise_for_status()
    expected = (w // mag_vec[0]) * (h // mag_vec[1]) * (d // mag_vec[2])
    buf = np.frombuffer(r.content, dtype=dtype)
    if buf.size != expected:
        raise ValueError(f"Datastore returned {buf.size} voxels, expected {expected}")
    return buf.reshape((w // mag_vec[0], h // mag_vec[1], d // mag_vec[2]), order="F")


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
