"""Generate a synthetic harbour scene and a contract-shaped ship-detection/0.2 payload.

Reuses the repository's real plan_tiles() and canonical_polygon() so the demo data
obeys exactly the same tiling and polygon-ordering rules as the Python module.

The scene deliberately contains the failure modes the module documentation describes:
  - thin boundary fragments that survive NMS (IoU < merge_iou) and double-count a ship
  - detections straddling a tile seam
  - a craft touching the image border
  - small craft below the practical recall limit
"""
import hashlib
import json
import math
import random
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
FRONTEND = HERE.parent
ROOT = FRONTEND.parent
REPO = ROOT / "branches" / "codex_ship-tiled-inference"
SHIM = ROOT / "analysis"
sys.path[:0] = [str(SHIM), str(REPO)]

from algorithm.tiled_inference import plan_tiles          # noqa: E402
from algorithm.ship_detector import canonical_polygon     # noqa: E402

W, H = 2305, 1537
TILE, STRIDE = 1024, 512
random.seed(20261027)
np.random.seed(20261027)

LAND = [((0, 1180), (980, 1537)), ((1460, 0), (2305, 250)), ((1000, 1042), (1186, 1180))]


def corners(cx, cy, length, width, theta_deg):
    t = math.radians(theta_deg)
    ux, uy = math.cos(t), math.sin(t)
    vx, vy = -uy, ux
    hl, hw = length / 2, width / 2
    return [[round(px, 2), round(py, 2)] for px, py in (
        (cx - ux * hl - vx * hw, cy - uy * hl - vy * hw),
        (cx + ux * hl - vx * hw, cy + uy * hl - vy * hw),
        (cx + ux * hl + vx * hw, cy + uy * hl + vy * hw),
        (cx - ux * hl + vx * hw, cy - uy * hl + vy * hw),
    )]


def build_ships():
    ships = []

    def add(cx, cy, length, width, theta, kind, detected=True):
        ships.append(dict(cx=round(cx, 2), cy=round(cy, 2), length=length, width=width,
                          theta=theta, kind=kind, detected=detected))

    # A. moored perpendicular to the bottom quay — dense parallel rows
    x = 58.0
    idx = 0
    while x < 560:
        idx += 1
        length = random.uniform(58, 96)
        add(x, 1176 - length / 2 - random.uniform(1, 4), length, random.uniform(11, 14),
            90 + random.uniform(-1.5, 1.5), "moored", detected=idx not in (4, 17))
        x += random.uniform(21, 27)

    # B. moored along the top quay, parallel to it
    x = 1500.0
    while x < 2260:
        length = random.uniform(64, 110)
        add(x, 262 + length / 2, length, random.uniform(13, 17),
            0 + random.uniform(-2, 2), "moored")
        x += length + random.uniform(14, 30)

    # C. vessels in open water
    for _ in range(11):
        length = random.uniform(46, 140)
        add(random.uniform(160, W - 200), random.uniform(380, 980), length,
            length / random.uniform(4.2, 6.0), random.uniform(0, 360), "moving")

    # D. small craft — the practical recall limit
    for i, (cx, cy) in enumerate(((700, 900), (860, 640), (1180, 780), (1330, 420))):
        length = random.uniform(13, 21)
        add(cx, cy, length, length / 3.4, random.uniform(0, 180), "small",
            detected=i < 2)

    # E. long vessels straddling tile seams (each mostly inside one tile, hugging its edge)
    add(560.0, 760.0, 160, 26, 6, "seam")
    add(1060.0, 980.0, 150, 24, -8, "seam")
    #    small craft sitting exactly on a seam: the classic fragmentation case
    add(1024.0, 700.0, 24, 8, 25, "seam")
    add(512.0, 620.0, 26, 9, -15, "seam")

    # F. a craft touching the left image border (detected)
    add(11.0, 700.0, 22, 7, 12, "edge")

    for i, s in enumerate(ships, 1):
        s["id"] = i
        s["polygon"] = canonical_polygon(corners(s["cx"], s["cy"], s["length"], s["width"], s["theta"]))
    return ships


