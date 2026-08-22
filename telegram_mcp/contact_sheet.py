"""Compose labelled image grids so one picture can index many."""

from __future__ import annotations

import io
import math
from typing import List, Optional, Sequence, Tuple

CELL_EDGE_PIXELS = 256
LABEL_STRIP_PIXELS = 26
LABEL_FONT_PIXELS = 15
GRID_BACKGROUND = (24, 24, 24)
LABEL_BACKGROUND = (0, 0, 0)
LABEL_FOREGROUND = (255, 255, 255)
MAXIMUM_COLUMNS = 4
JPEG_QUALITY = 88


class ContactSheetUnavailable(RuntimeError):
    """Raised when Pillow is not importable, so callers can degrade politely."""


def _require_pillow():
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as missing_pillow:
        raise ContactSheetUnavailable(
            "Pillow is required to build contact sheets. Install it with "
            "`pip install pillow` or `uv sync`."
        ) from missing_pillow
    return Image, ImageDraw, ImageFont


def _columns_for(tile_count: int, requested_columns: Optional[int]) -> int:
    if requested_columns and requested_columns > 0:
        return min(requested_columns, tile_count)
    return min(MAXIMUM_COLUMNS, max(1, math.ceil(math.sqrt(tile_count))))


def _fit_within_cell(image, Image):
    scale = min(CELL_EDGE_PIXELS / image.width, CELL_EDGE_PIXELS / image.height)
    width = max(1, round(image.width * scale))
    height = max(1, round(image.height * scale))
    return image.resize((width, height), Image.LANCZOS)


def _load_label_font(ImageFont):
    try:
        return ImageFont.load_default(size=LABEL_FONT_PIXELS)
    except TypeError:
        return ImageFont.load_default()


def _draw_cell(sheet, draw, image_bytes: bytes, label: str, origin, Image, ImageFont):
    left, top = origin
    try:
        with Image.open(io.BytesIO(image_bytes)) as opened:
            thumbnail = _fit_within_cell(opened.convert("RGB"), Image)
    except Exception:
        thumbnail = Image.new("RGB", (CELL_EDGE_PIXELS, CELL_EDGE_PIXELS), GRID_BACKGROUND)

    centred_left = left + (CELL_EDGE_PIXELS - thumbnail.width) // 2
    centred_top = top + (CELL_EDGE_PIXELS - thumbnail.height) // 2
    sheet.paste(thumbnail, (centred_left, centred_top))

    strip_top = top + CELL_EDGE_PIXELS
    draw.rectangle(
        [left, strip_top, left + CELL_EDGE_PIXELS, strip_top + LABEL_STRIP_PIXELS],
        fill=LABEL_BACKGROUND,
    )
    draw.text(
        (left + 5, strip_top + 5),
        label,
        font=_load_label_font(ImageFont),
        fill=LABEL_FOREGROUND,
    )


def build_contact_sheet(
    tiles: Sequence[Tuple[bytes, str]],
    columns: Optional[int] = None,
) -> bytes:
    """Lay out ``(image_bytes, label)`` pairs row-major into one labelled JPEG.

    The label under each cell is the identifier a caller passes back to open
    that single image at full resolution, which is what makes the sheet an
    index rather than a decoration.
    """
    Image, ImageDraw, ImageFont = _require_pillow()

    materialised_tiles: List[Tuple[bytes, str]] = list(tiles)
    if not materialised_tiles:
        raise ValueError("A contact sheet needs at least one tile.")

    column_count = _columns_for(len(materialised_tiles), columns)
    row_count = math.ceil(len(materialised_tiles) / column_count)
    cell_height = CELL_EDGE_PIXELS + LABEL_STRIP_PIXELS

    sheet = Image.new(
        "RGB",
        (column_count * CELL_EDGE_PIXELS, row_count * cell_height),
        GRID_BACKGROUND,
    )
    draw = ImageDraw.Draw(sheet)

    for position, (image_bytes, label) in enumerate(materialised_tiles):
        origin = (
            (position % column_count) * CELL_EDGE_PIXELS,
            (position // column_count) * cell_height,
        )
        _draw_cell(sheet, draw, image_bytes, label, origin, Image, ImageFont)

    rendered = io.BytesIO()
    sheet.save(rendered, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return rendered.getvalue()
