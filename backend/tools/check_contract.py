"""契约一致性检查：拿真实运行中的后端响应，去核对前端给的 OpenAPI 规格。

用法（后端已启动）：
    python -m backend.tools.check_contract --base-url http://127.0.0.1:8000 \
        --spec frontend/api/openapi.yaml

检查方式是最小化的 JSON Schema 校验：`$ref / type / required / properties / items /
enum / nullable / allOf / minItems`。它不能替代完整 OpenAPI 校验器，但足以在联调阶段
发现字段名、枚举值、必填项和类型的偏差。
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TYPES = {
    "string": str, "integer": int, "number": (int, float), "boolean": bool,
    "array": list, "object": dict,
}


def _resolve(spec: dict, ref: str) -> dict:
    assert ref.startswith("#/"), f"只支持本地 $ref：{ref}"
    node = spec
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def validate(value, schema: dict, spec: dict, path: str = "$") -> list[str]:
    problems: list[str] = []
    if schema is None:
        return problems
    if "$ref" in schema:
        return validate(value, _resolve(spec, schema["$ref"]), spec, path)
    for entry in schema.get("allOf", []):
        problems += validate(value, entry, spec, path)

    if value is None:
        if schema.get("nullable") or schema.get("type") == "null":
            return problems
        return [f"{path}: 不允许为 null"]

    expected = schema.get("type")
    if expected and expected in TYPES and not isinstance(value, TYPES[expected]):
        return [f"{path}: 期望 {expected}，实际 {type(value).__name__}"]
    if expected == "integer" and isinstance(value, bool):
        return [f"{path}: 期望 integer，实际 bool"]

    if "enum" in schema and value not in schema["enum"]:
        problems.append(f"{path}: {value!r} 不在枚举 {schema['enum']}")

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                problems.append(f"{path}.{key}: 缺少必填字段")
        for key, sub in (schema.get("properties") or {}).items():
            if key in value:
                problems += validate(value[key], sub, spec, f"{path}.{key}")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            problems.append(f"{path}: 少于 minItems={schema['minItems']}")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            problems.append(f"{path}: 多于 maxItems={schema['maxItems']}")
        item_schema = schema.get("items")
        if item_schema:
            for index, item in enumerate(value[:20]):
                problems += validate(item, item_schema, spec, f"{path}[{index}]")
    return problems


class Client:
    def __init__(self, base_url: str):
        self.base = base_url.rstrip("/")
        self.token = ""

    def request(self, method: str, path: str, body=None, raw: bytes | None = None,
                content_type: str = "application/json"):
        data = raw
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif raw is not None:
            headers["Content-Type"] = content_type
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = response.read()
                return response.status, (json.loads(payload) if payload else None)
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            try:
                parsed = json.loads(payload) if payload else None
            except json.JSONDecodeError:
                parsed = payload.decode("utf-8", "replace")
            return exc.code, parsed


def _sample_png(size=(400, 300)) -> bytes:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", size, (26, 40, 58))
    draw = ImageDraw.Draw(image)
    for index in range(6):
        x = 30 + index * 55
        draw.rectangle([x, 90, x + 40, 115], fill=(245, 245, 235))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def run(base_url: str, spec_path: Path, username: str, password: str, project: str) -> int:
    import hashlib

    spec = json.loads(json.dumps(_load_yaml(spec_path)))
    client = Client(base_url)
    checks: list[tuple[str, str, list[str]]] = []

    def check(label: str, path: str, body=None):
        schema = (spec["paths"].get(path, {}).get("get", {}).get("responses", {})
                  .get("200", {}).get("content", {}).get("application/json", {}).get("schema"))
        status, payload = client.request("GET", path, body=body)
        if status != 200:
            checks.append((label, path, [f"HTTP {status}: {payload}"]))
            return None
        problems = validate(payload, schema, spec) if schema else []
        checks.append((label, path, problems))
        return payload

    status, login = client.request("POST", "/auth/login",
                                   body={"username": username, "password": password,
                                         "project_id": project})
    if status != 200:
        print(f"登录失败：HTTP {status} {login}")
        return 2
    client.token = login["token"]
    checks.append(("auth.login", "/auth/login", []))

    check("health", "/health")
    check("me", "/me")
    check("projects", "/projects")
    check("project", f"/projects/{project}")

    image_bytes = _sample_png()
    status, upload = client.request("POST", f"/projects/{project}/uploads",
                                    body={"file_name": "contract.png", "size": len(image_bytes)})
    checks.append(("uploads.create", f"/projects/{project}/uploads",
                   [] if status == 201 else [f"HTTP {status}: {upload}"]))
    upload_id = upload["id"]
    status, _ = client.request("PUT", f"/uploads/{upload_id}/chunks/0", raw=image_bytes,
                               content_type="application/octet-stream")
    checks.append(("uploads.putChunk", f"/uploads/{{id}}/chunks/{{index}}",
                   [] if status == 200 else [f"HTTP {status}"]))
    status, _ = client.request("POST", f"/uploads/{upload_id}/complete",
                               body={"sha256": hashlib.sha256(image_bytes).hexdigest(),
                                     "size": len(image_bytes)})
    checks.append(("uploads.complete", f"/uploads/{{id}}/complete",
                   [] if status == 200 else [f"HTTP {status}"]))
    check("uploads.get", f"/uploads/{upload_id}")

    deadline = time.time() + 120
    upload_state = {}
    while time.time() < deadline:
        _, upload_state = client.request("GET", f"/uploads/{upload_id}")
        if upload_state["status"] in {"ready", "failed"}:
            break
        time.sleep(0.2)
    checks.append(("uploads.ready", f"/uploads/{upload_id}",
                   [] if upload_state.get("status") == "ready" else [str(upload_state)]))
    image_id = upload_state.get("image_id")
    check("images.get", f"/images/{image_id}")

    status, task = client.request("POST", f"/projects/{project}/tasks",
                                  body={"image_id": image_id,
                                        "parameters": {"tiling": True, "tile_size": 256,
                                                       "stride": 128, "imgsz": 256}})
    checks.append(("tasks.create", f"/projects/{project}/tasks",
                   [] if status == 201 else [f"HTTP {status}: {task}"]))
    task_id = task["id"]

    deadline = time.time() + 600
    task_state = {}
    while time.time() < deadline:
        _, task_state = client.request("GET", f"/tasks/{task_id}")
        if task_state["status"] in {"done", "failed", "cancelled"}:
            break
        time.sleep(0.25)
    task_schema = spec["components"]["schemas"]["Task"]
    checks.append(("tasks.get", f"/tasks/{{task_id}}", validate(task_state, task_schema, spec)))
    if task_state.get("status") != "done":
        print(f"任务未完成：{task_state}")
        _print(checks)
        return 1

    check("tasks.result", f"/tasks/{task_id}/result")
    check("draft.get", f"/tasks/{task_id}/draft")
    check("versions.list", f"/tasks/{task_id}/versions")
    check("versions.get", f"/versions/v0?task_id={task_id}")
    check("review.queue", f"/tasks/{task_id}/draft/review-queue")

    status, region = client.request("POST", f"/tasks/{task_id}/draft/regions",
                                    body={"name": "契约检查区",
                                          "polygon": [[0, 0], [400, 0], [400, 300], [0, 300]]})
    region_schema = spec["components"]["schemas"]["Region"]
    checks.append(("regions.create", f"/tasks/{{id}}/draft/regions",
                   validate(region, region_schema, spec) if status == 200 else [f"HTTP {status}"]))

    status, export = client.request("POST", f"/versions/v0/exports?task_id={task_id}",
                                    body={"formats": ["csv", "json", "png"]})
    export_schema = spec["components"]["schemas"]["Export"]
    checks.append(("exports.create", f"/versions/{{vid}}/exports",
                   validate(export, export_schema, spec) if status == 202 else [f"HTTP {status}"]))
    export_id = export.get("id")
    deadline = time.time() + 120
    export_state = {}
    while time.time() < deadline:
        _, export_state = client.request("GET", f"/exports/{export_id}")
        if export_state.get("status") in {"done", "failed"}:
            break
        time.sleep(0.2)
    checks.append(("exports.get", f"/exports/{{id}}", validate(export_state, export_schema, spec)))

    return _print(checks)


def _load_yaml(path: Path) -> dict:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("需要 PyYAML 才能读取 OpenAPI 规格：pip install pyyaml") from exc
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def run_vision(base_url: str, spec_path: Path, token: str = "") -> int:
    """核对 `/vision/*`（前端 detect-openapi.yaml）。"""
    import base64

    from PIL import Image

    spec = _load_yaml(spec_path)
    client = Client(base_url)
    client.token = token
    checks: list[tuple[str, str, list[str]]] = []

    def check(label: str, path: str, method: str = "get", body=None):
        schema = (spec["paths"].get(path, {}).get(method, {}).get("responses", {})
                  .get("200", {}).get("content", {}).get("application/json", {}).get("schema"))
        status, payload = client.request(method.upper(),
                                         path.replace("{job_id}", "job_contract"), body=body)
        if status != 200:
            checks.append((label, path, [f"HTTP {status}: {payload}"]))
            return None
        problems = validate(payload, schema, spec) if schema else []
        checks.append((label, path, problems))
        return payload

    check("vision.capabilities", "/vision/capabilities")
    check("vision.sessions", "/vision/sessions", "post",
          {"kind": "image", "name": "contract.png", "width": 400, "height": 300})

    image = Image.open(io.BytesIO(_sample_png())).convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    data_url = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()

    payload = check("vision.detect", "/vision/detect", "post", {
        "session_id": "ses_contract", "kind": "image",
        "frame": {"index": 0, "time_ms": 0, "width": 800, "height": 600},
        "image": {"data_url": data_url, "width": 400, "height": 300},
        "options": {"conf": 0.05, "max_det": 50},
    })
    if payload:
        # 坐标必须是原始媒体像素：frame 是 payload 的 2 倍，所有点都不应超出 800×600
        overflow = [p for det in payload["detections"] for p in det["polygon"]
                    if p[0] > 800 or p[1] > 600]
        checks.append(("vision.detect.coords", "/vision/detect",
                       [f"坐标超出原始媒体范围：{overflow[:3]}"] if overflow else []))

    check("vision.segment", "/vision/segment", "post", {
        "session_id": "ses_contract", "kind": "image",
        "frame": {"index": 0, "time_ms": 0, "width": 800, "height": 600},
        "image": {"data_url": data_url, "width": 400, "height": 300},
        "roi": [60, 140, 260, 260], "mode": "roi", "prompt": "船舶",
    })
    check("vision.cancel", "/vision/jobs/{job_id}/cancel", "post", {})
    return _print(checks)



def _print(checks) -> int:
    failures = 0
    width = max(len(label) for label, _, _ in checks)
    for label, path, problems in checks:
        if problems:
            failures += 1
            print(f"FAIL  {label:<{width}}  {path}")
            for problem in problems[:8]:
                print(f"        - {problem}")
        else:
            print(f"ok    {label:<{width}}  {path}")
    print(f"\n{len(checks) - failures}/{len(checks)} 个端点通过契约校验")
    return 0 if failures == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--spec", type=Path, default=ROOT / "frontend/api/openapi.yaml")
    parser.add_argument("--vision-spec", type=Path,
                        default=ROOT / "frontend/api/detect-openapi.yaml")
    parser.add_argument("--username", default="roxysunrise")
    parser.add_argument("--password", default="demo-1234")
    parser.add_argument("--project", default="harbor-a")
    args = parser.parse_args()

    if not args.spec.is_file() and not args.vision_spec.is_file():
        print("找不到任何 OpenAPI 规格（frontend/ 由 B 方的 PR 提供，合入后再跑）")
        return 2

    exit_code = 0
    if args.spec.is_file():
        print("== 业务端点（openapi.yaml） ==")
        exit_code |= run(args.base_url, args.spec, args.username, args.password, args.project)
    if args.vision_spec.is_file():
        print("\n== 推理端点（detect-openapi.yaml） ==")
        client = Client(args.base_url)
        status, login = client.request("POST", "/auth/login",
                                       body={"username": args.username, "password": args.password,
                                             "project_id": args.project})
        exit_code |= run_vision(args.base_url, args.vision_spec,
                                login["token"] if status == 200 else "")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
