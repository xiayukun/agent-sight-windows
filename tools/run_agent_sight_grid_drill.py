from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path
from typing import Any


CALLER = os.environ.get("AI_CONTROL_CALLER", "agent-sight-grid-drill-20260621")
BLUE_TARGET = (37, 99, 235)
GREEN_DONE = (22, 163, 74)


def main() -> int:
    agent_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "ai-control"
    discovery = json.loads((agent_dir / "host-agent.json").read_text(encoding="utf-8"))
    client = HostClient(discovery)
    page = (Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "agent_sight_grid_drill.html").resolve()
    page_uri = page.as_uri()

    first_view = client.look_fullscreen("grid-drill-before-open", scale_down=2)["view"]
    open_result = client.do(
        "grid-drill-open-page",
        first_view["id"],
        [
            {"t": "chord", "modifiers": ["CTRL"], "key": "L"},
            100,
            {"t": "text", "text": page_uri},
            100,
            {"t": "key", "key": "ENTER"},
            800,
        ],
        post_observe={"delay_ms": 200, "frame_count": 1, "interval_ms": 0},
    )

    view = client.look_fullscreen("grid-drill-page-loaded", scale_down=1)["view"]
    target_points = find_color_components(Path(view["path"]), BLUE_TARGET, min_area=1000)
    target_points = sorted(target_points, key=lambda point: (point["y"], point["x"]))[:30]
    click_results: list[dict[str, Any]] = []
    for index, point in enumerate(target_points, start=1):
        result = client.do(
            f"grid-drill-click-{index:02d}",
            view["id"],
            [
                {"t": "move", "x": point["x"], "y": point["y"], "coord": "view", "move": "instant"},
                40,
                {"t": "click", "b": "left"},
            ],
            post_observe={"delay_ms": 30, "frame_count": 1, "interval_ms": 0},
        )
        click_results.append(
            {
                "i": index,
                "point": point,
                "status": result.get("status"),
                "input": result.get("input"),
                "post_observe_status": (result.get("post_observe") or {}).get("status"),
                "tool_asserts_business_success": False,
            }
        )

    after = client.look_fullscreen("grid-drill-after-30-clicks", scale_down=1)["view"]
    remaining_blue = find_color_components(Path(after["path"]), BLUE_TARGET, min_area=1000)
    done_green = find_color_components(Path(after["path"]), GREEN_DONE, min_area=1000)
    report = {
        "object_type": "AgentSightGridDrillReport",
        "schema": "agent_sight_grid_drill_v1",
        "page_uri": page_uri,
        "open_result": {"status": open_result.get("status"), "input": open_result.get("input")},
        "target_count_detected": len(target_points),
        "click_count_attempted": len(click_results),
        "click_results": click_results,
        "after_view": after,
        "remaining_blue_component_count": len(remaining_blue),
        "green_component_count": len(done_green),
        "host_input_sent": any(bool((item.get("input") or {}).get("sent")) for item in click_results),
        "host_sent_event_count": sum(int((item.get("input") or {}).get("host_event_count") or 0) for item in click_results),
        "tool_asserts_target_hit": False,
        "tool_asserts_causality": False,
        "tool_asserts_business_success": False,
        "boundary": {
            "ocr_used": False,
            "clipboard_used": False,
            "accessibility_tree_used": False,
            "dom_used": False,
            "window_semantics_used": False,
            "business_success_judged": False,
        },
    }
    report_path = agent_dir / "agent-sight-grid-drill-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report_path": str(report_path), **{k: report[k] for k in ("target_count_detected", "click_count_attempted", "remaining_blue_component_count", "green_component_count", "host_sent_event_count")}}, ensure_ascii=False, indent=2))
    return 0 if len(target_points) == 30 and len(click_results) == 30 else 2


class HostClient:
    def __init__(self, discovery: dict[str, Any]) -> None:
        self.url = str(discovery["url"])
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {discovery['token']}",
            "X-AI-Control-Caller": CALLER,
        }

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            self.url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers=self.headers,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))

    def look_fullscreen(self, request_id: str, *, scale_down: int) -> dict[str, Any]:
        return self.post(
            "/look",
            {
                "v": "V1",
                "id": request_id,
                "op": "look",
                "q": "frame",
                "src": {"type": "screen", "t": "latest"},
                "r": {"x": 0, "y": 0, "w": 1920, "h": 1080},
                "scale_down": scale_down,
            },
        )

    def do(self, request_id: str, view_id: str, seq: list[Any], *, post_observe: dict[str, Any]) -> dict[str, Any]:
        return self.post(
            "/do",
            {
                "v": "V1",
                "id": request_id,
                "op": "do",
                "basis": {"view_id": view_id},
                "seq": seq,
                "post_observe": post_observe,
            },
        )


def find_color_components(path: Path, target_rgb: tuple[int, int, int], *, min_area: int) -> list[dict[str, int]]:
    from PIL import Image

    with Image.open(path) as image:
        rgb = image.convert("RGB")
        width, height = rgb.size
        pixels = rgb.load()
        mask: set[tuple[int, int]] = set()
        for y in range(height):
            for x in range(width):
                if close_rgb(pixels[x, y], target_rgb, tolerance=18):
                    mask.add((x, y))
        components: list[dict[str, int]] = []
        while mask:
            seed = mask.pop()
            stack = [seed]
            min_x = max_x = seed[0]
            min_y = max_y = seed[1]
            area = 1
            while stack:
                x, y = stack.pop()
                min_x = min(min_x, x)
                max_x = max(max_x, x)
                min_y = min(min_y, y)
                max_y = max(max_y, y)
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if (nx, ny) in mask:
                        mask.remove((nx, ny))
                        stack.append((nx, ny))
                        area += 1
            if area >= min_area:
                components.append(
                    {
                        "x": (min_x + max_x) // 2,
                        "y": (min_y + max_y) // 2,
                        "area": area,
                        "bbox_x": min_x,
                        "bbox_y": min_y,
                        "bbox_w": max_x - min_x + 1,
                        "bbox_h": max_y - min_y + 1,
                    }
                )
        return components


def close_rgb(actual: tuple[int, int, int], expected: tuple[int, int, int], *, tolerance: int) -> bool:
    return all(abs(int(actual[i]) - int(expected[i])) <= tolerance for i in range(3))


if __name__ == "__main__":
    raise SystemExit(main())
