"""Compares two snapshot runs (`tests/test_snapshots.py`) picture by picture.

    python tools/compare_snapshots.py <before_dir> <after_dir> [--diff <dir>]

For each picture present in both folders: the share of pixels that changed by more than a small tolerance
(antialiasing and paper grain move a few levels), and the mean change. With `--diff` it also writes a
side-by-side picture (before | after | changes in red) for every picture that changed.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image

#: A pixel counts as changed above this difference (0..255, largest channel).
TOLERANCE = 12


def compare(before: Path, after: Path) -> tuple[float, float, Image.Image | None]:
    a = np.asarray(Image.open(before).convert("RGB"), np.int16)
    b = np.asarray(Image.open(after).convert("RGB"), np.int16)
    if a.shape != b.shape:
        return 100.0, 255.0, None
    delta = np.abs(a - b).max(axis=2)
    changed = delta > TOLERANCE
    mask = np.zeros_like(a, dtype=np.uint8)
    mask[changed] = (230, 40, 40)
    return float(changed.mean() * 100), float(delta.mean()), Image.fromarray(mask)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--diff", type=Path)
    args = parser.parse_args()
    names = sorted({p.name for p in args.before.glob("*.png")} & {p.name for p in args.after.glob("*.png")})
    print(f"{'picture':45} {'changed %':>10} {'mean':>6}")
    for name in names:
        share, mean, mask = compare(args.before / name, args.after / name)
        print(f"{name:45} {share:10.2f} {mean:6.2f}")
        if args.diff and share > 0 and mask is not None:
            left, right = Image.open(args.before / name).convert("RGB"), Image.open(args.after / name).convert("RGB")
            sheet = Image.new("RGB", (left.width * 3 + 20, left.height), (90, 90, 90))
            for index, image in enumerate((left, right, mask)):
                sheet.paste(image, (index * (left.width + 10), 0))
            args.diff.mkdir(parents=True, exist_ok=True)
            sheet.save(args.diff / name)
    missing = sorted({p.name for p in args.before.glob("*.png")} ^ {p.name for p in args.after.glob("*.png")})
    if missing:
        print("only in one run:", ", ".join(missing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
