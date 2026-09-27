"""Zwischenspeicher fuer gerenderte Bilder; beim Umfaerben und Umskalieren geleert."""

from __future__ import annotations

from collections import OrderedDict

from PIL import ImageTk

_img_cache: OrderedDict[tuple, ImageTk.PhotoImage] = OrderedDict()

_icon_cache: OrderedDict[tuple, ImageTk.PhotoImage] = OrderedDict()
