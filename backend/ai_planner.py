"""VLM 规划器：让大模型做"判断"，几何仍交给分割/检测。

定位（应用优先）：

    图块 ──▶ VLM：这里有没有船？大概几艘？ ──▶ 有/不确定 → 走 ai_assist 的几何通道
                                          └─▶ 没有     → 直接 NO_CANDIDATE

VLM **不画框**：它输出的是语义判断，坐标精度不足以做旋转框；几何一律由 `ai_assist`
（ROI 高分辨率重检 / 轮廓兜底 / 未来的 SAM2）产出。

生产可用要点（都是应用项目必须的，不是论文指标）：
1. 默认关闭，不配 `SHIP_VLM_PROVIDER` 就没有任何外部调用；
2. 失败重试 `SHIP_VLM_RETRIES` 次，仍失败返回 `None`（"没意见"）→ 调用方照原逻辑继续；
3. 单次回答限制 `SHIP_VLM_MAX_TOKENS`，避免长输出烧钱；
4. **每日调用上限** `SHIP_VLM_DAILY_LIMIT`，超了自动停用 VLM（降级而非报错），防止成本失控；
5. 每次调用写 `ai.vlm_call` 事件（模型、耗时、判定、token 用量、是否重试），既可算成本也能排查。
"""
from __future__ import annotations

import base64
import io
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from . import config, db

PROMPT = (
    "你是遥感影像判读助手。图中可能包含船舶（ship）、码头、浮标、陆地设施。"
    "请只判断这块区域里是否存在船舶，不要输出像素坐标。"
    '严格返回 JSON：{"has_ship": true/false, "count": 整数, "reason": "不超过30字的中文理由"}'
)


def _encode(image) -> str:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


def _post(payload: dict) -> dict | None:
    headers = {"Content-Type": "application/json"}
    if config.VLM_API_KEY:
        headers["Authorization"] = f"Bearer {config.VLM_API_KEY}"
    request = urllib.request.Request(
        config.VLM_BASE_URL.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=config.VLM_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def _call_vlm(data_url: str) -> dict | None:
    """调用一次（含重试）。返回 {"verdict", "reason", "usage", "attempts"} 或 None。"""
    payload = {
        "model": config.VLM_MODEL,
        "temperature": 0,
        "max_tokens": config.VLM_MAX_TOKENS,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }],
    }
    attempts = 0
    for attempt in range(max(1, config.VLM_RETRIES + 1)):
        attempts = attempt + 1
        try:
            body = _post(payload)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            continue
        parsed = _parse(_content_of(body))
        if parsed is not None:
            parsed["usage"] = body.get("usage") or {}
            parsed["attempts"] = attempts
            return parsed
    return None


def _content_of(body: dict):
    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None


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


def _day_start() -> str:
    return datetime.now(timezone.utc).date().isoformat() + "T00:00:00+00:00"


def usage_today() -> int:
    row = db.one("SELECT COUNT(*) AS n FROM events WHERE action='ai.vlm_call' AND created_at >= ?",
                 (_day_start(),))
    return int(row["n"]) if row else 0


def status() -> dict:
    used = usage_today() if enabled() else 0
    return {
        "enabled": enabled(),
        "provider": config.VLM_PROVIDER,
        "model": config.VLM_MODEL,
        "endpoint": config.VLM_BASE_URL,
        "timeout_s": config.VLM_TIMEOUT,
        "retries": config.VLM_RETRIES,
        "max_tokens": config.VLM_MAX_TOKENS,
        "daily_limit": config.VLM_DAILY_LIMIT,
        "used_today": used,
        "remaining": max(0, config.VLM_DAILY_LIMIT - used),
        "budget_exhausted": enabled() and used >= config.VLM_DAILY_LIMIT,
        "note": "关闭或超预算时 AI 补框自动降级为纯几何通道，不影响可用性",
    }


def _record(project_id: str | None, user_id: str | None, target: str | None, detail: dict) -> None:
    db.log_event("ai.vlm_call", project_id=project_id, user_id=user_id, target=target, detail=detail)


def make_gate(record: dict | None = None, *, project_id: str | None = None,
              user_id: str | None = None):
    """返回 gate 函数：输入 ROI 图，输出 "yes" / "no" / None（没意见）。"""
    if not enabled():
        return None
    if usage_today() >= config.VLM_DAILY_LIMIT:
        if record is not None:
            record.update({"provider": config.VLM_PROVIDER, "model": config.VLM_MODEL,
                           "skipped": "daily_limit", "verdict": None})
        return None

    def gate(crop):
        started = time.perf_counter()
        result = _call_vlm(_encode(crop))
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        verdict = None if result is None else ("yes" if result["has_ship"] else "no")
        detail = {
            "provider": config.VLM_PROVIDER,
            "model": config.VLM_MODEL,
            "elapsed_ms": elapsed,
            "verdict": verdict,
            "reason": None if result is None else result.get("reason"),
            "count": None if result is None else result.get("count"),
            "attempts": None if result is None else result.get("attempts", 1),
            "tokens": None if result is None else (result.get("usage") or {}).get("total_tokens"),
        }
        _record(project_id, user_id, None, detail)
        if record is not None:
            record.update(detail)
        return verdict

    return gate
