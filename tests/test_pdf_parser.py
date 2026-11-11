"""Unit tests for the Hebrew fix layer in src/pdf_parser.py.

Words are (x0, y0, x1, y1, text) boxes as PyMuPDF returns them; x grows to the right.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.pdf_parser import _fix_parens, _header_rows, _is_num, _line_text  # noqa: E402


def w(x0, x1, text, y0=0.0, y1=10.0):
    return (x0, y0, x1, y1, text)


def test_hebrew_words_read_right_to_left():
    assert _line_text([w(50, 90, "עולם"), w(100, 130, "שלום")]) == "שלום עולם"


def test_split_number_pieces_glued_left_to_right():
    # "70,000" stored as touching pieces; plain extraction yields "000 , 70"
    pieces = [w(59.7, 75, "000"), w(58.2, 59.5, ","), w(45, 58, "70")]
    assert _line_text(pieces) == "70,000"


def test_split_currency_value():
    pieces = [w(57.1, 62, "6"), w(55.1, 57, "."), w(45.1, 55, "11"), w(40, 45, "$")]
    assert _line_text(pieces) == "$11.6"


def test_number_inside_hebrew_sentence():
    assert _line_text([w(100, 120, "בסך"), w(60, 90, "10,400"), w(40, 50, "₪")]) == "בסך 10,400 ₪"


def test_sentence_final_period_moved_after_number():
    assert _line_text([w(100, 120, "מיום"), w(40, 90, ".11/11/2025")]) == "מיום 11/11/2025."


def test_mirrored_parentheses():
    assert _fix_parens(")19X12(") == "(19X12)"
    assert _fix_parens(")(במעוגל") == "(במעוגל)"
    assert _fix_parens("(תקין)") == "(תקין)"
    assert _fix_parens("1)") == "1)"


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
