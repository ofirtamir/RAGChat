"""Tables cut by a page break get the header of the table they continue (src/page_tables.py).
Fictional data."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import page_tables as pg  # noqa: E402

HEADER = "| דירה | קומה | שטח | שווי |"
END_OF_PAGE = f"3. השומה\n\nלהלן הנתונים:\n\n{HEADER}\n|---|---|---|---|\n| 1 | א | 80 | 1,200,000 |\n\nעמוד 5"


def page(number, markdown):
    return SimpleNamespace(number=number, markdown=markdown, tables=[])


def test_a_headerless_continuation_gets_the_previous_header():
    p1 = page(4, END_OF_PAGE)
    p2 = page(5, "עמוד 6\n\n|  |  |  |  |\n|---|---|---|---|\n| 2 | ב | 90 | 1,350,000 |\n\n4. סיכום ההכרעה")
    fixed = pg.carry_headers([p1, p2])
    assert p2.markdown.splitlines()[2] == HEADER and fixed[0]["page"] == 5
    table = SimpleNamespace(markdown="|  |  |  |  |\n|---|---|---|---|\n| 2 | ב | 90 | 1,350,000 |",
                            problems=["4 columns without header", "word coverage 80%"])
    pg.unflag([table], fixed)
    assert table.problems == ["word coverage 80%"]


def test_a_data_row_taken_for_the_header_goes_back_to_the_rows():
    p1 = page(0, END_OF_PAGE)
    p2 = page(1, "| 2 | ב | 90 | 1,350,000 |\n|---|---|---|---|\n| 3 | ג | 95 | 1,400,000 |")
    pg.carry_headers([p1, p2])
    assert p2.markdown.splitlines() == [HEADER, "|---|---|---|---|", "| 2 | ב | 90 | 1,350,000 |",
                                        "| 3 | ג | 95 | 1,400,000 |"]


def test_headers_carry_along_a_table_spanning_three_pages():
    pages = [page(0, END_OF_PAGE), page(1, "|  |  |  |  |\n|---|---|---|---|\n| 2 | ב | 90 | 1 |"),
             page(2, "|  |  |  |  |\n|---|---|---|---|\n| 3 | ג | 95 | 2 |")]
    assert len(pg.carry_headers(pages)) == 2 and pages[2].markdown.startswith(HEADER)


def test_no_header_is_borrowed_when_the_tables_are_not_the_same():
    other_width = page(1, "|  |  |  |\n|---|---|---|\n| 2 | ב | 90 |")
    own_header = page(1, "| שם | ערך | הערה | מקור |\n|---|---|---|---|\n| א | 1 | - | ב |")
    after_text = page(1, "פסקה ארוכה. " * 40 + "\n\n|  |  |  |  |\n|---|---|---|---|\n| 2 | ב | 90 | 1 |")
    for p in (other_width, own_header, after_text):
        before = p.markdown
        assert pg.carry_headers([page(0, END_OF_PAGE), p]) == [] and p.markdown == before
    prose_after = page(0, END_OF_PAGE + "\n\n" + "המשך הנימוק. " * 40)       # table does not close the page
    p = page(1, "|  |  |  |  |\n|---|---|---|---|\n| 2 | ב | 90 | 1 |")
    assert pg.carry_headers([prose_after, p]) == []
