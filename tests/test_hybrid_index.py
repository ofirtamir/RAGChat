"""Section chunking, Hebrew keyword normalisation, the LanceDB hybrid index and batch indexing."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402

from src import hebrew_text, hybrid_index  # noqa: E402
from src.section_chunker import SECTION_CHUNK_SIZE, chunk_parsed_document  # noqa: E402

AGENT = {
    "id": "t",
    "filters": [
        {"name": "AppraisalHeader", "label": "כותרת", "type": "text"},
        {"name": "Committee", "label": "ועדה", "type": "choice"},
        {"name": "Block", "label": "גוש", "type": "text"},
        {"name": "Plot", "label": "חלקה", "type": "text"},
        {"name": "PublicityDate", "label": "פרסום", "type": "date"},
    ],
}
TABLE = "| מגרש | שטח |\n|---|---|\n| 301 | 7,705 |\n| 303 | 4,246 |"


# ── section chunker ──────────────────────────────────────────────────────

def test_paragraph_cut_by_a_page_break_is_joined():
    chunks = chunk_parsed_document([(0, "1. מטרת ההחלטה ההחלטה עוסקת בהיטל"), (1, "השבחה על המקרקעין.")])
    assert len(chunks) == 1
    assert "בהיטל השבחה על המקרקעין." in chunks[0].text and chunks[0].page == 0


def test_table_split_by_a_page_break_is_one_table():
    first = "| מגרש | שטח |\n|---|---|\n| 301 | 7,705 |"
    second = "| מגרש | שטח |\n|---|---|\n| 303 | 4,246 |"
    chunks = chunk_parsed_document([(0, "להלן הטבלה:\n\n" + first), (1, second + "\n\nסוף.")])
    tables = [c for c in chunks if "|---|" in c.text]
    assert len(tables) == 1
    assert "| 301 |" in tables[0].text and "| 303 |" in tables[0].text
    assert tables[0].text.count("| מגרש | שטח |") == 1


def test_chunks_follow_numbered_sections_and_carry_the_heading():
    pages = [(0, "1. מטרת ההחלטה. הכרעה במחלוקת.\n\n2. תיאור הנכס. מגרש בשטח 400 מ\"ר.\n\n"
                 "2.1 הנכס בנוי. קומה אחת.")]
    chunks = chunk_parsed_document(pages)
    assert [c.section for c in chunks] == ["סעיף 1: מטרת ההחלטה", "סעיף 2: תיאור הנכס"]
    assert chunks[1].text.startswith("[סעיף 2: תיאור הנכס]\n")
    assert "2.1 הנכס בנוי" in chunks[1].text and "מטרת" not in chunks[1].text


def test_long_section_is_packed_with_small_overlap_and_tables_stay_whole():
    long_para = " ".join(f"משפט מספר {i} בסעיף הארוך." for i in range(120))
    chunks = chunk_parsed_document([(0, f"3. שומה. {long_para}\n\n**טבלת מגרשים**\n\n{TABLE}")])
    prose = [c for c in chunks if "|---|" not in c.text]
    assert len(prose) >= 3
    assert all(f"משפט מספר {i} " in "".join(c.text for c in chunks) for i in range(120))  # nothing lost
    assert all(len(c.text) <= SECTION_CHUNK_SIZE + 200 for c in prose)
    assert all(c.section.startswith("סעיף 3") for c in chunks)
    assert prose[1].text.split("\n", 1)[1].startswith("…")  # overlap with the previous chunk
    table = next(c for c in chunks if "|---|" in c.text)
    assert "**טבלת מגרשים**" in table.text and "| 303 | 4,246 |" in table.text


def test_lines_that_introduce_a_table_go_with_it_not_into_a_tiny_chunk():
    pages = [(0, "1. רקע. " + "הנכס נמצא בעיר. " * 30 + "\n\n3. המועד הקובע:\n\nלהלן המועד הקובע לשומה:\n\n"
                 + TABLE + "\n\nסוף הסעיף.")]
    chunks = chunk_parsed_document(pages)
    sec3 = [c for c in chunks if c.section.startswith("סעיף 3")]
    assert "|---|" in sec3[0].text  # no chunk holding only the intro lines
    assert "3. המועד הקובע:" in sec3[0].text and "להלן המועד הקובע לשומה:" in sec3[0].text
    assert sum("להלן המועד הקובע" in c.text for c in chunks) == 1
    # a long paragraph before a table stays in its prose chunk; only its end is copied
    chunks = chunk_parsed_document([(0, "1. רקע. " + "הנכס נמצא בעיר. " * 30 + "\n\n" + TABLE)])
    assert "|---|" not in chunks[0].text and chunks[1].text.split("\n", 1)[1].startswith("…")


# ── Hebrew keyword normalisation ─────────────────────────────────────────

def test_prefixes_are_indexed_both_ways():
    terms = hebrew_text.keyword_terms("והשבחה ובתל-אביב")
    assert {"והשבחה", "השבחה", "ובתל", "בתל", "תל", "אביב"} <= set(terms)  # ו + ב: two prefix letters


def test_prefix_stripping_does_not_invent_other_words():
    terms = set(hebrew_text.keyword_terms("היטל השבחה המועד הקובע מלא בהיטל לשמאי"))
    assert not terms & {"יטל", "שבחה", "בחה", "ועד", "לא", "מאי"}
    assert {"מועד", "היטל", "קובע", "שמאי"} <= terms  # real prefixes are still removed
    assert set(hebrew_text.keyword_terms("והמבנה")) == {"והמבנה", "המבנה", "מבנה"}


def test_numbers_and_quotes():
    terms = hebrew_text.keyword_terms('גוש 6426/484 בשטח 400 מ"ר')
    assert {"6426/484", "6426", "484", "400", "מר"} <= set(terms)


def test_block_and_plot_are_read_from_the_question():
    assert hebrew_text.extract_block_plot("מה נקבע בגוש 6426 חלקה 484?") == {"Block": "6426", "Plot": "484"}
    assert hebrew_text.extract_block_plot("היטל השבחה בירושלים") == {}


# ── hybrid index (real LanceDB, fake embeddings) ─────────────────────────

def fake_vector(text: str) -> list[float]:
    """Deterministic 768-d vector: similar word sets give similar vectors."""
    v = [0.0] * 768
    for term in hebrew_text.keyword_terms(text):
        v[hash(term) % 768] += 1.0
    norm = sum(x * x for x in v) ** 0.5 or 1.0
    return [x / norm for x in v]


@pytest.fixture
def index(tmp_path, monkeypatch):
    import src.embeddings

    monkeypatch.setattr(hybrid_index, "LANCEDB_DIR", tmp_path / "lancedb")
    monkeypatch.setattr(hybrid_index, "_db", None)
    monkeypatch.setattr(src.embeddings, "embed_query", fake_vector)

    def add(key, committee, block, date, pages):
        chunks = chunk_parsed_document(pages)
        meta = {"record_key": key, "source": f"הכרעה {key}", "Committee": committee, "Block": block,
                "PublicityDate": date, "PublicityDate_int": int(date.replace("-", ""))}
        rows = hybrid_index.rows_for(AGENT, key, meta, chunks, [fake_vector(c.text) for c in chunks])
        hybrid_index.replace_document(AGENT, key, rows)

    add("a1", "כפר סבא", "6426", "2026-07-10",
        [(0, "1. מטרת ההחלטה. היטל השבחה בגין תוספת קומה.\n\n2. שומה. השווי נקבע לפי עסקאות השוואה.")])
    add("b2", "ירושלים", "30279", "2025-01-01",
        [(0, "1. רקע. תביעת פיצויים לפי סעיף 197 בשל פגיעה בזכויות.")])
    add("c3", "ירושלים", "100", "2024-05-05",
        [(0, "1. רקע. היטל השבחה בעקבות תוכנית חדשה בשכונה.")])
    return add


def test_maintenance_does_not_keep_old_copies_of_the_table(index, monkeypatch):
    """Each compaction writes a new version; old ones must not pile up on the volume."""
    from datetime import timedelta

    monkeypatch.setattr(hybrid_index, "KEEP_VERSIONS", timedelta(0))
    for i in range(4):
        index(f"n{i}", "חיפה", "1", "2025-02-02", [(0, f"1. רקע. החלטה מספר {i} על היטל השבחה.")])
        hybrid_index.maintain(AGENT)
    table = hybrid_index._table(AGENT)
    assert len(table.list_versions()) <= 2
    assert hybrid_index.record_keys(AGENT) == {"a1", "b2", "c3", "n0", "n1", "n2", "n3"}  # nothing lost
    index("n4", "חיפה", "1", "2025-02-02", [(0, "1. רקע. עוד החלטה.")])
    assert hybrid_index.drop_old_versions(AGENT, timedelta(0))["old_versions"] >= 1
    assert len(hybrid_index._table(AGENT).list_versions()) == 1
    assert hybrid_index.count(AGENT) == 9


def test_count_sources_and_keys(index):
    assert hybrid_index.count(AGENT) == 4
    indexed = [getattr(ix, "columns", []) for ix in hybrid_index._table(AGENT).list_indices()]
    assert ["kw"] in indexed  # the BM25 keyword index is really built
    assert hybrid_index.record_keys(AGENT) == {"a1", "b2", "c3"}
    assert hybrid_index.sources(AGENT) == ["הכרעה a1", "הכרעה b2", "הכרעה c3"]
    assert hybrid_index.sources(AGENT, limit=2) is None


def test_keyword_search_finds_exact_numbers_and_prefixed_words(index):
    hits = hybrid_index.search(AGENT, "פיצויים סעיף 197", k=3)
    assert hits[0].metadata["record_key"] == "b2"
    hits = hybrid_index.search(AGENT, "בהיטל ההשבחה", k=5)
    assert {"a1", "c3"} <= {h.metadata["record_key"] for h in hits}


def test_filters_apply_to_both_search_methods(index):
    hits = hybrid_index.search(AGENT, "היטל השבחה", {"Committee": "ירושלים"}, k=5)
    assert {h.metadata["record_key"] for h in hits} == {"b2", "c3"} or \
        {h.metadata["record_key"] for h in hits} <= {"b2", "c3"}
    assert all(h.metadata["Committee"] == "ירושלים" for h in hits)
    hits = hybrid_index.search(AGENT, "היטל השבחה", {"PublicityDate": {"from": "2025-01-01"}}, k=5)
    assert {h.metadata["record_key"] for h in hits} <= {"a1", "b2"}


def test_block_in_the_question_becomes_a_filter(index):
    hits = hybrid_index.search(AGENT, "מה נקבע בגוש 6426?", k=5)
    assert hits and {h.metadata["record_key"] for h in hits} == {"a1"}
    assert hybrid_index.build_where(AGENT, {"Block": "100"}, "גוש 6426") == "Block = '100'"


def test_quotes_in_filter_values_are_escaped():
    assert hybrid_index.build_where(AGENT, {"Committee": "תל אביב' OR 1=1"}) == \
        "Committee = 'תל אביב'' OR 1=1'"


def test_hits_expand_to_their_section_and_full_document_is_ordered(index):
    hits = hybrid_index.search(AGENT, "עסקאות השוואה", k=1)
    expanded = hybrid_index.expand_to_sections(AGENT, hits)
    assert expanded[0].page_content.startswith("[סעיף 2: שומה]")
    chunks = hybrid_index.chunks_for_source(AGENT, "הכרעה a1")
    assert [c.metadata["chunk_index"] for c in chunks] == [0, 1]


def test_search_stages_show_both_rankings_and_survive_a_failed_embedding(index, monkeypatch):
    import src.embeddings

    stages = hybrid_index.search_stages(AGENT, "פיצויים סעיף 197", k=2)
    top = stages["rows"][0]
    assert top["record_key"] == "b2" and top["fused_rank"] == 1 and top["in_top_k"]
    assert top["semantic_rank"] and top["keyword_rank"] == 1 and top["text"]
    assert [r["id"] for r in stages["rows"]] == [d.metadata["record_key"] + "_" + str(d.metadata["chunk_index"])
                                                 for d in hybrid_index.search(AGENT, "פיצויים סעיף 197", k=10)]

    def no_credit(_):
        raise RuntimeError("RESOURCE_EXHAUSTED")

    monkeypatch.setattr(src.embeddings, "embed_query", no_credit)
    stages = hybrid_index.search_stages(AGENT, "פיצויים סעיף 197")
    assert "semantic" in stages["errors"] and stages["rows"][0]["record_key"] == "b2"
    with pytest.raises(RuntimeError):
        hybrid_index.search(AGENT, "פיצויים סעיף 197")


def test_document_chunks_are_read_back_in_order(index):
    chunks = hybrid_index.document_chunks(AGENT, "a1")
    assert [c["chunk_index"] for c in chunks] == [0, 1] and chunks[1]["section"] == "סעיף 2: שומה"
    assert "vector" not in chunks[0]
    with pytest.raises(ValueError):
        hybrid_index.document_chunks(AGENT, "a1' OR '1'='1")


def test_reindex_replaces_and_delete_removes(index):
    index("a1", "כפר סבא", "6426", "2026-07-10", [(0, "1. רקע. גרסה חדשה.")])
    assert hybrid_index.count(AGENT) == 3
    hybrid_index.delete_document(AGENT, "a1")
    assert hybrid_index.record_keys(AGENT) == {"b2", "c3"}
    with pytest.raises(ValueError):
        hybrid_index.delete_document(AGENT, "x' OR '1'='1")


# ── batch indexing ───────────────────────────────────────────────────────

def test_batch_job_results_are_written_per_document(tmp_path, monkeypatch):
    from src import admin_store, library_batch, library_index, library_store
    import src.agents
    import src.embeddings

    db = tmp_path / "admin.db"
    for module in (admin_store, library_store, library_batch):
        monkeypatch.setattr(module, "_DB_PATH", db)
        module._init_db()
    monkeypatch.setattr(hybrid_index, "LANCEDB_DIR", tmp_path / "lancedb")
    monkeypatch.setattr(hybrid_index, "_db", None)
    monkeypatch.setattr(src.agents, "get_agent", lambda agent_id: AGENT)
    monkeypatch.setattr(library_batch, "_start_poller", lambda: None)

    records = [{"AppraisalHeader": f"הכרעה {i}", "Committee": "ירושלים", "Block": str(i),
                "PublicityDate": "2025-03-01T00:00:00+03:00",
                "Document": [{"FileName": f"https://example.org/{i}", "Extension": "pdf"}]} for i in range(3)]
    library_store.upsert_catalog(AGENT, records)
    keys = [d["record_key"] for d in library_store.search(AGENT)["documents"]]
    for i, key in enumerate(keys):
        text = "\n\n".join(f"{n}. סעיף {n}. " + "מילים רבות בסעיף. " * 40 for n in range(1, 2 + i))
        library_store.save_parsed("t", key, [{"number": 0, "markdown": text}], 0, [])
    library_store.transition(AGENT, "indexing", ("parsed",), keys=keys)

    sent = {}

    class Batches:
        def create_embeddings(self, model, src, config):
            sent["texts"] = src.inlined_requests.contents
            sent["config"] = src.inlined_requests.config
            return SimpleNamespace(name="batches/1")

        def get(self, name):
            responses = [SimpleNamespace(error=None, response=SimpleNamespace(
                embedding=SimpleNamespace(values=fake_vector(t)), token_count=100)) for t in sent["texts"]]
            return SimpleNamespace(state=SimpleNamespace(name="JOB_STATE_SUCCEEDED"), error=None,
                                   dest=SimpleNamespace(inlined_embed_content_responses=responses))

    monkeypatch.setattr(src.embeddings, "genai_client", lambda: SimpleNamespace(batches=Batches()))
    admin_store.update_settings({"price_embedding_batch_per_m": 0.075})

    assert library_batch.submit("t", keys) == ["batches/1"]
    assert sent["config"].output_dimensionality == 768 and sent["config"].task_type == "RETRIEVAL_DOCUMENT"
    assert library_batch.resume() == set(keys)

    library_batch.poll_once()
    counts = library_store.status_counts("t")
    assert counts["counts"]["indexed"] == 3
    total_chunks = len(sent["texts"])
    assert counts["index_cost"]["tokens"] == 100 * total_chunks
    assert counts["index_cost"]["total_usd"] == pytest.approx(100 * total_chunks * 0.075 / 1_000_000)
    assert hybrid_index.record_keys(AGENT) == set(keys)
    assert library_batch.resume() == set()
    assert library_index.BATCH_MIN_DOCUMENTS >= 2
