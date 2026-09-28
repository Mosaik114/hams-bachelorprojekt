"""Runde Flaechen als anti-aliaste Bilder, mit Zwischenspeicher."""

from __future__ import annotations

import math

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageTk

from wisper.ui_kit.caches import _img_cache
from wisper.ui_kit.scale import Scale

# ---------------------------------------------------------------- Flächen-Rendering

SUPERSAMPLE = 4
"""Faktor, mit dem Formen überabgetastet gezeichnet und dann verkleinert werden.

Getrennt zu halten von `px()`: `px()` rechnet logische Maße in physische Pixel,
das Supersampling arbeitet ausschließlich innerhalb dieser Funktion und ist nach
dem Verkleinern wieder verschwunden. Beides nie miteinander multiplizieren.
"""

SQUIRCLE_EXPONENT = 4.2
"""Exponent der Superellipse. 2 wäre ein Kreisbogen, gegen unendlich eine Ecke.

4,2 ist sichtbar weicher als ein Kreisbogen, ohne dass die Ecke ausfranst.
"""

CACHE_LIMIT = 256
"""Obergrenze des Bild-Caches.

Gerechnet für den ungünstigsten Fall: eine 600 x 60 grosse RGBA-Fläche belegt
rund 140 KB, 256 Einträge also gut 35 MB. Real liegt der Verbrauch weit
darunter, weil die meisten Flächen klein sind. Ohne Grenze wüchse der Cache mit
jeder Breite, jedem Zustand und jedem DPI-Faktor unbegrenzt weiter.
"""


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _squircle_points(width: int, height: int, radius: int, steps: int = 24) -> list:
    """Umriss eines Rechtecks mit Superellipsen-Ecken.

    Nur die Ecken folgen der Superellipse; die Kanten dazwischen bleiben gerade.
    Eine Superellipse über die gesamte Fläche würde einen breiten Button in eine
    Ellipse verwandeln.
    """
    radius = max(0, min(radius, width // 2, height // 2))
    if radius == 0:
        return [(0, 0), (width - 1, 0), (width - 1, height - 1), (0, height - 1)]

    exponent = 2.0 / SQUIRCLE_EXPONENT
    quarter = [
        (radius * (math.cos(t) ** exponent), radius * (math.sin(t) ** exponent))
        for t in (i * (math.pi / 2) / (steps - 1) for i in range(steps))
    ]

    right, bottom = width - 1, height - 1
    points: list[tuple[float, float]] = []
    # oben rechts: von der oberen Kante zur rechten Kante
    points += [(right - radius + y, radius - x) for x, y in quarter]
    # unten rechts
    points += [(right - radius + x, bottom - radius + y) for x, y in quarter]
    # unten links
    points += [(radius - y, bottom - radius + x) for x, y in quarter]
    # oben links
    points += [(radius - x, radius - y) for x, y in quarter]
    return points


def _shape_mask(size: tuple[int, int], radius: int, squircle: bool) -> Image.Image:
    """Weisse Form auf schwarzem Grund — die Alpha-Maske der Fläche."""
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    if squircle:
        draw.polygon(_squircle_points(size[0], size[1], radius), fill=255)
    else:
        draw.rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius=radius, fill=255)
    return mask


def _vertical_ramp(size: tuple[int, int], top: int, stop: float) -> Image.Image:
    """Senkrechter Verlauf von `top` (oben) auf 0 bei `stop` * Höhe."""
    width, height = size
    ramp = Image.new("L", (1, height), 0)
    limit = max(1, int(height * stop))
    for y in range(limit):
        ramp.putpixel((0, y), int(top * (1.0 - y / limit)))
    return ramp.resize((width, height))


def render_surface(
    width: int,
    height: int,
    radius: int,
    fill: str,
    *,
    edge: str | None = None,
    gradient: float = 0.0,
    shadow: tuple[float, float, float] | None = None,
    squircle: bool = False,
    border: str | None = None,
    border_w: int = 0,
    ring: str | None = None,
    ring_width: int = 0,
    ring_gap: int = 0,
    dither: bool = True,
) -> Image.Image:
    """Rendert eine UI-Fläche als anti-aliastes RGBA-Bild.

    Reines PIL, ohne Tk und ohne Cache — dadurch ohne Anzeige testbar.
    Die zwischenspeichernde Tk-Variante ist `surface()`.

    Alle Masse sind **physische** Pixel — der Aufrufer hat sie bereits durch
    `px()` geschickt. Gezeichnet wird um `SUPERSAMPLE` vergrössert und danach
    mit Lanczos verkleinert; daher die weichen Kanten.

    Args (Einzelheiten bei den Hilfsfunktionen darunter):
        radius: Eckenradius. Bei `squircle=True` Superellipsen-Ecken.
        edge: feine Lichtkante oben (`_add_edge`). gradient: weisse Aufhellung oben, 0…1 (`_body`).
        shadow: (Weichzeichnung, Versatz nach unten, Deckkraft) (`_shadow_layer`).
        border, border_w: Kontur (`_add_border`). ring, ring_width, ring_gap: Fokusring (`_draw_ring`).
        dither: feines Rauschen gegen Streifen im Verlauf (`_dithered`).
    """
    width, height = max(1, int(width)), max(1, int(height))
    scale = SUPERSAMPLE
    big = (width * scale, height * scale)
    pad = _padding(shadow, ring_width, ring_gap)
    inner = (max(1, big[0] - 2 * pad), max(1, big[1] - 2 * pad))
    mask = _shape_mask(inner, int(radius * scale), squircle)

    canvas = Image.new("RGBA", big, (0, 0, 0, 0))
    if shadow is not None:  # Schatten zuerst, damit die Fläche darüber liegt
        canvas = Image.alpha_composite(canvas, _shadow_layer(big, inner, mask, pad, shadow))

    body = _body(inner, fill, gradient)
    if edge is not None:
        _add_edge(body, mask, edge)
    if border is not None and border_w:
        body = _add_border(body, int(radius * scale), squircle, border, border_w)

    body.putalpha(mask)
    layer = Image.new("RGBA", big, (0, 0, 0, 0))
    layer.paste(body, (pad, pad), body)
    canvas = Image.alpha_composite(canvas, layer)

    if ring is not None and ring_width:
        _draw_ring(canvas, radius, ring, ring_width, ring_gap)

    image = canvas.resize((width, height), Image.LANCZOS)
    return _dithered(image) if dither and gradient > 0 else image


def _padding(shadow: tuple[float, float, float] | None, ring_width: int, ring_gap: int) -> int:
    """Rand um die Form (hochgerechnet): Platz für Fokusring und Schatten."""
    pad = int((ring_width + ring_gap) * SUPERSAMPLE)
    if shadow is not None:
        blur, offset, _alpha = shadow
        pad += int(math.ceil((blur + abs(offset)) * SUPERSAMPLE))
    return pad


def _shadow_layer(
    big: tuple[int, int], inner: tuple[int, int], mask: Image.Image, pad: int, shadow: tuple[float, float, float]
) -> Image.Image:
    """Weicher Schatten der Form, um `offset` nach unten versetzt.

    Der Schatten wird in die Bounding Box eingerechnet — die Form wird dafür entsprechend eingerückt, das Bild
    bleibt gleich gross. Nur sinnvoll, wenn der Untergrund bekannt ist, etwa beim Schaltknopf."""
    blur, offset, alpha = shadow
    layer = Image.new("RGBA", big, (0, 0, 0, 0))
    blurred = mask.filter(ImageFilter.GaussianBlur(blur * SUPERSAMPLE))
    tinted = Image.new("RGBA", inner, (0, 0, 0, int(255 * alpha)))
    layer.paste(tinted, (pad, pad + int(offset * SUPERSAMPLE)), blurred)
    return layer


def _body(inner: tuple[int, int], fill: str, gradient: float) -> Image.Image:
    """Füllung, optional mit Mikro-Verlauf (weisse Aufhellung oben).

    Werte über etwa 0,05 wirken sichtbar als Verlauf statt als Material."""
    body = Image.new("RGBA", inner, _hex_to_rgb(fill) + (255,))
    if gradient <= 0:
        return body
    highlight = Image.new("RGBA", inner, (255, 255, 255, 255))
    ramp = _vertical_ramp(inner, int(255 * gradient), 0.55)
    return Image.composite(Image.alpha_composite(body, highlight), body, ramp)


def _add_edge(body: Image.Image, mask: Image.Image, edge: str) -> None:
    """Lichtkante: Form minus verschobene Form = genau der obere Saum.

    1 px fein, folgt der Rundung und ragt nie darüber hinaus – sie entsteht als Differenz der Form zu ihrer
    eigenen Verschiebung."""
    edge_w = max(1, SUPERSAMPLE)  # 1 logisches Pixel
    shifted = Image.new("L", body.size, 0)
    shifted.paste(mask, (0, edge_w))
    rim = ImageChops.subtract(mask, shifted)
    rim = ImageChops.multiply(rim, _vertical_ramp(body.size, 255, 0.5))
    body.paste(Image.new("RGBA", body.size, _hex_to_rgb(edge) + (255,)), (0, 0), rim)


def _add_border(body: Image.Image, radius: int, squircle: bool, border: str, border_w: int) -> Image.Image:
    """Kontur entlang der Form (`radius` schon hochgerechnet)."""
    width, height = body.size
    outline = Image.new("RGBA", body.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(outline)
    line = max(1, int(border_w * SUPERSAMPLE))
    if squircle:
        punkte = _squircle_points(width, height, radius)
        draw.line(punkte + [punkte[0]], fill=border, width=line, joint="curve")
    else:
        draw.rounded_rectangle([0, 0, width - 1, height - 1], radius=radius, outline=border, width=line)
    return Image.alpha_composite(body, outline)


def _draw_ring(canvas: Image.Image, radius: int, ring: str, ring_width: int, ring_gap: int) -> None:
    """Fokusring am äussersten Rand des reservierten Platzes.

    `ring_width` und `ring_gap` reservieren den Platz *immer*, auch ohne Ring – sonst spränge die Fläche in dem
    Moment, in dem sie den Fokus bekommt."""
    line = max(1, int(ring_width * SUPERSAMPLE))
    big = canvas.size
    outer = [line // 2, line // 2, big[0] - 1 - line // 2, big[1] - 1 - line // 2]
    outer_radius = int((radius + ring_gap + ring_width) * SUPERSAMPLE)
    ImageDraw.Draw(canvas).rounded_rectangle(outer, radius=outer_radius, outline=ring, width=line)


def _dithered(image: Image.Image) -> Image.Image:
    """Rund +-1 Helligkeitsstufe, nach dem Verkleinern aufgebracht: bricht Streifenbildung, ohne als Textur
    sichtbar zu werden. effect_noise liegt um 128; add(scale=1, offset=-128) rechnet a + b - 128 und zentriert
    das Rauschen damit auf null."""
    noise = Image.effect_noise(image.size, 1.0).convert("L")
    rgb, alpha_channel = image.convert("RGB"), image.getchannel("A")
    rgb = ImageChops.add(rgb, Image.merge("RGB", (noise, noise, noise)), scale=1.0, offset=-128)
    return Image.merge("RGBA", (*rgb.split(), alpha_channel))


def surface(
    width: int,
    height: int,
    radius: int,
    fill: str,
    *,
    edge: str | None = None,
    gradient: float = 0.0,
    shadow: tuple[float, float, float] | None = None,
    squircle: bool = False,
    border: str | None = None,
    border_w: int = 0,
    ring: str | None = None,
    ring_width: int = 0,
    ring_gap: int = 0,
    dither: bool = True,
) -> ImageTk.PhotoImage:
    """Wie `render_surface()`, aber als Tk-Bild und zwischengespeichert.

    Der Cache-Schlüssel enthält jeden Parameter *und* den DPI-Faktor: ein Bild
    aus 100 % darf bei 150 % nicht wiederverwendet werden. Die Grösse ist auf
    `CACHE_LIMIT` begrenzt, der älteste Eintrag fliegt heraus.
    """
    width, height = max(1, int(width)), max(1, int(height))
    optionen = {
        "edge": edge,
        "gradient": gradient,
        "shadow": shadow,
        "squircle": squircle,
        "border": border,
        "border_w": border_w,
        "ring": ring,
        "ring_width": ring_width,
        "ring_gap": ring_gap,
        "dither": dither,
    }
    key = (width, height, radius, fill, *optionen.values(), round(gradient, 4), Scale.factor)
    cached = _img_cache.get(key)
    if cached is not None:
        _img_cache.move_to_end(key)
        return cached

    photo = ImageTk.PhotoImage(render_surface(width, height, radius, fill, **optionen))
    _img_cache[key] = photo
    if len(_img_cache) > CACHE_LIMIT:
        _img_cache.popitem(last=False)  # ältester Eintrag fliegt raus
    return photo


def rounded_image(
    w: int,
    h: int,
    radius: int,
    fill: str,
    border: str | None = None,
    border_w: int = 0,
    scale: int = SUPERSAMPLE,
) -> ImageTk.PhotoImage:
    """Schlichte abgerundete Fläche — Kurzform von `surface()`.

    Bleibt erhalten, weil die vorhandenen Controls sie benutzen.
    """
    return surface(w, h, radius, fill, border=border, border_w=border_w)
