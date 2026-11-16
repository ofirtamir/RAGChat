"""Paid table re-read: only hard tables go to Gemini, answers are checked, costs are recorded."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402

from src import table_fallback  # noqa: E402
from src.pdf_parser import ParsedTable  # noqa: E402

LOCAL = "**מצב קודם**\n\n| מגרש | שטח |  |\n|---|---|---|\n| 301 | 7,705 | 8,492,000 |\n| 303 | 4,246 | 2,100,000 |"
GOOD = "| מגרש | שטח | שווי |\n|---|---|---|\n| 301 | 7,705 | 8,492,000 |\n| 303 | 4,246 | 2,100,000 |"
NUMBERS = ["301", "7,705", "8,492,000", "303", "4,246", "2,100,000"]


def table(problems, markdown=LOCAL, numbers=NUMBERS, bbox=(10, 10, 300, 200)):
    return ParsedTable(markdown=markdown, problems=problems, bbox=bbox, numbers=numbers)


def test_only_unusable_real_tables_are_sent():
    assert table_fallback.is_hard(table(["1 columns without header"]))
    assert table_fallback.is_hard(table(["word coverage 80%"]))
    assert table_fallback.is_hard(table(["24 columns"]))
    assert not table_fallback.is_hard(table([]))                         # parsed fine
    assert not table_fallback.is_hard(table(["1 columns"]))              # a text box, not a table
    assert not table_fallback.is_hard(table(["no data rows"]))
    assert not table_fallback.is_hard(table(["word coverage 80%"], numbers=["5"]))  # no numbers to protect
    one_row = "| א | ב |\n|---|---|\n| 1 | 2 |"
    assert not table_fallback.is_hard(table(["word coverage 80%"], markdown=one_row))


def test_number_recall_ignores_formatting():
    assert table_fallback.number_recall(GOOD, NUMBERS) == 1.0
    assert table_fallback.number_recall(GOOD.replace("8,492,000", "8492000"), NUMBERS) == 1.0
    assert table_fallback.number_recall(GOOD.replace("4,246", "4,264"), NUMBERS) < 1.0


def test_caption_is_kept_when_the_table_is_replaced():
    assert table_fallback.replace_table(LOCAL, GOOD) == "**מצב קודם**\n\n" + GOOD
    assert table_fallback.replace_table(GOOD, GOOD) == GOOD


class FakePage:
    rect = SimpleNamespace(__and__=None)


def fake_client(answer: str, inp=1200, out=300):
    def generate_content(model, contents, config):
        return SimpleNamespace(text=answer, usage_metadata=SimpleNamespace(
            prompt_token_count=inp, candidates_token_count=out, thoughts_token_count=0))
    return SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))


@pytest.fixture
def page():
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_text((50, 50), "301 7,705")
    yield p
    doc.close()


def test_a_trustworthy_answer_is_accepted_and_costed(page):
    repair = table_fallback.reread_table(page, table(["word coverage 80%"]), fake_client(f"```\n{GOOD}\n```"))
    assert repair.markdown == GOOD
    assert repair.cost_usd == pytest.approx((1200 * 0.25 + 300 * 1.50) / 1_000_000)


def test_an_answer_missing_numbers_is_rejected_but_still_costed(page):
    bad = GOOD.replace("8,492,000", "8,000,000").replace("2,100,000", "2,000,000")
    repair = table_fallback.reread_table(page, table(["word coverage 80%"]), fake_client(bad))
    assert repair.markdown == "" and repair.cost_usd > 0
    repair = table_fallback.reread_table(page, table(["word coverage 80%"]), fake_client("אין כאן טבלה"))
    assert repair.markdown == ""


def test_worker_replaces_only_hard_tables_and_reports_cost(monkeypatch, tmp_path):
    pytest.importorskip("pymupdf")
    sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
    import index_worker

    hard, fine = table(["word coverage 80%"]), table([], markdown=GOOD)
    pages = [SimpleNamespace(number=0, markdown=f"טקסט\n\n{LOCAL}\n\nעוד\n\n{GOOD}", tables=[hard, fine])]
    pdf = tmp_path / "k.pdf"
    import pymupdf
    d = pymupdf.open()
    d.new_page()
    d.save(pdf)

    monkeypatch.setattr("src.pdf_parser.parse_pdf", lambda path: pages)
    monkeypatch.setattr(table_fallback, "reread_table",
                        lambda page, t: table_fallback.Repair(GOOD.replace("שטח", "שטח מ\"ר"), 0.001, 1, 1))
    client = SimpleNamespace(download=lambda url: b"")
    result = index_worker.process({"record_key": "k", "files": [{"url": "u", "ext": "pdf"}]}, client, tmp_path)
    assert result["llm_tables"] == 1 and result["parse_cost_usd"] == pytest.approx(0.001)
    assert result["problems"] == []
    assert 'שטח מ"ר' in result["pages"][0]["markdown"] and "**מצב קודם**" in result["pages"][0]["markdown"]

    result = index_worker.process({"record_key": "k", "files": [{"url": "u", "ext": "pdf"}]}, client, tmp_path,
                                  llm_tables=False)
    assert result["llm_tables"] == 0 and result["parse_cost_usd"] == 0
    assert result["problems"] == [{"page": 0, "problems": ["word coverage 80%"]}]


def test_tables_with_no_header_at_all_are_not_sent():
    no_header = "|  |  |  |\n|---|---|---|\n| 17.4.75 | עצמון 2 | 6417-17 |\n| 31.10.72 | סיני 4 | 6417-156 |"
    assert not table_fallback.is_hard(table(["3 columns without header"], markdown=no_header))
