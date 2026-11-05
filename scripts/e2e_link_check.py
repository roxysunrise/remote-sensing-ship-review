"""真实 HTTP 端到端联调检查：A 侧后端 + B 侧前端契约路径。

与 tests/ 的区别：这里不打 TestClient，全部走真实 uvicorn 进程和真实 socket，
路径前缀用前端实际调用的 `/api/v1`（见 frontend/config.js）。

用法：
    python -m backend.server --port 8000          # 另一个终端
    python scripts/e2e_link_check.py --image frontend/assets/harbor.png

判据：每一步打印 [PASS]/[FAIL]；任何一步失败即退出码 1。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import sys
import time

import httpx
from PIL import Image

PREFIX = "/api/v1"
USERNAME = "roxysunrise"
PASSWORD = "demo-1234"
PROJECT = "harbor-a"

PASSED: list[str] = []
FAILED: list[str] = []


def step(name: str, ok: bool, detail: str = "") -> bool:
    if ok:
        PASSED.append(name)
        print(f"[PASS] {name}{('  ' + detail) if detail else ''}", flush=True)
    else:
        FAILED.append(name)
        print(f"[FAIL] {name}{('  ' + detail) if detail else ''}", flush=True)
    return ok


def wait_for(fn, timeout: float, interval: float = 0.2):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = fn()
        if last is not None:
            return last
        time.sleep(interval)
    return last


def make_data_url(path: str, max_edge: int = 1600) -> tuple[str, tuple[int, int]]:
    with Image.open(path) as im:
        im = im.convert("RGB")
        scale = min(1.0, max_edge / max(im.size))
        size = (max(1, int(im.width * scale)), max(1, int(im.height * scale)))
        if size != im.size:
            im = im.resize(size, Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=85)
        payload = buf.getvalue()
    return "data:image/jpeg;base64," + base64.b64encode(payload).decode(), size


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--image", default="frontend/assets/harbor.png")
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args()

    with open(args.image, "rb") as fh:
        image_bytes = fh.read()
    digest = hashlib.sha256(image_bytes).hexdigest()
    print(f"影像 {args.image}  {len(image_bytes)} B  sha256={digest[:16]}…", flush=True)

    # trust_env=False：本机装了系统代理时，httpx 会读到注册表代理并把 127.0.0.1 也发过去（502）
    client = httpx.Client(base_url=args.base_url, timeout=120.0, follow_redirects=True,
                          trust_env=False)
    t0 = time.perf_counter()

    # 0) 健康检查
    health = client.get(f"{PREFIX}/health").json()
    step("健康检查", health.get("status") in {"ok", "degraded"},
         f"status={health.get('status')} model_ready={health.get('model_ready')} "
         f"active_tasks={health.get('active_tasks')}")

    # 1) 登录 / 会话
    login = client.post(f"{PREFIX}/auth/login",
                        json={"username": USERNAME, "password": PASSWORD, "project_id": PROJECT})
    ok = step("登录", login.status_code == 200, f"HTTP {login.status_code}")
    token = login.json().get("token") if login.status_code == 200 else None
    if not token:
        print(login.text[:800], flush=True)
        return 1
    headers = {"Authorization": f"Bearer {token}"}

    me = client.get(f"{PREFIX}/me", headers=headers).json()
    step("会话 /me", me.get("id") == "usr_owner"
         and (me.get("project") or {}).get("id") == PROJECT,
         f"id={me.get('id')} role={me.get('role')} project={(me.get('project') or {}).get('id')}")

    bad = client.post(f"{PREFIX}/auth/login",
                      json={"username": USERNAME, "password": "wrong", "project_id": PROJECT})
    step("错误口令被拒", bad.status_code == 401 and bad.json()["error"]["code"] == "INVALID_CREDENTIALS",
         f"HTTP {bad.status_code} code={bad.json().get('error', {}).get('code')}")

    # 2) 项目
    projects = client.get(f"{PREFIX}/projects", headers=headers).json()
    detail = client.get(f"{PREFIX}/projects/{PROJECT}", headers=headers).json()
    step("项目列表与详情", len(projects) >= 1 and detail.get("code") == "PRJ-A-0271",
         f"count={len(projects)} code={detail.get('code')} members={len(detail.get('members', []))}")

    # 3) 可恢复分片上传
    chunk_size = 1 << 20
    created = client.post(f"{PREFIX}/projects/{PROJECT}/uploads",
                          json={"file_name": "harbor-e2e.png", "size": len(image_bytes)},
                          headers=headers)
    if not step("创建上传会话", created.status_code == 201, f"HTTP {created.status_code}"):
        print(created.text[:800], flush=True)
        return 1
    upload = created.json()
    upload_id = upload["id"]
    chunk_size = int(upload.get("chunk_size") or chunk_size)

    chunks = [image_bytes[i:i + chunk_size] for i in range(0, len(image_bytes), chunk_size)]
    for index, blob in enumerate(chunks):
        put = client.put(f"{PREFIX}/uploads/{upload_id}/chunks/{index}", content=blob,
                         headers={**headers, "Content-Type": "application/octet-stream"})
        if put.status_code != 200:
            step(f"上传分片 {index}", False, f"HTTP {put.status_code} {put.text[:200]}")
            return 1
    step("分片上传", True, f"{len(chunks)} 片 × {chunk_size} B")

    dup = client.put(f"{PREFIX}/uploads/{upload_id}/chunks/0", content=chunks[0],
                     headers={**headers, "Content-Type": "application/octet-stream"})
    step("重复分片幂等", dup.status_code == 200, f"HTTP {dup.status_code}")

    completed = client.post(f"{PREFIX}/uploads/{upload_id}/complete",
                            json={"sha256": digest, "size": len(image_bytes)}, headers=headers)
    step("提交完整校验", completed.status_code == 200, f"HTTP {completed.status_code}")

    def upload_state():
        body = client.get(f"{PREFIX}/uploads/{upload_id}", headers=headers).json()
        return body if body.get("status") in {"ready", "failed"} else None

    state = wait_for(upload_state, 90) or {}
    ok = step("影像校验与入库", state.get("status") == "ready",
              f"status={state.get('status')} stage={state.get('stage')} image_id={state.get('image_id')}")
    if not ok:
        print(json.dumps(state, ensure_ascii=False)[:800], flush=True)
        return 1
    image_id = state["image_id"]

    image = client.get(f"{PREFIX}/images/{image_id}", headers=headers).json()
    step("影像元数据", image.get("sha256") == digest,
         f"{image.get('width')}×{image.get('height')} sha256 一致={image.get('sha256') == digest}")

    listing = client.get(f"{PREFIX}/projects/{PROJECT}/images", headers=headers).json()
    listed = [item.get("id") for item in listing.get("items", [])]
    step("项目影像列表", image_id in listed and listing.get("total") == len(listed),
         f"total={listing.get('total')} 新影像在内={image_id in listed}")

    preview = client.get(f"{PREFIX}/previews/{image_id}", headers=headers)
    etag = preview.headers.get("etag")
    cached = client.get(f"{PREFIX}/previews/{image_id}",
                        headers={**headers, "If-None-Match": etag}) if etag else None
    step("预览图与 ETag 304", preview.status_code == 200 and cached is not None and cached.status_code == 304,
         f"preview={preview.status_code} etag={etag} 二次={getattr(cached, 'status_code', 'n/a')}")

    # 4) 大图瓦片 + 切片预演
    manifest = client.get(f"{PREFIX}/images/{image_id}/tile-manifest", headers=headers).json()
    tile0 = client.get(f"{PREFIX}/tiles/{image_id}/0/0/0", headers=headers)
    out_of_range = client.get(f"{PREFIX}/tiles/{image_id}/0/99/0", headers=headers)
    step("大图瓦片", manifest.get("max_z") is not None and tile0.status_code == 200
         and out_of_range.status_code == 404,
         f"levels={len(manifest.get('levels', []))} max_z={manifest.get('max_z')} "
         f"tile={tile0.status_code} 越界={out_of_range.status_code}")

    plan = client.post(f"{PREFIX}/images/{image_id}/tile-plan",
                       json={"tile_size": 1024, "stride": 512}, headers=headers)
    plan_body = plan.json()
    step("切片预演", plan.status_code == 200 and plan_body.get("tile_count", 0) > 0,
         f"tile_count={plan_body.get('tile_count')} overlap={plan_body.get('overlap_ratio')}")

    # 5) 检测任务（真实推理）
    task = client.post(f"{PREFIX}/projects/{PROJECT}/tasks", headers=headers, json={
        "image_id": image_id,
        "parameters": {"tiling": True, "tile_size": 1024, "stride": 512, "imgsz": 640,
                       "conf": 0.15, "iou": 0.7, "max_det": 300, "merge_iou": 0.1},
    })
    if not step("创建检测任务", task.status_code == 201, f"HTTP {task.status_code} {task.text[:200]}"):
        return 1
    task_id = task.json()["id"]
    started = time.perf_counter()
    seen_progress = False

    def task_state():
        nonlocal seen_progress
        body = client.get(f"{PREFIX}/tasks/{task_id}", headers=headers).json()
        if (body.get("tiles_done") or 0) > 0:
            seen_progress = True
        return body if body.get("status") in {"done", "failed", "cancelled"} else None

    final = wait_for(task_state, args.timeout) or {}
    elapsed = round(time.perf_counter() - started, 2)
    step("任务跑完", final.get("status") == "done",
         f"status={final.get('status')} state={final.get('state')} tiles={final.get('tiles_done')}/"
         f"{final.get('tiles_total')} final_count={final.get('final_count')} {elapsed}s")
    step("进度可见", seen_progress or (final.get("tiles_total") or 0) == 1,
         f"observed tiles_done>0 before done")
    if final.get("status") != "done":
        print(json.dumps(final, ensure_ascii=False)[:1200], flush=True)
        return 1

    # 6) 结果
    result = client.get(f"{PREFIX}/tasks/{task_id}/result", headers=headers).json()
    detections = result.get("detections", [])
    tiling = result.get("tiling") or {}
    step("检测结果 v0.2", result.get("schema_version") == "ship-detection/0.2"
         and result.get("count") == len(detections),
         f"count={result.get('count')} candidate={tiling.get('candidate_count')} "
         f"suppressed={tiling.get('suppressed_count')} 恢复坐标={result.get('image', {}).get('id') == image_id}")

    # 7) 草稿 → 人工补框 → 版本
    draft = client.get(f"{PREFIX}/tasks/{task_id}/draft", headers=headers).json()
    step("草稿读取", len(draft.get("detections", [])) == len(detections),
         f"detections={len(draft.get('detections', []))}")

    versions = client.get(f"{PREFIX}/tasks/{task_id}/versions", headers=headers).json()
    step("初始版本 v0 只读", [v.get("label") for v in versions] == ["v0"]
         and versions[0].get("readonly") is True, f"versions={[v.get('label') for v in versions]}")

    conflict = client.post(f"{PREFIX}/tasks/{task_id}/versions", json={}, headers=headers)
    step("空提交被拒 409", conflict.status_code == 409
         and conflict.json()["error"]["code"] == "NOTHING_TO_COMMIT", f"HTTP {conflict.status_code}")

    added = client.post(f"{PREFIX}/tasks/{task_id}/draft/detections", headers=headers, json={
        "polygon": [[100, 100], [260, 100], [260, 160], [100, 160]],
        "origin": "manual", "review_state": "confirmed", "note": "联调人工补框",
    })
    added_body = added.json() if added.status_code == 201 else {}
    step("人工补框", added.status_code == 201 and added_body.get("confidence") is None,
         f"id={added_body.get('id')} confidence={added_body.get('confidence')}")

    moved = client.patch(f"{PREFIX}/tasks/{task_id}/draft/detections/{added_body.get('id')}",
                         headers=headers,
                         json={"polygon": [[110, 104], [272, 104], [272, 166], [110, 166]]})
    step("改几何自动置 modified", moved.status_code == 200
         and moved.json().get("review_state") == "modified",
         f"review_state={moved.json().get('review_state')}")

    committed = client.post(f"{PREFIX}/tasks/{task_id}/versions",
                            json={"label": "联调修订"}, headers=headers)
    committed_body = committed.json() if committed.status_code == 201 else {}
    step("提交版本 v1", committed.status_code == 201
         and committed_body.get("label") == "v1"
         and (committed_body.get("summary") or {}).get("added") == 1,
         f"label={committed_body.get('label')} summary={committed_body.get('summary')}")

    v0 = client.get(f"{PREFIX}/versions/v0?task_id={task_id}", headers=headers).json()
    step("历史版本只读可读", v0.get("readonly") is True
         and len(v0.get("detections", [])) == len(detections), f"v0 detections={len(v0.get('detections', []))}")

    # 8) 复核队列 + 区域统计
    queue = client.get(f"{PREFIX}/tasks/{task_id}/draft/review-queue", headers=headers).json()
    step("复核队列", "items" in queue and "sampling" in queue,
         f"items={len(queue.get('items', []))} sampling={queue.get('sampling')}")

    region = client.post(f"{PREFIX}/tasks/{task_id}/draft/regions", headers=headers, json={
        "name": "联调全区", "polygon": [[0, 0], [image["width"], 0],
                                        [image["width"], image["height"]], [0, image["height"]]],
    })
    region_body = region.json() if region.status_code == 200 else {}
    counts = region_body.get("counts") or {}
    step("区域计数", region.status_code == 200
         and counts.get("effective") == len(detections) + 1,
         f"rule={region_body.get('rule')} counts={counts}")

    # 9) 导出三格式
    export = client.post(f"{PREFIX}/versions/v1/exports?task_id={task_id}", headers=headers,
                         json={"formats": ["csv", "json", "png"]})
    export_id = export.json().get("id") if export.status_code == 202 else None
    step("创建导出", export.status_code == 202, f"HTTP {export.status_code} export_id={export_id}")

    def export_state():
        body = client.get(f"{PREFIX}/exports/{export_id}", headers=headers).json()
        return body if body.get("status") in {"done", "failed"} else None

    exported = wait_for(export_state, 120) or {}
    files = exported.get("files") or []
    step("导出完成", exported.get("status") == "done" and len(files) == 3,
         f"status={exported.get('status')} formats={[f.get('format') for f in files]}")

    csv_file = next((f for f in files if f.get("format") == "csv"), None)
    if csv_file:
        download = client.get(csv_file["url"], headers=headers)
        head = download.text.splitlines()[:2]
        step("下载 CSV", download.status_code == 200 and "review_state" in download.text,
             f"HTTP {download.status_code} bytes={len(download.content)} 表头={head[0] if head else ''}")
    else:
        step("下载 CSV", False, "导出结果里没有 csv")

    # 10) 统计
    pstats = client.get(f"{PREFIX}/projects/{PROJECT}/stats", headers=headers)
    tstats = client.get(f"{PREFIX}/tasks/{task_id}/stats", headers=headers)
    pbody = pstats.json() if pstats.status_code == 200 else {}
    tbody = tstats.json() if tstats.status_code == 200 else {}
    step("项目统计", pstats.status_code == 200 and "tasks" in pbody,
         f"keys={sorted(pbody.keys())[:6]}")
    step("任务统计", tstats.status_code == 200 and tbody.get("timing") is not None,
         f"duration_ms={(tbody.get('timing') or {}).get('duration_ms')} "
         f"versions={(tbody.get('versions') or {}).get('count')}")

    # 11) AI 候选（真实模型 ROI + 轮廓兜底）
    # 提示点用真实检出框的中心，避免点在空水域（那会得到合法的 422 NO_CANDIDATE）
    if detections:
        poly = detections[0]["polygon"]
        hint = (sum(p[0] for p in poly) / 4.0, sum(p[1] for p in poly) / 4.0)
    else:
        hint = (image["width"] * 0.5, image["height"] * 0.5)
    cand = client.post(f"{PREFIX}/images/{image_id}/ai-candidates", headers=headers,
                       json={"mode": "point", "point": [int(hint[0]), int(hint[1])]})
    cand_body = cand.json() if cand.status_code in {200, 201} else {}
    step("AI 候选", cand.status_code in {200, 201} and bool(cand_body.get("polygon")),
         f"HTTP {cand.status_code} hint={[int(hint[0]), int(hint[1])]} "
         f"source={cand_body.get('source')} reason={cand_body.get('reason')} "
         f"quality={cand_body.get('quality')}")
    if cand_body.get("id"):
        accepted = client.post(f"{PREFIX}/ai-candidates/{cand_body['id']}/accept",
                               json={"note": "联调采纳"}, headers=headers)
        step("采纳 AI 候选", accepted.status_code == 201, f"HTTP {accepted.status_code}")
    planner = client.get(f"{PREFIX}/ai/planner", headers=headers).json()
    step("VLM 规划器状态", "enabled" in planner or "provider" in planner, f"{planner}")

    # 12) /vision/detect 坐标还原（前端检测页走的路径）
    data_url, payload_size = make_data_url(args.image, max_edge=1600)
    frame = {"index": 0, "time_ms": 0,
             "width": payload_size[0] // 2, "height": payload_size[1] // 2}
    vision = client.post(f"{PREFIX}/vision/detect", headers=headers, json={
        "image": {"data_url": data_url},
        "frame": frame,
        "options": {"conf": 0.2, "max_det": 100},
    })
    vbody = vision.json() if vision.status_code == 200 else {}
    inside = True
    for det in vbody.get("detections", []):
        for x, y in det.get("polygon", []):
            if not (0 <= x <= frame["width"] and 0 <= y <= frame["height"]):
                inside = False
    step("/vision/detect 坐标还原", vision.status_code == 200 and inside,
         f"HTTP {vision.status_code} payload={payload_size} frame={frame['width']}×{frame['height']} "
         f"count={vbody.get('count')} elapsed_ms={vbody.get('elapsed_ms')} 全部点在帧内={inside}")

    caps = client.get(f"{PREFIX}/vision/capabilities").json()
    step("/vision/capabilities", caps.get("loaded") is True,
         f"models={list((caps.get('models') or {}).keys())} loaded={caps.get('loaded')}")

    # 13) 错误信封一致性
    denied = client.get(f"{PREFIX}/projects/harbor-b")
    step("未带令牌 401 信封", denied.status_code == 401
         and denied.json()["error"]["code"] == "UNAUTHENTICATED"
         and denied.headers.get("x-request-id") is not None,
         f"HTTP {denied.status_code} code={denied.json().get('error', {}).get('code')} "
         f"request_id={denied.headers.get('x-request-id')}")

    total = round(time.perf_counter() - t0, 2)
    print("\n" + "=" * 70, flush=True)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项，总耗时 {total}s", flush=True)
    if FAILED:
        print("失败项：" + " | ".join(FAILED), flush=True)
    print("=" * 70, flush=True)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
