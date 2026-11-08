"""Fast geometry/input checks, without loading or mocking the model."""
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from algorithm.ship_detector import DetectionError, canonical_polygon, read_image


class ContractTests(unittest.TestCase):
    def test_rotated_polygon_preserves_geometry(self):
        original = [[5, 1], [9, 5], [5, 9], [1, 5]]
        for vertices in (original, original[::-1], original[2:] + original[:2]):
            self.assertEqual(canonical_polygon(vertices), original)

    def test_degenerate_or_nonfinite_polygon_rejected(self):
        for vertices in ([[1, 1]] * 4, [[0, 0], [1, 0], [1, float("nan")], [0, 1]]):
            with self.assertRaises(DetectionError):
                canonical_polygon(vertices)

    def test_rgb_jpeg_dimensions_and_invalid_formats(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            rgb = root / "中文.jpg"
            Image.new("RGB", (320, 160)).save(rgb)
            self.assertEqual(read_image(rgb).size, (320, 160))
            gray = root / "gray.png"
            Image.new("L", (10, 10)).save(gray)
            with self.assertRaises(DetectionError) as error:
                read_image(gray)
            self.assertEqual(error.exception.code, "INVALID_IMAGE")
            bad = root / "bad.png"
            bad.write_bytes(b"not a PNG")
            with self.assertRaises(DetectionError):
                read_image(bad)

    def test_missing_image(self):
        with self.assertRaises(DetectionError) as error:
            read_image("nonexistent_image.png")
        self.assertEqual(error.exception.code, "INPUT_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
