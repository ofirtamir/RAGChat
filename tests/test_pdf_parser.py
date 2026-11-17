"""Unit tests for the Hebrew fix layer in src/pdf_parser.py.

Words are (x0, y0, x1, y1, text) boxes; x grows to the right. A word's text is
*visual*: its characters left to right as they sit on the page, so a Hebrew word
is stored reversed ("םולש" for שלום). The cases below are lines found in real decisions.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402

from src.pdf_parser import _header_rows, _is_num, _line_text, _logical  # noqa: E402


def w(x0, x1, text, y0=0.0, y1=10.0):
    return (x0, y0, x1, y1, text)


def test_hebrew_words_read_right_to_left():
    assert _line_text([w(50, 90, "םלוע"), w(100, 130, "םולש")]) == "שלום עולם"


@pytest.mark.parametrize("visual, reading", [
    (")223 שרגמ(", "(מגרש 223)"),                                    # was "(מגרש )223"
    (".)11.2.2.3( .ק.ס יפל", "לפי ס.ק. (11.2.2.3)."),               # was ".)11.2.2.3("
    (":ןלהלדכ ,ןעטנ ,27.04.2026 םוימ", "מיום 27.04.2026, נטען, כדלהלן:"),  # was "2026.04.27:,נטען"
    ("]1[]ב[ 14 ףיעס", "סעיף 14 [ב][1]"),
    (".'חי 20", "20 יח'."),                                          # was "יח.'"
    ("'\"ב םירוגמ\"", "\"מגורים ב\"'"),
    ("₪ 10,400 ךסב", "בסך 10,400 ₪"),
    ("10 - 5 ןיב", "בין 5 - 10"),
    ("22% לש", "של 22%"),
    ("12×19", "19×12"),
])
def test_reading_order_of_real_lines(visual, reading):
    assert _logical(visual) == reading


def test_numbers_and_latin_keep_their_order():
    assert _logical("1,560,000") == "1,560,000"
    assert _logical("19/02/2023") == "19/02/2023"
    assert _logical("$11.6") == "$11.6"
    assert _logical("Geographer & an expert on urban planning") == "Geographer & an expert on urban planning"
    assert _logical("ביבא לתב Herzl 5 בוחרב") == "ברחוב Herzl 5 בתל אביב"
    assert _logical(")19X12(") == "(19X12)"


def test_numeric_cells():
    assert _is_num("2,061")
    assert _is_num('400 מ"ר')
    assert _is_num("--")
    assert _is_num("$11.6")
    assert not _is_num("שטח עיקרי")
    assert not _is_num('70,000 ל"י (מס שבח על הקונה)')


def test_header_is_text_row_above_numeric_body():
    grid = [
        ["מגרש", "שטח", "ייעוד", "שווי"],
        ["301", "7,705", "בית עלמין", "--"],
        ["303", "4,246", "תעשייה", "8,492,000"],
        ["סה\"כ", "29,741", "", "27,682,000"],
    ]
    hdr, body_start = _header_rows(grid)
    assert hdr == [0]
    assert body_start == 1


def test_multi_row_header_above_coefficient_rows():
    grid = [
        ["מצב חדש", "", "", ""],
        ["מקדמים", "1", "0.4", "0.5"],
        ["קומה", "שטח", "מרפסת", "סה\"כ"],
        ["קומה 1", "272.24", "38.02", "270.87"],
        ["קומה 2", "289.35", "38.23", "292.81"],
    ]
    hdr, body_start = _header_rows(grid)
    assert hdr == [2]
    assert body_start == 3


class _FakeTable:
    """Just enough of a PyMuPDF table for _build_table: one row of cells, each a bbox."""

    def __init__(self, rows):
        self.header = type("H", (), {"external": False, "cells": []})()
        self.rows = [type("R", (), {"cells": r})() for r in rows]
        self.bbox = (0, 0, 300, 10 * len(rows))


def test_text_first_row_over_numbers_becomes_the_header():
    from src.pdf_parser import _build_table

    texts = [["ח/8/1", "ח/4/1", "כתובת", "גוש-חלקה"],
             ["$300", "$220", "עמוס 22", "6019-228"],
             ["$300", "$220", "יחזקאל 11", "6019-166"]]
    rows, words = [], []
    for r, row in enumerate(texts):
        cells = []
        for c, text in enumerate(row):
            box = (c * 70, r * 10, c * 70 + 60, r * 10 + 8)
            cells.append(box)
            words.append((box[0] + 1, box[1] + 1, box[2] - 1, box[3] - 1, _logical(text)))  # visual form
        rows.append(cells)
    parsed = _build_table(_FakeTable(rows), words)
    first = parsed.markdown.splitlines()[0]
    assert "גוש-חלקה" in first and "כתובת" in first
    assert not any("without header" in p for p in parsed.problems)


def test_a_list_typed_left_to_right_keeps_its_order():
    assert _logical("211, 212, 311") == "211, 212, 311"
    assert _logical("311 ,212 ,211 תוקלח") == "חלקות 211, 212, 311"


def test_rows_cut_in_pieces_are_put_back_and_the_whole_header_is_found():
    # the building-rights table of a real decision, as PyMuPDF cuts it: the header is
    # spread over five rows and the cell "מגורים ד'" over two single-piece rows
    from src.pdf_parser import _fold_fragments

    grid = [
        ["מס'", "שטח", "", "", "שטחים עיקריים", "מס' קומות"],
        ["מגרש", "מגרש", "ייעוד", "שימוש", "מעל ±0.00", "עילי"],
        ["", "", "", "", "0.00 ±", ""],
        ["", "", "", "", "", ""],
        ["", "", "מגורים", "", "", ""],
        ["223", "2,693", "", "מגורים", "4,200", "7"],
        ["", "", "ד'", "", "", ""],
        ["321", "3,773", "מגורים ד'", "מגורים", "10,100", "12"],
        ["339", "3,191", "מגורים ד'", "מגורים", "9,200", "12"],
    ]
    spans = [[[c] for c in range(6)] for _ in grid]
    _fold_fragments(grid, spans)
    assert grid[5][2] == "מגורים ד'" and not any(grid[4]) and not any(grid[6])
    hdr, body_start = _header_rows(grid, spans)
    assert hdr == [0, 1, 2] and body_start == 5


def test_a_full_width_title_row_is_not_a_header():
    grid = [["תכנית", "פרסום", "מהות"],
            ["מצב קודם", "", ""],
            ["תמל/1087", "24.12.2018", "מגורים"]]
    spans = [[[0], [1], [2]], [[0, 1, 2], [1], [2]], [[0], [1], [2]]]
    hdr, body_start = _header_rows(grid, spans)
    assert hdr == [0] and body_start == 2


def _visual(text):
    """Reading-order text -> the word boxes it would have on a page (right to left)."""
    x, words = 500.0, []
    for token in text.split():
        words.append((x - 8 * len(token), 0.0, x, 10.0, _logical(token)))
        x -= 8 * len(token) + 4
    return words


def test_paragraphs_break_at_items_and_after_short_lines():
    from src.pdf_parser import _paragraphs

    def line(text, y):
        return [(x0, y, x1, y + 10, t) for x0, _, x1, _, t in _visual(text)]

    words = (line("1. פרטי המקרקעין", 0)
             + line("הנכס הוא דירת מגורים בקומה שלישית בבניין ותיק ברחוב נמיר", 14)
             + line("בתל אביב.", 28)
             + line("א. סביבת הנכס והפיתוח הסביבתי בשכונה", 42)
             + line("ב. המצב המשפטי והזכויות בנכס", 56))
    texts = [t for _, t in _paragraphs(words)]
    assert texts == ["1. פרטי המקרקעין",
                     "הנכס הוא דירת מגורים בקומה שלישית בבניין ותיק ברחוב נמיר בתל אביב.",
                     "א. סביבת הנכס והפיתוח הסביבתי בשכונה",
                     "ב. המצב המשפטי והזכויות בנכס"]


def test_brackets_stored_as_shapes_are_detected():
    from src.pdf_parser import _bracket_votes

    def chars(text):
        return [(i, 0, i + 1, 10, ch, 12.0, False, 1) for i, ch in enumerate(text)]

    assert _bracket_votes([chars(")223 שרגמ(")]) < 0      # stored as characters: reads right
    assert _bracket_votes([chars("(223 שרגמ)")]) > 0      # stored as drawn: must be swapped


def test_words_are_rebuilt_from_character_positions(tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    from src.pdf_parser import parse_pdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((60, 60), "Tel Aviv 5")
    page.insert_text((60, 60), "Tel Aviv 5")          # struck twice, as fake bold is
    page.insert_text((60, 90), "27.04.2026")
    path = tmp_path / "d.pdf"
    doc.save(path)
    assert parse_pdf(path)[0].markdown == "Tel Aviv 5\n\n27.04.2026"
