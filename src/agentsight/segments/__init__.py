from __future__ import annotations

from agentsight.segments.decoder import (
    decode_segment_diff_to_png,
    decode_segment_frame_to_image,
    decode_segment_frame_to_png,
    decode_segment_region_to_image_content,
    decode_segment_region_to_png,
    query_segment_change_index,
    query_segment_decoder_near_time,
    query_segment_review_clip,
    query_segment_timeline_diff,
)
from agentsight.segments.global_index import build_global_segment_frame_index, query_segment_frames_near_time
from agentsight.segments.manifest import (
    SEGMENT_INDEX_SCHEMA,
    SEGMENT_SCHEMA,
    boundary_facts,
    build_empty_segment_index,
    build_empty_segment_manifest,
    build_frame_record,
    sha256_image_rgba,
    validate_segment_manifest,
)
from agentsight.segments.binary_container import BINARY_CONTAINER_MODEL, BinarySegmentReader, BinarySegmentWriter
from agentsight.segments.mkv_container import MKV_CONTAINER_MODEL, MkvSegmentWriter, decode_mkv_frame_to_image, iter_mkv_frames
from agentsight.segments.recorder import SegmentFrameRecorder
from agentsight.segments.reader import SegmentReader
from agentsight.segments.writer import SegmentWriter

__all__ = [
    "BINARY_CONTAINER_MODEL",
    "BinarySegmentReader",
    "BinarySegmentWriter",
    "MKV_CONTAINER_MODEL",
    "MkvSegmentWriter",
    "SegmentFrameRecorder",
    "SegmentReader",
    "SEGMENT_INDEX_SCHEMA",
    "SEGMENT_SCHEMA",
    "SegmentWriter",
    "boundary_facts",
    "build_empty_segment_index",
    "build_empty_segment_manifest",
    "build_frame_record",
    "build_global_segment_frame_index",
    "decode_mkv_frame_to_image",
    "decode_segment_diff_to_png",
    "decode_segment_frame_to_image",
    "decode_segment_frame_to_png",
    "decode_segment_region_to_image_content",
    "decode_segment_region_to_png",
    "iter_mkv_frames",
    "query_segment_change_index",
    "query_segment_decoder_near_time",
    "query_segment_frames_near_time",
    "query_segment_review_clip",
    "query_segment_timeline_diff",
    "sha256_image_rgba",
    "validate_segment_manifest",
]