def render(ships):
    low = np.random.rand(max(1, H // 40), max(1, W // 40)).astype(np.float32)
    low = np.asarray(Image.fromarray((low * 255).astype(np.uint8)).resize((W, H), Image.BICUBIC),
                     dtype=np.float32) / 255.0
    fine = np.random.normal(0, 3.2, (H, W)).astype(np.float32)
    base = np.zeros((H, W, 3), dtype=np.float32)
    for ch, colour in enumerate((16, 40, 56)):
        base[:, :, ch] = colour + low * 22 + fine
    img = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8), "RGB")
    draw = ImageDraw.Draw(img, "RGBA")

    for (x0, y0), (x1, y1) in LAND:
        draw.rectangle([x0, y0, x1, y1], fill=(84, 86, 82, 255))
        draw.rectangle([x0, y0, x1, y0 + 6], fill=(120, 122, 116, 255))
        palette = [(150, 62, 46), (44, 86, 130), (58, 110, 74), (140, 120, 60), (110, 112, 116)]
        cy = y0 + 14
        while cy < y1 - 26:
            cx = x0 + 10
            while cx < x1 - 40:
                bw, bh = random.uniform(16, 30), random.uniform(8, 13)
                draw.rectangle([cx, cy, cx + bw, cy + bh], fill=random.choice(palette) + (255,))
                cx += bw + random.uniform(3, 8)
            cy += random.uniform(13, 20)

    for s in ships:
        pts = [tuple(p) for p in s["polygon"]]
        draw.polygon([(x + 2.5, y + 2.5) for x, y in pts], fill=(6, 14, 20, 150))
        draw.polygon(pts, fill=(226, 231, 234, 255), outline=(150, 158, 164, 255))
        inner = corners(s["cx"], s["cy"], s["length"] * 0.55, s["width"] * 0.5, s["theta"])
        draw.polygon([tuple(p) for p in inner], fill=(158, 168, 176, 255))
        if s["kind"] in ("moving", "seam", "edge") and s["length"] > 70:
            t = math.radians(s["theta"])
            for k in (1.25, 1.55):
                draw.line([(s["cx"] - math.cos(t) * s["length"] * k, s["cy"] - math.sin(t) * s["length"] * k),
                           (s["cx"] - math.cos(t) * s["length"] * (k + 0.28),
                            s["cy"] - math.sin(t) * s["length"] * (k + 0.28))],
                          fill=(120, 150, 168, 90), width=5)

    img = img.filter(ImageFilter.GaussianBlur(0.55))
    FRONTEND.joinpath("assets").mkdir(parents=True, exist_ok=True)
    out = FRONTEND / "assets" / "harbor.png"
    img.save(out, optimize=True)
    return out


def bbox(p):
    xs = [q[0] for q in p]
    ys = [q[1] for q in p]
    return min(xs), min(ys), max(xs), max(ys)


def overlap_area(a, b):
    ax0, ay0, ax1, ay1 = bbox(a)
    bx0, by0, bx1, by1 = bbox(b)
    return max(0.0, min(ax1, bx1) - max(ax0, bx0)) * max(0.0, min(ay1, by1) - max(ay0, by0))


