from __future__ import annotations

import json
import os
import sys

from ai_control.adapters.session import SessionAdapter


def _handle_request(client: SessionAdapter, request: dict[str, object]) -> dict[str, object]:
    return client.call(str(request["command"]), request.get("payload", {}))  # type: ignore[arg-type]


def main() -> int:
    client = SessionAdapter(runs_dir=os.environ.get("AI_CONTROL_RUNS_DIR", "runs"), adapter_ref="local_cli")
    raw = sys.stdin.read().strip()
    if not raw:
        print(json.dumps({"ok": False, "error": "empty input"}))
        return 1

    if "\n" in raw:
        for line in raw.splitlines():
            if not line.strip():
                continue
            response = _handle_request(client, json.loads(line))
            print(json.dumps(response, ensure_ascii=False))
        return 0

    request = json.loads(raw)
    if isinstance(request, list):
        responses = [_handle_request(client, item) for item in request]
        print(json.dumps(responses, ensure_ascii=False))
    else:
        print(json.dumps(_handle_request(client, request), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
