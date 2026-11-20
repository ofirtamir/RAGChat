"""Tidying pictured tables before they go into the document (src/picture_tables.py),
and borrowing the header of the table a picture continues (scripts/index_worker).
Fictional data, shaped like real readings."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import picture_tables as pt  # noqa: E402

LABEL = "*[מתוך תמונה במסמך]*"
FIRST = (f"{LABEL}\n\n| חלקת משנה | קומה | שטח | עסקה | בעלים |\n| :--- | :--- | :--- | :--- | :--- |\n"
         "| 1 | א' | 44.73 מייר | מכר | כהן | 2017 |\n| 2 | א' | 44.94 מייר | מכר | לוי | 2018 |")
CONTINUED = f"{LABEL}\n\n| 3 | ב' | 44.94 מייר | מכר | ישראלי | 2019 |\n| 4 | ב' | 44.73 מייר | ירושה | דוד | 2020 |"


def rows(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.startswith("|")]


def test_rows_wider_than_the_header_keep_every_cell():
    out = pt.tidy(FIRST)
    table = rows(out)
    assert table[0] == "| חלקת משנה | קומה | שטח | עסקה | בעלים |  |"      # padded to 6 columns
    assert table[1] == "|---|---|---|---|---|---|"
    assert table[2] == '| 1 | א\' | 44.73 מ"ר | מכר | כהן | 2017 |'
    assert out.startswith(LABEL)


def test_a_continuation_borrows_the_previous_header_or_gets_an_empty_one():
    hint = pt.last_header(pt.tidy(FIRST))
    out = rows(pt.tidy(CONTINUED, hint))
    assert out[0] == hint and out[1] == "|---|---|---|---|---|---|" and out[2].startswith("| 3 |")
    alone = rows(pt.tidy(CONTINUED, "| א | ב |"))                       # columns don't match: not borrowed
    assert alone[0] == "|  |  |  |  |  |  |" and len(alone) == 4


def test_a_data_row_promoted_to_header_is_kept_as_data():
    hint = pt.last_header(pt.tidy(FIRST))
    promoted = (f"{LABEL}\n\n| 5 | ב' | 44.73 מ\"ר | מכר | אבי | 2018 |\n| --- | --- | --- | --- | --- | --- |\n"
                "| 6 | ב' | 44.94 מ\"ר | מכר | רון | 2019 |")
    out = rows(pt.tidy(promoted, hint))
    assert out[0] == hint and out[2].startswith("| 5 |") and out[3].startswith("| 6 |")
    assert not pt.looks_like_data(["חלקת משנה", "קומה", "שטח", "2017"])
    assert pt.looks_like_data(["5", "ב'", "44.73", "2018"])


def test_text_without_tables_and_tidy_tables_are_left_alone():
    text = f"{LABEL}\n\nפסקה רגילה עם 12 מייר וקו | באמצע."
    assert pt.tidy(text) == text.replace("12 מייר", '12 מ"ר')
    good = "| א | ב |\n|---|---|\n| 1 | 2 |"
    assert pt.tidy(good) == "| א | ב |\n|---|---|\n| 1 | 2 |"
    assert pt.tidy(pt.tidy(FIRST)) == pt.tidy(FIRST)                  # idempotent
    assert pt.tidy("") == "" and pt.last_header("אין טבלה") is None


def test_the_worker_passes_the_header_of_the_table_before_the_picture(monkeypatch):
    sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
    import index_worker

    p1 = SimpleNamespace(number=0, markdown="פתיח\n\n" + pt.tidy(FIRST))
    p2 = SimpleNamespace(number=1, markdown="<!--img:1-->\n\nהמשך")
    by_number = {0: p1, 1: p2}
    assert index_worker._header_before(p2, "<!--img:1-->", by_number) == pt.last_header(pt.tidy(FIRST))
    p3 = SimpleNamespace(number=1, markdown="| x | y |\n|---|---|\n| 1 | 2 |\n\n<!--img:2-->")
    assert index_worker._header_before(p3, "<!--img:2-->", by_number) == "| x | y |"
