"""Select local DOTA validation samples. No dataset download or redistribution."""
import argparse
import json
import math
from pathlib import Path
import shutil
from PIL import Image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, help="DOTA split containing images/ and annfiles_v15/")
    parser.add_argument("--output", type=Path, default=Path("datasets/smoke"))
    args = parser.parse_args()
    selected = {}
    for label in sorted((args.dataset / "annfiles_v15").glob("*.txt")):
        ships = []
        for line in label.read_text(encoding="utf-8-sig").splitlines():
            fields = line.split()
            if len(fields) >= 10 and fields[8] == "ship":
                ships.append((list(map(float, fields[:8])), fields[9]))
        image = args.dataset / "images" / (label.stem + ".png")
        if not image.is_file():
            continue
        with Image.open(image) as im:
            if im.mode != "RGB":
                continue
        kind = None
        if "empty" not in selected and not ships:
            kind = "empty"
        elif "dense" not in selected and len(ships) >= 30 and all(d != "2" for _, d in ships):
            kind = "dense"
        elif "small" not in selected and 1 <= len(ships) <= 10:
            if any(d != "2" and min(math.dist(p[:2], p[2:4]), math.dist(p[2:4], p[4:6])) <= 12 for p, d in ships):
                kind = "small"
        if kind:
            selected[kind] = {"source_image": str(image.resolve()), "source_label": str(label.resolve()),
                              "annotated_ship_count": len(ships)}
        if len(selected) == 3:
            break
    if len(selected) != 3:
        raise SystemExit("Could not find all three sample categories")
    args.output.mkdir(parents=True, exist_ok=True)
    for kind, info in selected.items():
        shutil.copy2(info["source_image"], args.output / f"{kind}.png")
        shutil.copy2(info["source_label"], args.output / f"{kind}.txt")
    # JPEG and non-square input exercise decoding and letterbox coordinate restoration.
    with Image.open(args.output / "dense.png") as image:
        image.crop((0, image.height // 2, image.width, image.height)).save(args.output / "dense_nonsquare.jpg", quality=95)
    manifest = {"source": "Local DOTA-v1.5 validation patches", "license": "Academic use only; obey source imagery terms",
                "source_url": "https://captain-whu.github.io/DOTA/dataset.html",
                "purpose": "Pipeline smoke checks; training overlap is not excluded; not an accuracy benchmark",
                "samples": selected,
                "derived": {"dense_nonsquare.jpg": "bottom half of dense.png, JPEG quality 95; source y offset=512"}}
    (args.output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
