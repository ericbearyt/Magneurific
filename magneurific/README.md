# MagNeurific

**MagNeurific** is an interactive, collaborative web application designed for exploring, annotating, and sharing massive-scale, high-resolution brain imaging datasets. It provides a multi-panel viewer (XY, YZ, XZ, and 3D views) tailored for intuitive medical and neuroscience imaging.

## Core Features

### 1. Multipanel Magnifiers
- View various brain regions simultaneously using synchronized 2D orthogonal slices (XY, XZ, YZ planes).
- Each panel allows you to independently zoom and pan, acting as a magnifier to inspect structures deeply while maintaining spatial context.

### 2. Global Navigation Panel
- A smaller global overview panel located at the top right of the interface.
- Provides a macroscopic, low-detail baseline view of the entire brain coordinate space.

### 3. Dynamic Tracing, Lasso & Annotations
- Trace and outline structures using open Paths or closed Lassos.
- Paint Bucket fills that directly lock into Lasso configurations for instantaneous neuron compartment highlighting.
- Highlighting sidebars map segments alongside custom text notes. 

### 4. XYZ Coordinate Tracking
- An XYZ axis indicator dynamically tracks your translation (Pan/Zoom/Space).
- Allows precise navigation and the ability to jump to specific spatial coordinates in the volume.

### 5. Collaborative Environment
- Real-time sharing allows multiple users to view, trace, and annotate the same dataset simultaneously using unified cursor spaces.

---

## Technical Implementation Plan & Data Architecture

### 1. Large Volume Data Preprocessing (Image Pyramids)
Raw data (like heavy 8-bit or 16-bit TIFF/PNG continuous stacks from electron microscopes) is computationally disastrous to load directly into the browser. 
To achieve "infinite zoom" similar to Google Maps, datasets must be processed into highly-scalable array configurations:
- **Formats:** The pipeline will rely on formats such as **OME-Zarr**, **HDF5 (h5)**, or **N5**. 
- **Chunked Level-of-Detail (LOD):** These formats chunk the 3D volume into pyramidal resolutions. Fast, low-magnification overviews load instantly, while moving the viewer bounding box triggers background HTTP requests fetching high-resolution, specific coordinate chunks directly down the microscope scale.

### 2. Frontend Render Architecture
- **Framework:** React + Vite, tracking UI state globally using `Zustand`.
- **Styling:** Vanilla CSS focusing on native glassmorphism, responsive native UI layouts, and customizable sidebars for metadata injection.
- **Rendering Engine:** **WebGL / Three.js** to handle the heavy lifting, ingesting the loaded Zarr/H5 chunks and rendering manipulating slicing planes smoothly on the client's GPU.

### 3. Annotation & Sync Layer
- **SVG Overlay:** High-performance overlapping SVG elements scale proportionally inverse to the WebGL coordinates, locking user-drawn paths rigidly to the biological elements beneath.
- **WebSockets / WebRTC:** Real-time synchronization syncing the exact spatial bounding boxes, cursor positions, and metadata modifications between users.
