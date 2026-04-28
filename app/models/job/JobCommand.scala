package models.job

import com.scalableminds.util.enumeration.ExtendedEnumeration

object JobCommand extends ExtendedEnumeration {
  type JobCommand = Value

  /* NOTE: When adding a new job command here, do
   * - Decide if it should be a highPriority job
   * - Add it to the dbtool.js command enable-jobs so it is available during development
   * - Add it to the migration guide (operators need to decide which workers should provide it)
   */

  val compute_mesh_file, compute_segment_index_file, convert_to_wkw, export_tiff, find_largest_segment_id,
  materialize_volume_annotation, render_animation, align_sections, infer_nuclei, infer_neurons, infer_instances,
  infer_mitochondria, skeletonize_segmentation, train_neuron_model, train_instance_model,
  ingest_large_dataset, batch_skeletonize, train_glia_model, infer_glia, push_glia_segmentation,
  // No-longer supported jobs, kept here to be able to display old existing jobs:
  globalize_floodfills, train_model, infer_with_model = Value

  val highPriorityJobs: Set[Value] = Set(convert_to_wkw, export_tiff, ingest_large_dataset, batch_skeletonize)
  val lowPriorityJobs: Set[Value] = values.diff(highPriorityJobs)
}
