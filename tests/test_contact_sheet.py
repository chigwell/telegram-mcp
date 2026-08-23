import io

import pytest
from PIL import Image

from telegram_mcp.contact_sheet import (
    CELL_EDGE_PIXELS,
    LABEL_STRIP_PIXELS,
    build_contact_sheet,
)


def _jpeg_bytes(width=400, height=400, colour=(200, 40, 40)):
    rendered = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(rendered, format="JPEG")
    return rendered.getvalue()


def _sheet_size(tiles, columns=None):
    with Image.open(io.BytesIO(build_contact_sheet(tiles, columns))) as sheet:
        return sheet.size


def test_single_tile_produces_a_one_by_one_grid():
    assert _sheet_size([(_jpeg_bytes(), "42")]) == (
        CELL_EDGE_PIXELS,
        CELL_EDGE_PIXELS + LABEL_STRIP_PIXELS,
    )


def test_six_tiles_wrap_into_three_columns_and_two_rows():
    tiles = [(_jpeg_bytes(), str(index)) for index in range(6)]
    assert _sheet_size(tiles) == (
        3 * CELL_EDGE_PIXELS,
        2 * (CELL_EDGE_PIXELS + LABEL_STRIP_PIXELS),
    )


def test_seven_tiles_reserve_a_third_row_for_the_remainder():
    tiles = [(_jpeg_bytes(), str(index)) for index in range(7)]
    assert _sheet_size(tiles) == (
        3 * CELL_EDGE_PIXELS,
        3 * (CELL_EDGE_PIXELS + LABEL_STRIP_PIXELS),
    )


def test_explicit_columns_override_the_automatic_layout():
    tiles = [(_jpeg_bytes(), str(index)) for index in range(6)]
    assert _sheet_size(tiles, columns=2) == (
        2 * CELL_EDGE_PIXELS,
        3 * (CELL_EDGE_PIXELS + LABEL_STRIP_PIXELS),
    )


def test_columns_never_exceed_the_number_of_tiles():
    assert _sheet_size([(_jpeg_bytes(), "solo")], columns=8) == (
        CELL_EDGE_PIXELS,
        CELL_EDGE_PIXELS + LABEL_STRIP_PIXELS,
    )


def test_wide_source_keeps_aspect_by_letterboxing_rather_than_stretching():
    sheet_bytes = build_contact_sheet([(_jpeg_bytes(800, 200, (0, 200, 0)), "wide")])
    with Image.open(io.BytesIO(sheet_bytes)) as sheet:
        top_corner = sheet.getpixel((2, 2))
        centre = sheet.getpixel((CELL_EDGE_PIXELS // 2, CELL_EDGE_PIXELS // 2))
    assert centre[1] > centre[0] and centre[1] > centre[2]
    assert top_corner != centre


def test_unreadable_tile_degrades_to_a_placeholder_instead_of_raising():
    sheet_bytes = build_contact_sheet([(b"not-an-image", "broken")])
    with Image.open(io.BytesIO(sheet_bytes)) as sheet:
        assert sheet.size == (CELL_EDGE_PIXELS, CELL_EDGE_PIXELS + LABEL_STRIP_PIXELS)


def test_label_strip_is_drawn_beneath_every_cell():
    sheet_bytes = build_contact_sheet([(_jpeg_bytes(), "1820385360168986609")])
    with Image.open(io.BytesIO(sheet_bytes)) as sheet:
        strip_row = CELL_EDGE_PIXELS + LABEL_STRIP_PIXELS // 2
        strip_pixels = [sheet.getpixel((x, strip_row)) for x in range(0, CELL_EDGE_PIXELS, 4)]
    assert any(sum(pixel) > 300 for pixel in strip_pixels)


def test_empty_tile_sequence_is_rejected():
    with pytest.raises(ValueError):
        build_contact_sheet([])


def test_output_is_decodable_jpeg():
    sheet_bytes = build_contact_sheet([(_jpeg_bytes(), "x")])
    with Image.open(io.BytesIO(sheet_bytes)) as sheet:
        assert sheet.format == "JPEG"