def main():
    ships = build_ships()
    image_path = render(ships)
    sha = hashlib.sha256(image_path.read_bytes()).hexdigest()

    tiles = plan_tiles(W, H, TILE, STRIDE)
    tile_records = [{"id": i, "x": t[0], "y": t[1], "width": t[2] - t[0], "height": t[3] - t[1]}
                    for i, t in enumerate(tiles, 1)]

    def owner_tile(poly):
        best, best_area = 1, -1.0
        for rec in tile_records:
            box = [[rec["x"], rec["y"]], [rec["x"] + rec["width"], rec["y"]],
                   [rec["x"] + rec["width"], rec["y"] + rec["height"]], [rec["x"], rec["y"] + rec["height"]]]
            area = overlap_area(poly, box)
            if area > best_area:
                best, best_area = rec["id"], area
        return best

    detections = []

    def push(poly, conf, note=""):
        detections.append({"id": 0, "polygon": poly, "class_id": 1, "class_name": "ship",
                           "confidence": round(conf, 4), "source_tile_id": owner_tile(poly)})

    for s in ships:
        if not s["detected"]:
            continue
        cx = s["cx"] + random.uniform(-3.2, 3.2)
        cy = s["cy"] + random.uniform(-3.2, 3.2)
        poly = canonical_polygon(corners(cx, cy, s["length"] * random.uniform(0.94, 1.06),
                                         s["width"] * random.uniform(0.92, 1.08),
                                         s["theta"] + random.uniform(-2.0, 2.0)))
        conf = random.uniform(0.62, 0.95) if s["kind"] != "small" else random.uniform(0.36, 0.5)
        push(poly, conf)

    # boundary fragments: thin slivers sitting on a tile seam whose IoU with the parent hull
    # stays under merge_iou=0.1, so polygon NMS keeps both and the same hull is counted twice.
    from algorithm.tiled_inference import polygon_iou
    fragments = []
    for s, seam_x in zip([x for x in ships if x["kind"] == "seam" and x["length"] > 100], (512.0, 1024.0)):
        t = math.radians(s["theta"])
        d = (seam_x - s["cx"]) / math.cos(t)
        fx, fy = s["cx"] + math.cos(t) * d, s["cy"] + math.sin(t) * d
        frag = canonical_polygon(corners(fx, fy, 14, s["width"] * 0.9, s["theta"]))
        fragments.append((frag, s["polygon"], random.uniform(0.42, 0.58)))
        push(frag, fragments[-1][2])

    # false positives on quay structures and open water
    for cx, cy, length, width, theta, conf in (
            (300, 1290, 44, 12, 12, 0.41), (760, 1330, 58, 14, -6, 0.38),
            (1700, 150, 52, 13, 4, 0.47), (2050, 190, 40, 11, -11, 0.33),
            (640, 430, 36, 10, 63, 0.31), (1420, 890, 30, 9, 118, 0.36)):
        push(canonical_polygon(corners(cx, cy, length, width, theta)), conf)

    detections.sort(key=lambda d: -d["confidence"])
    for i, d in enumerate(detections, 1):
        d["id"] = i

    for rec in tile_records:
        box = [[rec["x"], rec["y"]], [rec["x"] + rec["width"], rec["y"]],
               [rec["x"] + rec["width"], rec["y"] + rec["height"]], [rec["x"], rec["y"] + rec["height"]]]
        kept = sum(1 for d in detections if d["source_tile_id"] == rec["id"])
        touched = sum(1 for d in detections if overlap_area(d["polygon"], box) > 0)
        rec["candidate_count"] = max(kept, touched) + random.randint(0, 3)
        rec["predict_ms"] = round(random.uniform(48, 96), 1)
        rec["kept"] = kept

    candidates = sum(r["candidate_count"] for r in tile_records)
    final = len(detections)
    missed = [{"id": s["id"], "polygon": s["polygon"], "length": round(s["length"], 1),
               "width": round(s["width"], 1), "kind": s["kind"]} for s in ships if not s["detected"]]

    payload = {
        "schema_version": "ship-detection/0.2",
        "created_at": "2026-10-29T09:44:25+00:00",
        "image": {"file_name": "harbor.png", "sha256": sha, "width": W, "height": H, "mode": "RGB"},
        "model": {"backend": "ultralytics", "task": "obb", "weights_file": "best.pt",
                  "architecture": "OBBModel",
                  "weights_sha256": "6508d583f538e858d498335c0fb6efd24c9e5116823eb4152f6d6979193cd26f",
                  "checkpoint_ultralytics_version": "8.1.5", "ultralytics_version": "8.4.189",
                  "torch_version": "2.9.1+cu128", "device": "0",
                  "device_name": "RTX 5060 Laptop 8GB"},
        "parameters": {"conf": 0.25, "iou": 0.7, "imgsz": 1024, "max_det": 1000, "classes": [1],
                       "half": False, "augment": False, "tiling": True,
                       "tile_size": TILE, "stride": STRIDE, "merge_iou": 0.1},
        "coordinates": {"unit": "pixel", "origin": "top-left", "x_axis": "right", "y_axis": "down",
                        "polygon_order": "clockwise; start=min(y,x)", "space": "original_image",
                        "clipped": False},
        "tiling": {"tile_count": len(tile_records), "candidate_count": candidates,
                   "suppressed_count": candidates - final,
                   "merge_method": "class_aware_polygon_nms", "tiles": tile_records},
        "count": final,
        "detections": detections,
        "scene": {"ships": [{"id": s["id"], "polygon": s["polygon"], "length": round(s["length"], 1),
                             "width": round(s["width"], 1), "kind": s["kind"]} for s in ships],
                  "missed": missed},
        "ground_truth_count": len(ships),
    }

    (FRONTEND / "data.js").write_text(
        "window.SCENE = " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";\n",
        encoding="utf-8")
    print(f"image    {image_path.name} {image_path.stat().st_size/1024:.0f} KB {W}x{H}")
    print(f"ships    {len(ships)}  detected {len(ships)-len(missed)}  missed {len(missed)}")
    print(f"detects  {final} (incl. {len(fragments)} fragments, 6 false positives)")
    print(f"tiles    {len(tile_records)}  candidates {candidates} -> {final}")
    worst = max(polygon_iou(f, p) for f, p, _ in fragments)
    print(f"fragment IoU vs parent  max {worst:.4f}  (merge_iou {payload['parameters']['merge_iou']}) "
          f"{'OK - survives NMS' if worst < payload['parameters']['merge_iou'] else 'INCONSISTENT'}")


if __name__ == "__main__":
    main()
