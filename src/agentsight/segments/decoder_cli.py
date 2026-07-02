from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from agentsight.segments.decoder import (
    decode_segment_diff_to_png,
    decode_segment_frame_to_png,
    decode_segment_region_to_png,
    query_segment_change_index,
    query_segment_decoder_near_time,
)
from agentsight.segments.manifest import boundary_facts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentsight-segment-decoder")
    sub = parser.add_subparsers(dest="command", required=True)

    frame = sub.add_parser("frame", help="Decode a Segment frame to PNG.")
    frame.add_argument("--segment-path", required=True)
    frame.add_argument("--frame-id", required=True)
    frame.add_argument("--output", required=True)

    region = sub.add_parser("region", help="Decode a Segment frame region to PNG.")
    region.add_argument("--segment-path", required=True)
    region.add_argument("--frame-id", required=True)
    region.add_argument("--output", required=True)
    region.add_argument("--x", type=int, required=True)
    region.add_argument("--y", type=int, required=True)
    region.add_argument("--w", type=int, required=True)
    region.add_argument("--h", type=int, required=True)
    region.add_argument("--scale-down", type=float, default=1)
    region.add_argument("--blur-radius", type=float, default=0)

    diff = sub.add_parser("diff", help="Decode a Segment before/after diff heatmap to PNG.")
    diff.add_argument("--segment-path")
    diff.add_argument("--before-segment-path")
    diff.add_argument("--after-segment-path")
    diff.add_argument("--before-frame-id", required=True)
    diff.add_argument("--after-frame-id", required=True)
    diff.add_argument("--output", required=True)
    diff.add_argument("--x", type=int)
    diff.add_argument("--y", type=int)
    diff.add_argument("--w", type=int)
    diff.add_argument("--h", type=int)

    near = sub.add_parser("near", help="Query nearest indexed Segment frame by time.")
    near.add_argument("--root", required=True)
    near.add_argument("--time", required=True)

    changes = sub.add_parser("changes", help="Query metadata-only pixel changes between indexed Segment frames.")
    changes.add_argument("--root", required=True)
    changes.add_argument("--x", type=int)
    changes.add_argument("--y", type=int)
    changes.add_argument("--w", type=int)
    changes.add_argument("--h", type=int)
    changes.add_argument("--max-pairs", type=int)
    changes.add_argument("--min-changed-pixel-ratio", type=float, default=0.0)
    changes.add_argument("--from-time")
    changes.add_argument("--to-time")

    args = parser.parse_args(argv)
    try:
        if args.command == "frame":
            report = decode_segment_frame_to_png(
                _restore_ref(args.segment_path, args.frame_id),
                output_path=Path(args.output),
            )
        elif args.command == "region":
            report = decode_segment_region_to_png(
                _restore_ref(args.segment_path, args.frame_id),
                region={"x": args.x, "y": args.y, "w": args.w, "h": args.h},
                output_path=Path(args.output),
                scale_down=args.scale_down,
                blur_radius=args.blur_radius,
            )
        elif args.command == "diff":
            before_segment_path = args.before_segment_path or args.segment_path
            after_segment_path = args.after_segment_path or args.segment_path
            if not before_segment_path or not after_segment_path:
                raise ValueError("segment_diff_requires_segment_path_or_before_after_paths")
            report = decode_segment_diff_to_png(
                _restore_ref(before_segment_path, args.before_frame_id),
                _restore_ref(after_segment_path, args.after_frame_id),
                output_path=Path(args.output),
                region=_optional_region(args),
            )
        else:
            if args.command == "near":
                report = query_segment_decoder_near_time(Path(args.root), args.time)
            else:
                report = query_segment_change_index(
                    Path(args.root),
                    region=_optional_region(args),
                    max_pairs=args.max_pairs,
                    min_changed_pixel_ratio=args.min_changed_pixel_ratio,
                    start_time=args.from_time,
                    end_time=args.to_time,
                )
    except Exception as exc:
        report = {
            "ok": False,
            "error": type(exc).__name__,
            "detail": str(exc),
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "boundary": boundary_facts(),
        }
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    report["ok"] = bool(report.get("hash_ok", True)) if args.command in {"frame", "region", "diff"} else True
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _restore_ref(segment_path: str, frame_id: str) -> dict[str, Any]:
    path = Path(segment_path)
    return {
        "storage_format": "binary_agseg" if path.suffix.lower() == ".agseg" else "proto_directory",
        "segment_path": str(path),
        "frame_id": frame_id,
    }


def _optional_region(args: Any) -> dict[str, int] | None:
    values = (args.x, args.y, args.w, args.h)
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise ValueError("region_requires_x_y_w_h")
    return {"x": int(args.x), "y": int(args.y), "w": int(args.w), "h": int(args.h)}


if __name__ == "__main__":
    raise SystemExit(main())
