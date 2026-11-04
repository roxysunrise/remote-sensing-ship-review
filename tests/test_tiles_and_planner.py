"""瓦片服务与 VLM 规划器的单元测试（不依赖权重、不联网）。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
_TMP = os.environ.setdefault("SHIP_VAR_DIR",
                             str(Path(tempfile.gettempdir()) / f"ship-test-{os.getpid()}"))
os.environ.setdefault("SHIP_DB_PATH", str(Path(_TMP) / "test.db"))
os.environ.setdefault("SHIP_DEVICE", "cpu")
os.environ.setdefault("SHIP_SEED_PASSWORD", "test-password-1234")
os.environ.setdefault("SHIP_MAX_ACTIVE_TASKS", "1")
os.environ.setdefault("SHIP_CHUNK_SIZE", "65536")
os.environ.setdefault("SHIP_WEIGHTS", str(ROOT / "models/yolov8l-obb-dota-v1-80e/best.pt"))
sys.path.insert(0, str(ROOT))

from backend import ai_assist, ai_planner, config, storage, tiles  # noqa: E402


def make_row(image_id="img_tile_test", stored_name="tile-test.png",
             width=1024, height=768, sha="deadbeef"):
    return {"id": image_id, "width": width, "height": height,
            "stored_name": stored_name, "sha256": sha}


class TileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_dirs()
        cls.row = make_row()
        path = storage.blob_path(cls.row["stored_name"])
        path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (1024, 768), (30, 40, 50))
        image.save(path, format="PNG")
        image.close()

    def test_manifest_levels(self):
        manifest = tiles.manifest(self.row)
        self.assertEqual(manifest["tile_edge"], 256)
        self.assertEqual(manifest["max_z"], 2)          # 1024 / 256 = 4 → log2 = 2
        self.assertEqual(len(manifest["levels"]), 3)
        self.assertEqual(manifest["levels"][0]["cols"], 4)
        self.assertEqual(manifest["levels"][0]["rows"], 3)
        self.assertEqual(manifest["levels"][2]["cols"], 1)
        self.assertIn("{z}", manifest["url_template"])

    def test_read_tile_generates_then_caches(self):
        path, cached = tiles.read_tile(self.row, 0, 0, 0)
        self.assertTrue(path.is_file())
        self.assertFalse(cached)
        with Image.open(path) as tile:
            self.assertEqual(tile.size, (256, 256))
        again, cached_again = tiles.read_tile(self.row, 0, 0, 0)
        self.assertTrue(cached_again)
        self.assertEqual(again, path)

    def test_read_tile_out_of_range(self):
        with self.assertRaises(ValueError):
            tiles.read_tile(self.row, 0, 99, 0)
        with self.assertRaises(ValueError):
            tiles.read_tile(self.row, 9, 0, 0)

    def test_plan_inference_tiles_matches_algorithm(self):
        from algorithm.tiled_inference import plan_tiles

        plan = tiles.plan_inference_tiles(self.row, 512, 256)
        expected = plan_tiles(1024, 768, 512, 256)
        self.assertEqual(plan["tile_count"], len(expected))
        self.assertEqual(plan["tiles"][0], {"id": 1, "x": 0, "y": 0, "width": 512, "height": 512})
        self.assertGreater(plan["coverage"]["overlap_ratio"], 0)
        self.assertIn("未运行模型", plan["note"])

    def test_tile_etag_is_stable(self):
        first = tiles.tile_etag(self.row, 1, 2, 3)
        second = tiles.tile_etag(dict(self.row), 1, 2, 3)
        self.assertEqual(first, second)
        self.assertNotEqual(first, tiles.tile_etag(self.row, 1, 2, 4))


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self._provider = config.VLM_PROVIDER
        self._model = config.VLM_MODEL
        self._original_call = ai_planner._call_vlm

    def tearDown(self):
        config.VLM_PROVIDER = self._provider
        config.VLM_MODEL = self._model
        ai_planner._call_vlm = self._original_call

    def test_disabled_by_default(self):
        config.VLM_PROVIDER = "none"
        self.assertFalse(ai_planner.enabled())
        self.assertIsNone(ai_planner.make_gate({}))

    def test_gate_short_circuits_when_vlm_says_no(self):
        config.VLM_PROVIDER = "openai"
        record = {}
        gate = ai_planner.make_gate(record)
        self.assertIsNotNone(gate)
        ai_planner._call_vlm = lambda data_url: {"has_ship": False, "count": 0, "reason": "疑似码头"}
        self.assertEqual(gate(Image.new("RGB", (64, 64), (0, 0, 0))), "no")
        self.assertEqual(record["verdict"], "no")
        self.assertEqual(record["model"], config.VLM_MODEL)
        self.assertGreaterEqual(record["elapsed_ms"], 0)

    def test_vlm_failure_returns_none_not_error(self):
        config.VLM_PROVIDER = "openai"
        ai_planner._call_vlm = lambda data_url: None       # 超时/解析失败
        gate = ai_planner.make_gate({})
        self.assertIsNone(gate(Image.new("RGB", (32, 32), (0, 0, 0))))

    def test_parse_accepts_json_with_noise(self):
        parsed = ai_planner._parse('结论如下：{"has_ship": true, "count": 2, "reason": "两艘船"} 以上')
        self.assertEqual(parsed["has_ship"], True)
        self.assertEqual(parsed["count"], 2)
        self.assertIsNone(ai_planner._parse("抱歉，我无法判断"))
        self.assertIsNone(ai_planner._parse('{"count": 3}'))

    def test_propose_honours_gate_no(self):
        class Service:
            def predict_image(self, image, params):
                return {"detections": [], "count": 0}

        image = Image.new("RGB", (400, 300), (20, 20, 20))
        called = {}

        def gate(crop):
            called["hit"] = True
            return "no"

        result = ai_assist.propose(Service(), image, roi=[50, 50, 200, 150], mode="box", gate=gate)
        self.assertEqual(result, [])
        self.assertTrue(called.get("hit"))


if __name__ == "__main__":
    unittest.main()
