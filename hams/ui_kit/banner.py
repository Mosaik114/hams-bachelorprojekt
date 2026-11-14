"""The painted stage at the top of the settings: the sound track of a spoken phrase in ink and watercolour.

Tusche und Aquarell (Loratu), kept calm for a work tool. Hams turns speech into text, so the head shows what a
voice looks like on a track: words of a few syllables, each a dense bundle of swings that rise and fall, short
pauses between the words, all symmetric around a thin zero line. A soft turquoise wash follows the loudness like
wet paint around the line. Both ends fade into the paper, so the picture has no edge against title or cross.

The picture covers only the stretch between title and cross (`Stage` places it there, against the cross). Its
width comes in steps (`STEP`): while a window is resized, the stage only moves the picture and paints a new one
when the free stretch crosses a step, never for every pixel.

`render_banner` is pure PIL/numpy and deterministic (fixed seeds), so the same size always gives the same image;
`banner_image` caches the Tk image per size, mode and DPI.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageTk

from hams.ui_kit.caches import _img_cache
from hams.ui_kit.scale import Scale
from hams.ui_kit.theme import Theme

#: Opacity of the ink line at its strongest.
INK_LINE = 0.62
#: Strength of the wash (share of the accent colour) inside the loudest syllable. Small on purpose.
WASH = 0.20
#: Shortest stretch (logical pixels) worth a track; below it the stage stays plain paper.
MIN_LINE = 64
#: Longest stretch (logical pixels): a wide window keeps one short phrase near the cross.
MAX_LINE = 300
#: The picture's width comes in steps of this many logical pixels (see the module text).
STEP = 16
#: One swing of the voice, in logical pixels: the same density at every width.
PERIOD = 3.4
SUPERSAMPLE = 4

#: The phrase: words as (start, end) in 0..1 of the stretch, with pauses between them.
WORDS = ((0.02, 0.24), (0.29, 0.55), (0.60, 0.70), (0.75, 0.98))


def _rgb(value: str) -> np.ndarray:
    value = value.lstrip("#")
    return np.array([int(value[i : i + 2], 16) for i in (0, 2, 4)], np.float32)


def speech_envelope(samples: int, seed: int = 11) -> np.ndarray:
    """Loudness 0..1 over `samples` points: syllables of different stress inside words, silence in the pauses."""
    rng = np.random.default_rng(seed)
    u = np.linspace(0, 1, samples)
    envelope = np.zeros(samples)
    for start, end in WORDS:
        syllables = max(2, round((end - start) / 0.07))
        edges = np.linspace(start, end, syllables + 1)
        for left, right in zip(edges[:-1], edges[1:], strict=True):
            stress = 0.45 + 0.55 * rng.random()
            t = np.clip((u - left) / (right - left), 0, 1)
            bump = np.sin(np.pi * t) ** 1.6 * stress
            envelope = np.maximum(envelope, np.where((u >= left) & (u <= right), bump, 0.0))
    # the phrase carries most in its middle
    return envelope * (0.55 + 0.45 * np.sin(np.pi * u) ** 0.7)


def _fade(width: int, height: int) -> np.ndarray:
    """1 inside, 0 at both ends: a long soft start on the left (towards the title), a short one at the cross."""
    xs = np.linspace(0, 1, width, dtype=np.float32)[None, :]
    left = np.clip(xs / 0.18, 0, 1)
    right = np.clip((1 - xs) / 0.06, 0, 1)
    fade = (left * left * (3 - 2 * left)) * (right * right * (3 - 2 * right))
    return np.repeat(fade, height, 0)


def _voice_line(width: int, height: int, scale: float, envelope: np.ndarray) -> np.ndarray:
    """The ink line as alpha 0..1: fast swings inside the loudness, each swing a little different."""
    s = SUPERSAMPLE
    big_w, big_h = width * s, height * s
    middle, reach = 0.5 * big_h, 0.30 * big_h
    xs = np.arange(big_w, dtype=np.float64)
    period = PERIOD * scale * s
    knots = np.arange(0, big_w + period, period / 2)
    jitter = np.interp(xs, knots, 0.55 + 0.45 * np.random.default_rng(5).random(len(knots)))
    loud = np.interp(xs, np.linspace(0, big_w - 1, len(envelope)), envelope)
    ys = middle - loud * reach * jitter * np.sin(2 * np.pi * xs / period)
    mask = Image.new("L", (big_w, big_h), 0)
    ImageDraw.Draw(mask).line(
        list(zip(xs.tolist(), ys.tolist(), strict=True)), fill=255, width=max(1, round(1.0 * scale * s)), joint="curve"
    )
    return np.asarray(mask.resize((width, height), Image.LANCZOS), np.float32) / 255


def _wash(width: int, height: int, scale: float, envelope: np.ndarray) -> np.ndarray:
    """The watercolour around the line as alpha 0..1: the loudness mirrored, softened, with paper fibres in it."""
    xs = np.linspace(0, len(envelope) - 1, width)
    loud = np.interp(xs, np.arange(len(envelope)), envelope)
    rows = np.abs(np.linspace(-1, 1, height, dtype=np.float32))[:, None]
    body = (rows <= 0.66 * loud[None, :]).astype(np.float32)
    soft = Image.fromarray((body * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.6 * scale))
    body = np.asarray(soft, np.float32) / 255
    cells = np.random.default_rng(9).random((max(2, height // 6), max(2, width // 5)))
    fibres = Image.fromarray((cells * 255).astype(np.uint8)).resize((width, height), Image.BICUBIC)
    body *= 0.75 + 0.5 * np.asarray(fibres, np.float32) / 255
    # a faint glow that reaches further than the body, like paint that ran into wet paper
    glow = np.clip(1 - (rows / np.maximum(0.15 + 0.85 * loud[None, :], 1e-3)) ** 2, 0, 1)
    glow = np.asarray(Image.fromarray((glow * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(height * 0.06)))
    return body * WASH + np.asarray(glow, np.float32) / 255 * 0.07


def render_banner(width: int, height: int, *, scale: float = 1.0) -> Image.Image:
    """The track in the current palette, `width` x `height` physical pixels, as RGB on the window paper.

    The whole image is the stretch between title and cross; both ends fade into the paper. Below `MIN_LINE` it is
    plain paper.
    """
    width, height = max(1, int(width)), max(1, int(height))
    paper = _rgb(Theme.WINDOW_BG)
    image = np.empty((height, width, 3), np.float32)
    image[:] = paper
    if width >= MIN_LINE * scale:
        envelope = speech_envelope(max(64, width * 2))
        fade = _fade(width, height)
        tint = _rgb(Theme.ACCENT_TINT)
        wash_colour = tint * 0.6 + _rgb(Theme.ACCENT_SURFACE) * 0.4
        wash = np.clip(_wash(width, height, scale, envelope), 0, 1) * fade
        image = image * (1 - wash[..., None]) + wash_colour * wash[..., None]
        # the thin zero line of the track, in the pauses too
        line = _voice_line(width, height, scale, envelope)
        zero = np.zeros((height, width), np.float32)
        zero[height // 2, :] = 0.28
        ink = np.maximum(line, zero) * INK_LINE * fade
        image = image * (1 - ink[..., None]) + tint * ink[..., None]
    return Image.fromarray(np.clip(image, 0, 255).astype(np.uint8), "RGB")


def track_width(stretch: int, scale: float) -> int:
    """How wide the picture is for a free stretch of `stretch` pixels: capped and in steps, 0 when too short."""
    stretch = min(int(stretch), int(MAX_LINE * scale))
    if stretch < MIN_LINE * scale:
        return 0
    step = max(1, round(STEP * scale))
    return stretch - stretch % step


def banner_image(width: int, height: int) -> ImageTk.PhotoImage:
    """`render_banner` as a cached Tk image (per size, colour mode and DPI)."""
    key = ("banner", int(width), int(height), Theme.mode, Theme.WINDOW_BG, Scale.factor)
    cached = _img_cache.fetch(key)
    if cached is not None:
        return cached
    image = render_banner(width, height, scale=Scale.factor)
    return _img_cache.store(key, ImageTk.PhotoImage(image))
