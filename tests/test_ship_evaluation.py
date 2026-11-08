from pathlib import Path
import json
import tempfile
import unittest

from scripts.evaluate_ship_obb import match_detections, metrics, read_manifest, validate_labels


def rectangle(left):
    return [[left, 1], [left + 10, 1], [left + 10, 9], [left, 9]]


class ShipEvaluationTests(unittest.TestCase):
    def test_one_to_one_matching_distinguishes_duplicates(self):
        labels = [rectangle(0), rectangle(20), rectangle(40)]
        detections = [
            {"polygon": rectangle(0), "confidence": 0.4},
            {"polygon": rectangle(0), "confidence": 0.9},
            {"polygon": rectangle(20), "confidence": 0.7},
            {"polygon": rectangle(70), "confidence": 0.6},
        ]
        self.assertEqual(match_detections(detections, labels, 0.5),
                         {"tp": 2, "fp": 2, "fn": 1, "duplicate_fp": 1})

    def test_empty_images_have_defined_counts_and_undefined_rates(self):
        counts = metrics(match_detections([], [], 0.5))
        self.assertEqual(counts, {"tp": 0, "fp": 0, "fn": 0, "duplicate_fp": 0,
                                  "precision": None, "recall": None})
        detection = {"polygon": rectangle(0), "confidence": 0.8}
        self.assertEqual(match_detections([detection], [], 0.5)["fp"], 1)
        self.assertEqual(match_detections([], [rectangle(0)], 0.5)["fn"], 1)

    def test_original_coordinates_validate_rotated_boxes(self):
        rotated = [[1, 5], [3, 1], [10, 4], [8, 8]]
        self.assertEqual(validate_labels([rotated], 11, 9)[0][0], [3.0, 1.0])
        with self.assertRaisesRegex(ValueError, "outside original image"):
            validate_labels([rotated], 9, 9)
        with self.assertRaisesRegex(ValueError, "convex"):
            validate_labels([[[0, 0], [4, 0], [3, 1], [4, 4]]], 9, 9)

    def test_manifest_does_not_treat_missing_labels_as_negative(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "manifest.json"
            manifest = {"source": "synthetic", "split": "test", "independence": "test only",
                        "samples": [{"image": "empty.png"}]}
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "explicit ships list"):
                read_manifest(path)
            manifest["samples"][0]["ships"] = []
            path.write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(read_manifest(path), manifest)
