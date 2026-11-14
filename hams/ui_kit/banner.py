"""The painted stage at the top of the settings: a quiet watercolour wash with one ink line like a spoken phrase.

Tusche und Aquarell (Loratu), kept deliberately calm for a work tool: no sun, no hills, no figure. The wash is
the accent colour at low strength, laid in two soft bands with the fibres of the paper; the ink line is a
voice-like wave that swells and fades. Everything fades into the window background at the bottom, so the stage
has no hard edge against the content below.

`render_banner` is pure PIL/numpy and deterministic (fixed seeds), so the same size always gives the same image;
`banner_image` caches the Tk image per size, mode and DPI.
"""

from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageTk

from hams.ui_kit.caches import _img_cache
from hams.ui_kit.scale import Scale
from hams.ui_kit.theme import Theme

#: Strength of the wash (share of the accent colour) at its deepest point. Small on purpose.
WASH = 0.14
#: Opacity of the ink line.
INK_LINE = 0.45
SUPERSAMPLE = 3


def _rgb(value: str) -> np.ndarray:
    value = value.lstrip("#")
    return np.array([int(value[i : i + 2], 16) for i in (0, 2, 4)], np.float32)


def _smooth_noise(width: int, height: int, cells: tuple[int, int], seed: int, blur: float) -> np.ndarray:
    """Low-frequency noise 0..1, the same for the same arguments."""
    rng = np.random.default_rng(seed)
    small = Image.fromarray((rng.random((cells[1], cells[0])) * 255).astype(np.uint8), "L")
    big = small.resize((width, height), Image.BICUBIC)
    if blur > 0:
        big = big.filter(ImageFilter.GaussianBlur(blur))
    return np.asarray(big, np.float32) / 255


def _smoothstep(edge0: float, edge1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - edge0) / (edge1 - edge0), 0, 1)
    return t * t * (3 - 2 * t)


def _band(width: int, height: int, top: float, amplitude: float, phase: float, soft: float) -> np.ndarray:
    """A soft watercolour band below a gentle curve (0..1), its edge frayed by noise."""
    xs = np.linspace(0, 1, width, dtype=np.float32)
    curve = top + amplitude * np.sin(xs * math.pi * 1.6 + phase) + amplitude * 0.4 * np.sin(xs * math.pi * 3.7 + phase)
    ys = np.arange(height, dtype=np.float32)[:, None] / max(1, height)
    fray = (_smooth_noise(width, height, (max(4, width // 18), 4), 7 + int(phase * 10), 1.0) - 0.5) * 0.06
    return np.clip((ys - curve[None, :] + fray) / soft + 0.5, 0, 1)


def _ink_wave(width: int, height: int, scale: float) -> Image.Image:
    """The ink line as an alpha mask: a phrase of speech that swells and fades, on the right half."""
    s = SUPERSAMPLE
    mask = Image.new("L", (width * s, height * s), 0)
    draw = ImageDraw.Draw(mask)
    start, end = 0.50 * width, 0.86 * width
    mid_y = 0.62 * height
    points = []
    steps = 360
    for i in range(steps + 1):
        u = i / steps
        x = start + (end - start) * u
        envelope = math.sin(math.pi * u) ** 1.6  # rises, carries, fades
        wobble = 0.55 * math.sin(u * 2 * math.pi * 5.0) + 0.45 * math.sin(u * 2 * math.pi * 8.3 + 0.7)
        y = mid_y - envelope * wobble * 0.30 * height
        points.append((x * s, y * s))
    line = max(1, round(1.6 * scale * s))
    draw.line(points, fill=255, width=line, joint="curve")
    for x, y in (points[0], points[-1]):  # round ends, like a brush lifted
        r = line / 2
        draw.ellipse((x - r, y - r, x + r, y + r), fill=255)
    return mask.resize((width, height), Image.LANCZOS)


def render_banner(width: int, height: int, *, scale: float = 1.0) -> Image.Image:
    """The stage in the current palette, `width` x `height` physical pixels, as RGB."""
    width, height = max(1, int(width)), max(1, int(height))
    paper = _rgb(Theme.WINDOW_BG)
    wash = _rgb(Theme.ACCENT_TINT)
    deep = _rgb(Theme.ACCENT_SURFACE)
    image = np.empty((height, width, 3), np.float32)
    image[:] = paper

    xs = np.linspace(0, 1, width, dtype=np.float32)[None, :]
    ys = np.linspace(0, 1, height, dtype=np.float32)[:, None]
    # a light wash that gathers towards the upper right, with the fibres of the paper in it
    gather = np.clip(0.35 + 0.65 * xs - 0.35 * ys, 0, 1)
    blotch = 0.75 + 0.5 * _smooth_noise(width, height, (7, 3), 21, 2.0)
    fibres = 0.9 + 0.2 * _smooth_noise(width, height, (max(8, width // 6), max(3, height // 10)), 5, 0.6)
    strength = WASH * 0.55 * gather * blotch * fibres
    # two soft bands at the bottom, like a wet edge that dried
    strength += WASH * 0.55 * _band(width, height, 0.55, 0.08, 0.6, 0.30) * blotch
    strength += WASH * 0.45 * _band(width, height, 0.74, 0.06, 2.1, 0.22) * (0.8 + 0.4 * fibres - 0.2)
    # fades out into the window below, and to plain paper where title and cross stand: no hard edges
    strength *= np.clip((1.0 - ys) / 0.35, 0, 1) ** 0.8
    strength *= _smoothstep(0.30, 0.50, xs) * (1.0 - _smoothstep(0.84, 0.93, xs))
    tint = wash * 0.6 + deep * 0.4
    image = image * (1 - strength[..., None]) + tint * strength[..., None]

    ink = np.asarray(_ink_wave(width, height, scale), np.float32)[..., None] / 255
    ink *= INK_LINE * np.clip((1.0 - ys[..., None]) / 0.25, 0, 1)
    image = image * (1 - ink) + wash * ink

    grain = np.random.default_rng(3).normal(0, 1.4, (height, width, 1)).astype(np.float32)
    image = np.clip(image + grain, 0, 255).astype(np.uint8)
    return Image.fromarray(image, "RGB")


def banner_image(width: int, height: int) -> ImageTk.PhotoImage:
    """`render_banner` as a cached Tk image (per size, colour mode and DPI)."""
    key = ("banner", int(width), int(height), Theme.mode, Theme.WINDOW_BG, Scale.factor)
    cached = _img_cache.fetch(key)
    if cached is not None:
        return cached
    return _img_cache.store(key, ImageTk.PhotoImage(render_banner(width, height, scale=Scale.factor)))
