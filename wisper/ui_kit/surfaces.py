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
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


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

    Args:
        radius: Eckenradius. Bei `squircle=True` Superellipsen-Ecken.
        edge: Farbe einer 1 px feinen Lichtkante am oberen Rand der Form. Sie
            folgt der Rundung und ragt nie darüber hinaus, weil sie als
            Differenz der Form zu ihrer eigenen Verschiebung entsteht.
        gradient: Anteil einer weissen Aufhellung am oberen Rand (0…1). Werte
            über etwa 0,05 wirken sichtbar als Verlauf statt als Material.
        shadow: (Weichzeichnung, Versatz nach unten, Deckkraft 0…1). Der
            Schatten wird in die Bounding Box eingerechnet — die Form wird dafür
            entsprechend eingerückt, das Bild bleibt gleich gross. Nur sinnvoll,
            wenn der Untergrund bekannt ist, etwa beim Schaltknopf.
        border: Konturfarbe; `border_w` deren Stärke in physischen Pixeln.
        ring: Farbe des Fokusrings. `ring_width` und `ring_gap` reservieren den
            Platz dafür *immer*, auch wenn `ring` None ist — sonst spränge die
            Fläche in dem Moment, in dem sie den Fokus bekommt.
        dither: Sehr feines Rauschen gegen Streifenbildung im Verlauf. Liegt
            unterhalb der Wahrnehmungsschwelle und verschiebt die mittlere
            Farbe nicht.
    """
    width, height = max(1, int(width)), max(1, int(height))
    scale = SUPERSAMPLE
    big = (width * scale, height * scale)

    ring_inset = int((ring_width + ring_gap) * scale)
    pad = ring_inset
    if shadow is not None:
        blur, offset, _alpha = shadow
        pad += int(math.ceil((blur + abs(offset)) * scale))

    inner = (max(1, big[0] - 2 * pad), max(1, big[1] - 2 * pad))
    mask = _shape_mask(inner, int(radius * scale), squircle)

    canvas = Image.new("RGBA", big, (0, 0, 0, 0))

    # --- Schatten zuerst, damit die Fläche darüber liegt ---
    if shadow is not None:
        blur, offset, alpha = shadow
        shadow_layer = Image.new("RGBA", big, (0, 0, 0, 0))
        blurred = mask.filter(ImageFilter.GaussianBlur(blur * scale))
        tinted = Image.new("RGBA", inner, (0, 0, 0, int(255 * alpha)))
        shadow_layer.paste(tinted, (pad, pad + int(offset * scale)), blurred)
        canvas = Image.alpha_composite(canvas, shadow_layer)

    # --- Füllung, optional mit Mikro-Verlauf ---
    body = Image.new("RGBA", inner, _hex_to_rgb(fill) + (255,))
    if gradient > 0:
        highlight = Image.new("RGBA", inner, (255, 255, 255, 255))
        body = Image.composite(
            Image.alpha_composite(body, highlight), body,
            _vertical_ramp(inner, int(255 * gradient), 0.55),
        )

    # --- Lichtkante: Form minus verschobene Form = genau der obere Saum ---
    if edge is not None:
        edge_w = max(1, scale)                       # 1 logisches Pixel
        shifted = Image.new("L", inner, 0)
        shifted.paste(mask, (0, edge_w))
        rim = ImageChops.subtract(mask, shifted)
        rim = ImageChops.multiply(rim, _vertical_ramp(inner, 255, 0.5))
        body.paste(Image.new("RGBA", inner, _hex_to_rgb(edge) + (255,)), (0, 0), rim)

    if border is not None and border_w:
        outline = Image.new("RGBA", inner, (0, 0, 0, 0))
        draw = ImageDraw.Draw(outline)
        line = max(1, int(border_w * scale))
        if squircle:
            draw.line(
                _squircle_points(inner[0], inner[1], int(radius * scale))
                + [_squircle_points(inner[0], inner[1], int(radius * scale))[0]],
                fill=border, width=line, joint="curve",
            )
        else:
            draw.rounded_rectangle(
                [0, 0, inner[0] - 1, inner[1] - 1],
                radius=int(radius * scale), outline=border, width=line,
            )
        body = Image.alpha_composite(body, outline)

    body.putalpha(mask)
    layer = Image.new("RGBA", big, (0, 0, 0, 0))
    layer.paste(body, (pad, pad), body)
    canvas = Image.alpha_composite(canvas, layer)

    if ring is not None and ring_width:
        line = max(1, int(ring_width * scale))
        draw = ImageDraw.Draw(canvas)
        outer = [line // 2, line // 2, big[0] - 1 - line // 2, big[1] - 1 - line // 2]
        outer_radius = int((radius + ring_gap + ring_width) * scale)
        draw.rounded_rectangle(outer, radius=outer_radius, outline=ring, width=line)

    image = canvas.resize((width, height), Image.LANCZOS)

    if dither and gradient > 0:
        # Rund +-1 Helligkeitsstufe, nach dem Verkleinern aufgebracht: bricht
        # Streifenbildung, ohne als Textur sichtbar zu werden.
        # effect_noise liegt um 128; add(scale=1, offset=-128) rechnet
        # a + b - 128 und zentriert das Rauschen damit auf null.
        noise = Image.effect_noise((width, height), 1.0).convert("L")
        rgb, alpha_channel = image.convert("RGB"), image.getchannel("A")
        rgb = ImageChops.add(
            rgb, Image.merge("RGB", (noise, noise, noise)), scale=1.0, offset=-128
        )
        image = Image.merge("RGBA", (*rgb.split(), alpha_channel))

    return image


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
    key = (width, height, radius, fill, edge, round(gradient, 4), shadow,
           squircle, border, border_w, ring, ring_width, ring_gap, dither,
           Scale.factor)
    cached = _img_cache.get(key)
    if cached is not None:
        _img_cache.move_to_end(key)
        return cached

    photo = ImageTk.PhotoImage(
        render_surface(width, height, radius, fill, edge=edge, gradient=gradient,
                       shadow=shadow, squircle=squircle, border=border,
                       border_w=border_w, ring=ring, ring_width=ring_width,
                       ring_gap=ring_gap, dither=dither)
    )
    _img_cache[key] = photo
    if len(_img_cache) > CACHE_LIMIT:
        _img_cache.popitem(last=False)      # ältester Eintrag fliegt raus
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
