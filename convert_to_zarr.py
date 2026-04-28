import os
import re
from pathlib import Path
import webknossos as wk

def natural_sort_key(s):
    return [int(text) if text.isdigit() else text.lower()
            for text in re.split('([0-9]+)', str(s))]

def convert_tiff_dir_to_zarr(input_dir, output_path, name, voxel_size=(4, 4, 40), layer_category=wk.COLOR_CATEGORY):
    input_dir = Path(input_dir)
    # Recursively find all tiff files just to count them
    files = sorted([f for f in input_dir.glob("*.tiff")], key=natural_sort_key)
    
    if not files:
        print(f"No TIFF files found in {input_dir}")
        return

    print(f"Converting {len(files)} files from {input_dir} to {output_path} (Zarr v2)...")
    
    # Create the dataset from the image stack using Zarr v2 (non-sharded)
    # data_format="zarr" ensures Zarr v2 compatibility
    dataset = wk.Dataset.from_images(
        input_path=input_dir,
        output_path=output_path,
        voxel_size=voxel_size,
        name=name,
        layer_category=layer_category,
        compress=True,
        data_format="zarr"
    )
    
    # Downsample the dataset for better viewing performance
    print(f"Downsampling {name}...")
    dataset.downsample()
    
    print(f"Successfully converted and downsampled {name} to {output_path}")

if __name__ == "__main__":
    # Convert ac3Data (likely grayscale EM images)
    ac3_data_input = "/Users/ericnguyen/MagNeurific/ac3Data"
    ac3_data_output = "/Users/ericnguyen/MagNeurific/ac3Data_zarr_v2"
    if os.path.exists(ac3_data_input):
        convert_tiff_dir_to_zarr(
            ac3_data_input,
            ac3_data_output,
            "ac3Data",
            voxel_size=(4, 4, 40),
            layer_category=wk.COLOR_CATEGORY
        )
    else:
        print(f"Directory not found: {ac3_data_input}")

    # Convert ac3synapse (likely segmentations)
    ac3_synapse_input = "/Users/ericnguyen/MagNeurific/ac3synapse"
    ac3_synapse_output = "/Users/ericnguyen/MagNeurific/ac3synapse_zarr_v2"
    if os.path.exists(ac3_synapse_input):
        convert_tiff_dir_to_zarr(
            ac3_synapse_input,
            ac3_synapse_output,
            "ac3synapse",
            voxel_size=(4, 4, 40),
            layer_category=wk.SEGMENTATION_CATEGORY
        )
    else:
        print(f"Directory not found: {ac3_synapse_input}")
