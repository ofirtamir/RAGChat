"""Pictured tables/text: free filter, markers in place, cache, limits; and the Windows-1255 Hebrew fix."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402

pymupdf = pytest.importorskip("pymupdf")
np = pytest.importorskip("numpy")

from src import image_reader  # noqa: E402
from src.pdf_parser import _fix_hebrew, parse_pdf, strip_image_markers  # noqa: E402


def test_hebrew_stored_as_windows_1255_is_restored():
    assert _fix_hebrew("בðיה") == "בניה"
    assert _fix_hebrew("ðטו") == "נטו"
    assert _fix_hebrew("café") == "café"          # real Latin text is left alone
    assert _fix_hebrew("בניה") == "בניה"


def _picture(kind: str) -> bytes:
    """A 'table' (white, black grid lines) or a 'photo' (colourful noise), as PNG."""
    h, w = 160, 480
    if kind == "table":
        a = np.full((h, w, 3), 255, np.uint8)
        for y in range(0, h, 32):
            a[y:y + 4, :, :] = 0
        for x in range(0, w, 96):
            a[:, x:x + 4, :] = 0
    else:
        a = np.random.default_rng(1).integers(0, 255, (h, w, 3), dtype=np.uint8)
    return pymupdf.Pixmap(pymupdf.csRGB, w, h, a.tobytes(), 0).tobytes("png")


def _pdf(tmp_path, pictures):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((60, 60), "Intro")
    for i, kind in enumerate(pictures):
        page.insert_image(pymupdf.Rect(60, 100 + i * 200, 540, 260 + i * 200), stream=_picture(kind))
    page.insert_text((60, 760), "End")
    path = tmp_path / "d.pdf"
    doc.save(path)
    return path


def test_pictures_get_a_marker_at_their_position_only_on_request(tmp_path):
    path = _pdf(tmp_path, ["table"])
    page = parse_pdf(path, image_markers=True)[0]
    assert len(page.images) == 1
    lines = page.markdown.split("\n\n")
    assert lines == ["Intro", page.images[0].marker, "End"]
    assert "<<image" not in parse_pdf(path)[0].markdown
    assert strip_image_markers(page.markdown) == "Intro\n\nEnd"


def test_the_free_filter_tells_a_table_from_a_photo(tmp_path):
    path = _pdf(tmp_path, ["table", "photo"])
    pages = parse_pdf(path, image_markers=True)
    with pymupdf.open(path) as doc:
        verdicts = [image_reader.looks_like_document(doc[0], pymupdf.Rect(i.bbox)) for i in pages[0].images]
    assert verdicts == [True, False]


def _page_with(tmp_path, array):
    h, w = array.shape[:2]
    png = pymupdf.Pixmap(pymupdf.csRGB, w, h, array.astype(np.uint8).tobytes(), 0).tobytes("png")
    doc = pymupdf.open()
    page = doc.new_page()
    rect = pymupdf.Rect(60, 100, 540, 260)
    page.insert_image(rect, stream=png)
    return doc, page, rect


def test_a_spreadsheet_with_coloured_cells_is_a_document_but_a_flat_map_is_not(tmp_path):
    sheet = np.full((160, 480, 3), 255)
    sheet[:32] = (255, 255, 0)                     # yellow header row
    sheet[:, :60] = (230, 60, 60)                  # a red marking column
    sheet[::32, :] = 0                             # grid lines
    sheet[:, ::96] = 0
    doc, page, rect = _page_with(tmp_path, sheet)
    assert image_reader.looks_like_document(page, rect)

    zoning = np.zeros((160, 480, 3))
    zoning[:, :240] = (230, 120, 40)               # orange and green plots: mid-tones
    zoning[:, 240:] = (90, 170, 90)
    doc, page, rect = _page_with(tmp_path, zoning)
    assert not image_reader.looks_like_document(page, rect)


def test_clean_answer():
    assert image_reader.clean_answer("NONE") == ""
    assert image_reader.clean_answer("```\n| א | ב |\n|---|---|\n| 1 | 2 |\n```").endswith("| 1 | 2 |")
    assert image_reader.clean_answer("| א |").startswith(image_reader.LABEL)


@pytest.fixture
def worker(monkeypatch, tmp_path):
    monkeypatch.setattr(image_reader, "CACHE_PATH", tmp_path / "cache.db")
    sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
    import index_worker
    return index_worker


def test_worker_reads_document_pictures_once_and_skips_photos(worker, monkeypatch, tmp_path):
    calls = []

    def fake_read(page, image, client=None):
        calls.append(image.digest)
        image_reader.remember(image.digest, f"{image_reader.LABEL}\n\n| מגרש |\n|---|\n| 301 |")
        return image_reader.Reading(text=f"{image_reader.LABEL}\n\n| מגרש |\n|---|\n| 301 |", cost_usd=0.002)

    monkeypatch.setattr(image_reader, "read_image", fake_read)
    path = _pdf(tmp_path, ["table", "photo"])
    parsed = parse_pdf(path, image_markers=True)
    out = worker.read_images(path, parsed, budget=5)
    assert out["calls"] == 1 and out["read"] == 1 and out["cost_usd"] == pytest.approx(0.002)
    assert "| 301 |" in parsed[0].markdown and "<<image" not in parsed[0].markdown

    parsed = parse_pdf(path, image_markers=True)          # re-parse: answer comes from the cache
    out = worker.read_images(path, parsed, budget=5)
    assert out["calls"] == 0 and out["read"] == 1 and len(calls) == 1


def test_worker_flags_unread_pictures_when_off_or_unavailable(worker, monkeypatch, tmp_path):
    path = _pdf(tmp_path, ["table"])
    parsed = parse_pdf(path, image_markers=True)
    out = worker.read_images(path, parsed, budget=0)
    assert out["calls"] == 0 and out["unread"] and "<<image" not in parsed[0].markdown

    def broken(page, image, client=None):
        raise RuntimeError("402 RESOURCE_EXHAUSTED")

    monkeypatch.setattr(image_reader, "read_image", broken)
    parsed = parse_pdf(path, image_markers=True)
    out = worker.read_images(path, parsed, budget=5)
    assert out["read"] == 0 and out["unread"][0][1].endswith("(Gemini unavailable)")


def test_process_reports_picture_cost(worker, monkeypatch, tmp_path):
    monkeypatch.setattr(image_reader, "read_image", lambda page, image, client=None:
                        image_reader.Reading(text="*[מתוך תמונה במסמך]*\n\nטקסט", cost_usd=0.003))
    path = _pdf(tmp_path, ["table"])
    (tmp_path / "k.pdf").write_bytes(path.read_bytes())
    client = SimpleNamespace(download=lambda url: b"")
    result = worker.process({"record_key": "k", "files": [{"url": "u", "ext": "pdf"}]}, client, tmp_path)
    assert result["llm_tables"] == 1 and result["parse_cost_usd"] == pytest.approx(0.003)
    assert "מתוך תמונה" in result["pages"][0]["markdown"]
