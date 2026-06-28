from __future__ import annotations

from typing import Any


SUGGESTED_NEXT = {
    "reobserve": "重新观察",
    "request_authorization": "请求授权",
    "narrow_scope": "缩小范围",
    "stop": "停止",
    "switch_configured_channel": "切换已配置通道",
    "return_to_ai": "返回上层决策",
}


class CapabilityFailureService:
    def make_failure(
        self,
        failure_code: str,
        *,
        stage: str,
        evidence_ref: dict[str, Any] | None = None,
        boundary_type: str | None = None,
        retryable: bool = False,
        suggested_next: list[str] | None = None,
        stopped_input: bool = True,
        released_inputs: bool | str = True,
        evidence_incomplete: bool = False,
        detail: str | None = None,
    ) -> dict[str, Any]:
        allowed_suggestions = suggested_next or ["reobserve", "stop", "return_to_ai"]
        allowed_suggestions = [item for item in allowed_suggestions if item in SUGGESTED_NEXT]
        data: dict[str, Any] = {
            "object_type": "CapabilityFailure",
            "failure_code": failure_code,
            "stage": stage,
            "stopped_input": stopped_input,
            "released_inputs": released_inputs,
            "retryable": retryable,
            "suggested_next": allowed_suggestions,
        }
        if boundary_type:
            data["boundary_type"] = boundary_type
        if evidence_ref:
            data["evidence_ref"] = evidence_ref
        if evidence_incomplete:
            data["evidence_incomplete"] = True
        if detail:
            data["detail"] = detail
        return data
