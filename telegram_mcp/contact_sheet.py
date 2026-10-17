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


def _columns_for(
    tile_count: int,
    requested_columns: Optional[int],
    max_columns: int = MAXIMUM_COLUMNS,
) -> int:
    if requested_columns and requested_columns > 0:
        return min(requested_columns, tile_count)
    return min(max_columns, max(1, math.ceil(math.sqrt(tile_count))))


def _fit_within_cell(image, Image, cell_edge: int):
    scale = min(cell_edge / image.width, cell_edge / image.height)
    width = max(1, round(image.width * scale))
    height = max(1, round(image.height * scale))
    return image.resize((width, height), Image.LANCZOS)


def _load_label_font(ImageFont, font_pixels: int = LABEL_FONT_PIXELS):
    try:
        return ImageFont.load_default(size=font_pixels)
    except TypeError:
        return ImageFont.load_default()


def _draw_cell(
    sheet,
    draw,
    image_bytes: bytes,
    label: str,
    origin,
    Image,
    ImageFont,
    cell_edge: int = CELL_EDGE_PIXELS,
    label_strip: int = LABEL_STRIP_PIXELS,
    label_font_pixels: int = LABEL_FONT_PIXELS,
):
    left, top = origin
    try:
        with Image.open(io.BytesIO(image_bytes)) as opened:
            thumbnail = _fit_within_cell(opened.convert("RGB"), Image, cell_edge)
    except Exception:
        thumbnail = Image.new("RGB", (cell_edge, cell_edge), GRID_BACKGROUND)

    centred_left = left + (cell_edge - thumbnail.width) // 2
    centred_top = top + (cell_edge - thumbnail.height) // 2
    sheet.paste(thumbnail, (centred_left, centred_top))

    strip_top = top + cell_edge
    draw.rectangle(
        [left, strip_top, left + cell_edge, strip_top + label_strip],
        fill=LABEL_BACKGROUND,
    )
    draw.text(
        (left + 5, strip_top + 3),
        label,
        font=_load_label_font(ImageFont, label_font_pixels),
        fill=LABEL_FOREGROUND,
    )


def build_contact_sheet(
    tiles: Sequence[Tuple[bytes, str]],
    columns: Optional[int] = None,
    cell_edge: int = CELL_EDGE_PIXELS,
    label_strip: int = LABEL_STRIP_PIXELS,
    label_font_pixels: int = LABEL_FONT_PIXELS,
    max_columns: int = MAXIMUM_COLUMNS,
) -> bytes:
    """Lay out ``(image_bytes, label)`` pairs row-major into one labelled JPEG.

    The label under each cell is the identifier a caller passes back to open
    that single image at full resolution, which is what makes the sheet an
    index rather than a decoration.

    ``cell_edge``, ``label_strip`` and ``label_font_pixels`` let a caller trade
    legibility for tile count, and ``max_columns`` raises the automatic wrap
    width for dense sheets such as sticker packs.
    """
    Image, ImageDraw, ImageFont = _require_pillow()

    materialised_tiles: List[Tuple[bytes, str]] = list(tiles)
    if not materialised_tiles:
        raise ValueError("A contact sheet needs at least one tile.")

    column_count = _columns_for(len(materialised_tiles), columns, max_columns)
    row_count = math.ceil(len(materialised_tiles) / column_count)
    cell_height = cell_edge + label_strip

    sheet = Image.new(
        "RGB",
        (column_count * cell_edge, row_count * cell_height),
        GRID_BACKGROUND,
    )
    draw = ImageDraw.Draw(sheet)

    for position, (image_bytes, label) in enumerate(materialised_tiles):
        origin = (
            (position % column_count) * cell_edge,
            (position // column_count) * cell_height,
        )
        _draw_cell(
            sheet,
            draw,
            image_bytes,
            label,
            origin,
            Image,
            ImageFont,
            cell_edge=cell_edge,
            label_strip=label_strip,
            label_font_pixels=label_font_pixels,
        )

    rendered = io.BytesIO()
    sheet.save(rendered, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return rendered.getvalue()
