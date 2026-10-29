"""Geometry and orchestration checks; scripted outputs are NOT model evidence."""
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from algorithm.ship_detector import DetectionError, MAX_PIXELS, ShipDetector, read_image, save_result
from algorithm.tiled_inference import plan_tiles, polygon_iou, polygon_nms, validate_tiling


def detection(points, score=0.9, cls=1):
    return dict(id=1, polygon=points, confidence=score, class_id=cls, class_name="ship")


class ScriptedDetector(ShipDetector):
    def __init__(self, batches):
        self.batches = iter(batches)
        self.metadata, self.ship_ids, self.load_ms = {"test_double": True}, [1], 0

    def _infer_image(self, image, **kwargs):
        return next(self.batches), {}


class TiledTests(unittest.TestCase):
    def test_full_coverage_odd_nonsquare_and_small_images(self):
        for width, height in ((2305, 1537), (37, 19), (2048, 1024), (1025, 1025)):
            tiles = plan_tiles(width, height, 1024, 512)
            coverage = np.zeros((height, width), dtype=bool)
            for x, y, right, bottom in tiles:
                self.assertTrue(0 <= x < right <= width and 0 <= y < bottom <= height)
                coverage[y:bottom, x:right] = True
            self.assertTrue(coverage.all())
            self.assertEqual(len(tiles), len(set(tiles)))
            self.assertEqual(tiles[-1][2:], (width, height))

    def test_no_overlap_and_tile_budget(self):
        self.assertEqual(plan_tiles(2048, 1024, 1024, 1024), [(0, 0, 1024, 1024), (1024, 0, 2048, 1024)])
        with self.assertRaises(DetectionError) as error:
            plan_tiles(10000, 10000, 32, 32)
        self.assertEqual(error.exception.code, "TILE_LIMIT")

    def test_invalid_tiling_options(self):
        for values in ((0, 1, .1), (True, 1, .1), (1024, 0, .1), (1024, 1025, .1),
                       (1024, 1.2, .1), (1024, 512, float("nan")), (1024, 512, 0),
                       (1024, 512, None), (1024, 512, True)):
            with self.subTest(values=values), self.assertRaises(DetectionError):
                validate_tiling(*values)

    def test_polygon_iou_translation_and_rotation(self):
        diamond = np.array([[5, 0], [10, 5], [5, 10], [0, 5]], dtype=float)
        self.assertAlmostEqual(polygon_iou(diamond, diamond[::-1]), 1)
        self.assertEqual(polygon_iou(diamond, diamond + 50), 0)
        self.assertAlmostEqual(polygon_iou(diamond + 1000000, diamond + 1000000), 1)
        self.assertAlmostEqual(polygon_iou([[0, 0], [10, 0], [10, 10], [0, 10]],
                                           [[5, 0], [15, 0], [15, 10], [5, 10]]), 1/3)

    def test_nms_keeps_dense_rotated_neighbors_with_overlapping_aabbs(self):
        # Parallel thin boxes: enclosing rectangles overlap, polygons are disjoint.
        a = [[0, 0], [10, 10], [9, 11], [-1, 1]]
        b = [[0, 3], [10, 13], [9, 14], [-1, 4]]
        self.assertEqual(polygon_iou(a, b), 0)
        self.assertEqual(len(polygon_nms([detection(a), detection(b)], .1)), 2)

    def test_nms_duplicate_classes_and_stable_ties(self):
        box = [[0, 0], [10, 0], [10, 10], [0, 10]]
        inputs = [dict(detection(box, .8), source_tile_id=1),
                  dict(detection(box, .9), source_tile_id=2), detection(box, .7, cls=2)]
        kept = polygon_nms(inputs, .1)
        self.assertEqual([d["id"] for d in kept], [1, 2])
        self.assertEqual(kept[0]["source_tile_id"], 2)
        self.assertEqual(len(polygon_nms(inputs, 1)), 3)  # strict IoU > threshold
        tied = [dict(detection(box), source_tile_id=i) for i in (8, 3)]
        self.assertEqual(polygon_nms(tied, .1)[0]["source_tile_id"], 8)
        self.assertEqual(polygon_nms([], .1), [])

    def test_global_coordinate_restore_merge_and_save(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "odd.png"
            Image.new("RGB", (96, 64)).save(path)
            # Windows x=0 and x=32 observe the SAME object at different local positions.
            global_box = [[40, 20], [50, 20], [50, 30], [40, 30]]
            local_box = [[x - 32, y] for x, y in global_box]
            detector = ScriptedDetector([[detection(global_box, .8)], [detection(local_box, .9)]])
            result = detector.predict_large(path, tile_size=64, stride=32)
            self.assertEqual(result["detections"][0]["polygon"], global_box)
            self.assertEqual(result["detections"][0]["source_tile_id"], 2)
            self.assertEqual(result["count"], 1)
            self.assertEqual(result["tiling"]["candidate_count"], 2)
            self.assertEqual(result["tiling"]["suppressed_count"], 1)
            paths = save_result(result, path, Path(folder) / "out")
            self.assertEqual(json.loads(Path(paths["result"]).read_text())["count"], 1)
            with Image.open(paths["preview"]) as preview:
                self.assertEqual(preview.size, (96, 64))

    def test_empty_truncation_warning_and_candidate_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "small.png"
            Image.new("RGB", (32, 32)).save(path)
            result = ScriptedDetector([[]]).predict_large(path)
            self.assertEqual(result["count"], 0)
            self.assertEqual(result["tiling"]["tile_count"], 1)
            box = detection([[1, 1], [10, 1], [10, 10], [1, 10]])
            result = ScriptedDetector([[box]]).predict_large(path, max_det=1)
            self.assertTrue(any("MAX_DET_REACHED" in w for w in result["warnings"]))
            with patch("algorithm.tiled_inference.MAX_CANDIDATES", 1):
                with self.assertRaises(DetectionError) as error:
                    ScriptedDetector([[box, box]]).predict_large(path)
            self.assertEqual(error.exception.code, "CANDIDATE_LIMIT")

    def test_large_mode_exceeds_old_pixel_limit_and_enforces_new_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "large.png"
            Image.new("RGB", (4097, 4097)).save(path)
            self.assertGreater(4097 * 4097, MAX_PIXELS)
            with self.assertRaises(DetectionError) as error:
                read_image(path)
            self.assertEqual(error.exception.code, "IMAGE_TOO_LARGE")
            result = ScriptedDetector([[] for _ in range(9)]).predict_large(path, tile_size=2048, stride=2048)
            self.assertEqual(result["tiling"]["tile_count"], 9)
            with patch("algorithm.tiled_inference.MAX_LARGE_PIXELS", 100):
                with self.assertRaises(DetectionError) as error:
                    ScriptedDetector([]).predict_large(path)
            self.assertEqual(error.exception.code, "IMAGE_TOO_LARGE")

    def test_input_changed_during_inference(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "small.png"
            Image.new("RGB", (32, 32)).save(path)
            with patch("algorithm.tiled_inference.sha256", side_effect=["old", "new"]):
                with self.assertRaises(DetectionError) as error:
                    ScriptedDetector([[]]).predict_large(path)
            self.assertEqual(error.exception.code, "IMAGE_CHANGED")

    def test_mid_job_failure_returns_no_partial_success(self):
        class FailingDetector(ScriptedDetector):
            def _infer_image(self, image, **kwargs):
                result = next(self.batches)
                if result is None:
                    raise DetectionError("OUT_OF_MEMORY", "scripted failure")
                return result, {}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "image.png"
            Image.new("RGB", (96, 64)).save(path)
            with self.assertRaises(DetectionError) as error:
                FailingDetector([[], None]).predict_large(path, tile_size=64, stride=32)
            self.assertEqual(error.exception.code, "OUT_OF_MEMORY")
            self.assertIn("Tile 2", str(error.exception))
            self.assertFalse((Path(folder) / "result.json").exists())

    def test_invalid_model_parameters(self):
        for options in ({"conf": None}, {"conf": True}, {"iou": float("inf")},
                        {"imgsz": 31}, {"max_det": 0}):
            with self.subTest(options=options), self.assertRaises(DetectionError) as error:
                ScriptedDetector([]).predict_large("unused.png", **options)
            self.assertEqual(error.exception.code, "INVALID_ARGUMENT")

    def test_module_cli_structured_errors_in_subprocess(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "image.png"
            Image.new("RGB", (32, 32)).save(path)
            for image, flags, code in (
                (str(path), ["--tiled", "--stride", "0"], "INVALID_ARGUMENT"),
                (str(path), ["--tile-size", "64"], "INVALID_ARGUMENT"),
                (str(Path(folder) / "missing.png"), ["--tiled"], "INPUT_NOT_FOUND"),
                (str(path), ["--tiled", "--weights", str(Path(folder) / "missing.pt")], "WEIGHTS_NOT_FOUND"),
            ):
                with self.subTest(code=code, flags=flags):
                    result = subprocess.run([sys.executable, "-m", "algorithm.ship_detector", image, *flags],
                                            cwd=Path(__file__).resolve().parents[1], capture_output=True,
                                            text=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertEqual(json.loads(result.stderr)["error"]["code"], code)


if __name__ == "__main__":
    unittest.main()
