"""VLM 规划器：让大模型做"判断"，几何仍交给分割/检测。

定位（对应 `docs/frontend-integration.md` §10 的接法 A）：

    图块 ──▶ VLM：这里有没有船？大概几艘？ ──▶ 有 → 走 ai_assist 的几何通道
                                          └─▶ 没有 → 直接 NO_CANDIDATE

VLM **不负责画框**：它输出的是语义判断，坐标精度不足以做旋转框。几何一律由
`ai_assist`（ROI 高分辨率重检 / 轮廓兜底 / 未来的 SAM2）产出。

三条硬约束：
1. 默认关闭（`SHIP_VLM_PROVIDER=none`），不配置就没有任何外部调用；
2. 任何异常、超时、返回解析失败都返回 `None`（"没意见"），调用方按原逻辑继续，
   **不能因为大模型挂了导致 AI 补框不可用**；
3. 判定结果与模型名写进候选的 `reason`/事件，评估时可复现。
"""
from __future__ import annotations

import base64
import io
import json
import time
import urllib.error
import urllib.request

from . import config

PROMPT = (
    "你是遥感影像判读助手。图中可能包含船舶（ship）、码头、浮标、陆地设施。"
    "请只判断这块区域里是否存在船舶，不要输出像素坐标。"
    '严格返回 JSON：{"has_ship": true/false, "count": 整数, "reason": "不超过30字的中文理由"}'
)


def _encode(image) -> str:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


def _call_vlm(data_url: str) -> dict | None:
    payload = {
        "model": config.VLM_MODEL,
        "temperature": 0,
        "max_tokens": 200,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }],
    }
    headers = {"Content-Type": "application/json"}
    if config.VLM_API_KEY:
        headers["Authorization"] = f"Bearer {config.VLM_API_KEY}"
    request = urllib.request.Request(
        config.VLM_BASE_URL.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=config.VLM_TIMEOUT) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None
    return _parse(content)


def _parse(content) -> dict | None:
    if isinstance(content, list):                      # 部分实现返回分段内容
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    if not isinstance(content, str):
        return None
    text = content.strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    if "has_ship" not in parsed:
        return None
    return {"has_ship": bool(parsed.get("has_ship")),
            "count": int(parsed.get("count") or 0),
            "reason": str(parsed.get("reason") or "")[:60]}


def enabled() -> bool:
    return config.VLM_PROVIDER not in {"", "none", "off", "false", "False"}


def make_gate(record: dict | None = None):
    """返回一个 gate 函数：输入 ROI 图，输出 "yes" / "no" / None（没意见）。"""
    if not enabled():
        return None

    def gate(crop):
        started = time.perf_counter()
        verdict = _call_vlm(_encode(crop))
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        if record is not None:
            record.update({
                "provider": config.VLM_PROVIDER,
                "model": config.VLM_MODEL,
                "elapsed_ms": elapsed,
                "verdict": None if verdict is None else ("yes" if verdict["has_ship"] else "no"),
                "reason": None if verdict is None else verdict["reason"],
            })
        if verdict is None:
            return None
        return "yes" if verdict["has_ship"] else "no"

    return gate
