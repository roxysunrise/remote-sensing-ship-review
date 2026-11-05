"""`ai_assist` 候选生成核心的单元测试（不依赖权重，用脚本化检测器）。

坐标约定：模型返回的是 **crop 空间**坐标，`ai_assist` 负责加上 crop 偏移；
测试里用 `crop_offset()` 把"图像空间想放的位置"换算成 crop 空间，避免坐标错位。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
# 与 test_backend 共用同一套环境（谁先导入 backend 谁绑定配置）
_TMP = os.environ.setdefault("SHIP_VAR_DIR",
                             str(Path(tempfile.gettempdir()) / f"ship-test-{os.getpid()}"))
os.environ.setdefault("SHIP_DB_PATH", str(Path(_TMP) / "test.db"))
os.environ.setdefault("SHIP_DEVICE", "cpu")
os.environ.setdefault("SHIP_SEED_PASSWORD", "test-password-1234")
os.environ.setdefault("SHIP_MAX_ACTIVE_TASKS", "1")
os.environ.setdefault("SHIP_CHUNK_SIZE", "65536")
os.environ.setdefault("SHIP_WEIGHTS", str(ROOT / "models/yolov8l-obb-dota-v1-80e/best.pt"))
sys.path.insert(0, str(ROOT))

from backend import ai_assist, errors  # noqa: E402


class ScriptedService:
    """按预设返回检测结果的假服务（crop 空间），用来单独验证 ROI 与过滤逻辑。"""

    def __init__(self, detections):
        self.detections = detections
        self.calls = []

    def predict_image(self, image, params):
        self.calls.append((image.size, params))
        return {"detections": list(self.detections), "count": len(self.detections)}


def rect(x0, y0, x1, y1, conf=0.9):
    return {"polygon": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
            "class_id": 1, "class_name": "ship", "confidence": conf}


def make_image(size=(400, 300), rects=((100, 100, 190, 150),)) -> Image.Image:
    image = Image.new("RGB", size, (18, 24, 32))
    draw = ImageDraw.Draw(image)
    for box in rects:
        draw.rectangle(list(box), fill=(245, 245, 235))
    return image


def crop_box_for(image, *, roi=None, point=None, existing=None, mode="box"):
    box = ai_assist._hint_box(image, roi=roi, point=point,
                              existing_polygon=existing, mode=mode)
    return ai_assist._clamp_box(box, image)


def to_crop(image, box, target_rect):
    """把图像空间的目标框换算到 crop 空间（脚本化服务要返回 crop 坐标）。"""
    left, top, _, _ = box
    x0, y0, x1, y1 = target_rect
    return rect(x0 - left, y0 - top, x1 - left, y1 - top)


class AiAssistTests(unittest.TestCase):
    def test_refine_keeps_candidate_near_existing_box(self):
        image = make_image()
        existing = [[100, 100], [190, 100], [190, 150], [100, 150]]
        box = crop_box_for(image, existing=existing, mode="refine")
        service = ScriptedService([to_crop(image, box, (102, 102, 188, 148))])
        candidates = ai_assist.propose(service, image, mode="refine", existing_polygon=existing)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["source"], "detect")
        self.assertTrue(0.0 <= candidates[0]["quality"] <= 1.0)

    def test_refine_rejects_candidate_on_neighbour_ship(self):
        image = make_image(size=(600, 300), rects=((100, 100, 190, 150), (420, 100, 510, 150)))
        existing = [[100, 100], [190, 100], [190, 150], [100, 150]]
        box = crop_box_for(image, existing=existing, mode="refine")
        # 让"模型"在 ROI 里返回一个位于 crop 右侧很远的框（模拟跳到隔壁船）
        service = ScriptedService([rect(300, 20, 390, 66)])
        self.assertEqual(ai_assist.propose(service, image, mode="refine",
                                           existing_polygon=existing), [])

    def test_fill_mode_accepts_box_hint(self):
        image = make_image()
        roi = [90, 90, 200, 160]
        box = crop_box_for(image, roi=roi, mode="box")
        service = ScriptedService([to_crop(image, box, (102, 102, 188, 148))])
        candidates = ai_assist.propose(service, image, roi=roi, mode="box")
        self.assertEqual(len(candidates), 1)
        self.assertEqual(len(candidates[0]["polygon"]), 4)

    def test_area_ratio_filter_drops_tiny_and_huge(self):
        image = make_image()
        roi = [90, 90, 200, 160]
        box = crop_box_for(image, roi=roi, mode="box")
        tiny = ScriptedService([to_crop(image, box, (102, 102, 106, 103))])
        self.assertEqual(ai_assist.propose(tiny, image, roi=roi, mode="box"), [])
        huge = ScriptedService([rect(-40, -40, 400, 400)])
        self.assertEqual(ai_assist.propose(huge, image, roi=roi, mode="box"), [])

    def test_aspect_filter_drops_square_blob(self):
        image = make_image()
        roi = [90, 90, 200, 160]
        box = crop_box_for(image, roi=roi, mode="box")
        service = ScriptedService([to_crop(image, box, (102, 102, 142, 142))])  # 1:1
        self.assertEqual(ai_assist.propose(service, image, roi=roi, mode="box"), [])

    def test_contour_fallback_when_detector_returns_nothing(self):
        image = make_image()
        roi = [90, 90, 200, 160]
        candidates = ai_assist.propose(ScriptedService([]), image, roi=roi, mode="box")
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["source"], "contour")
        self.assertIn("非语义分割", candidates[0]["reason"])

    def test_invalid_hint_raises(self):
        image = make_image()
        with self.assertRaises(errors.ApiError) as ctx:
            ai_assist.propose(ScriptedService([]), image, mode="fill")
        self.assertEqual(ctx.exception.code, "INVALID_ARGUMENT")

    def test_tiny_region_raises(self):
        image = make_image()
        with self.assertRaises(errors.ApiError) as ctx:
            ai_assist.propose(ScriptedService([]), image, roi=[10, 10, 12, 12], mode="box")
        self.assertEqual(ctx.exception.code, "INVALID_REGION")


if __name__ == "__main__":
    unittest.main()
