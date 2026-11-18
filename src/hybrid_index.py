"""Library search index: one LanceDB table per agent on the data volume.

Every chunk row holds its text, a 768-number vector, the keyword form of its
text (src/hebrew_text) and the agent's filter fields as plain columns. A search
runs two queries with the same filters, applied before ranking:
- semantic: nearest vectors to the question's vector;
- lexical:  BM25 over the keyword form (exact block numbers, names, terms);
and fuses the two rankings with reciprocal-rank fusion. LanceDB reads from
disk, so the index costs disk space rather than server memory.
"""
from __future__ import annotations

import json
import logging
import re
import threading

from langchain_core.documents import Document

from config import EMBEDDING_DIM, LANCEDB_DIR
from src.hebrew_text import extract_block_plot, keyword_query, keyword_text

logger = logging.getLogger(__name__)

CANDIDATES = 30          # per search method, before fusion
RRF_K = 60
ANN_MIN_ROWS = 50_000    # below this an exact (flat) vector scan is fast enough
_KEY_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
_write_lock = threading.Lock()
_db = None


def _connect():
    global _db
    if _db is None:
        import lancedb

        LANCEDB_DIR.mkdir(parents=True, exist_ok=True)
        _db = lancedb.connect(str(LANCEDB_DIR))
    return _db


def _table_name(agent: dict) -> str:
    return f"agent_{agent['id']}"


def _filter_columns(agent: dict) -> list[tuple[str, str]]:
    cols = []
    for f in agent.get("filters", []):
        if f["type"] == "date":
            cols.append((f"{f['name']}_int", "int"))
        elif not f["name"].endswith("Header"):
            cols.append((f["name"], "str"))
    return cols


def _schema(agent: dict):
    import pyarrow as pa

    fields = [
        pa.field("id", pa.string()), pa.field("record_key", pa.string()),
        pa.field("source", pa.string()), pa.field("page", pa.int32()),
        pa.field("section", pa.string()), pa.field("chunk_index", pa.int32()),
        pa.field("text", pa.string()), pa.field("kw", pa.string()), pa.field("meta", pa.string()),
        pa.field("vector", pa.list_(pa.float32(), EMBEDDING_DIM)),
    ]
    for name, kind in _filter_columns(agent):
        fields.append(pa.field(name, pa.int64() if kind == "int" else pa.string()))
    return pa.schema(fields)


def _table_names(db) -> set[str]:
    listed = db.list_tables()
    names = getattr(listed, "tables", listed)
    return {getattr(n, "name", n) for n in names}


def _table(agent: dict, create: bool = False):
    db = _connect()
    name = _table_name(agent)
    if name in _table_names(db):
        return db.open_table(name)
    if not create:
        return None
    return db.create_table(name, schema=_schema(agent))


def _sql_str(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def build_where(agent: dict, filters: dict | None, query: str = "") -> str | None:
    """SQL filter for the agent's filter fields. Block/plot numbers named in the
    question are added when the user did not set them."""
    from src.agents import _date_to_int

    filters = dict(filters or {})
    names = {f["name"] for f in agent.get("filters", [])}
    for name, value in extract_block_plot(query).items():
        if name in names and not filters.get(name):
            filters[name] = value
    clauses = []
    schema = {f["name"]: f for f in agent.get("filters", [])}
    for name, value in filters.items():
        field = schema.get(name)
        if field is None or value in (None, "", {}) or name.endswith("Header"):
            continue
        if field["type"] == "date":
            if not isinstance(value, dict):
                continue
            if value.get("from"):
                clauses.append(f"{name}_int >= {_date_to_int(value['from'])}")
            if value.get("to"):
                clauses.append(f"{name}_int <= {_date_to_int(value['to'])}")
        else:
            clauses.append(f"{name} = {_sql_str(str(value).strip())}")
    return " AND ".join(clauses) or None


# ── writing ──────────────────────────────────────────────────────────────

def rows_for(agent: dict, key: str, meta: dict, chunks, vectors) -> list[dict]:
    """Table rows for one document. `chunks` are src.section_chunker.Chunk, `meta`
    the flattened filter metadata (with <date>_int) plus record_key/source."""
    rows = []
    for i, (chunk, vector) in enumerate(zip(chunks, vectors)):
        row = {
            "id": f"{key}_{i}", "record_key": key, "source": meta.get("source", key),
            "page": int(chunk.page or 0), "section": chunk.section, "chunk_index": i,
            "text": chunk.text, "kw": keyword_text(chunk.text),
            "meta": json.dumps(meta, ensure_ascii=False), "vector": vector,
        }
        for name, kind in _filter_columns(agent):
            value = meta.get(name)
            row[name] = (int(value) if value not in (None, "") else None) if kind == "int" else (
                str(value) if value not in (None, "") else None)
        rows.append(row)
    return rows


def replace_document(agent: dict, key: str, rows: list[dict]) -> None:
    if not _KEY_RE.match(key):
        raise ValueError(f"bad record key: {key!r}")
    with _write_lock:
        table = _table(agent, create=True)
        table.delete(f"record_key = {_sql_str(key)}")
        if rows:
            table.add(rows)
        _ensure_keyword_index(table)


def delete_document(agent: dict, key: str) -> None:
    if not _KEY_RE.match(key):
        raise ValueError(f"bad record key: {key!r}")
    table = _table(agent)
    if table is not None:
        with _write_lock:
            table.delete(f"record_key = {_sql_str(key)}")


def _ensure_keyword_index(table) -> None:
    """Create the BM25 index once; rows added later are searched too and get
    folded in when the table is optimised."""
    try:
        if not any("kw" in (getattr(ix, "columns", None) or []) for ix in table.list_indices()):
            from lancedb.index import FTS

            # the column is the first argument; passing it as vector_column_name
            # made LanceDB look for a column named "l2" and no index was built
            table.create_index("kw", config=FTS(base_tokenizer="whitespace", lower_case=False, stem=False,
                                          remove_stop_words=False, ascii_folding=False,
                                          with_position=False),
                               replace=True)
    except Exception:
        logger.exception("could not create the keyword index")


def maintain(agent: dict) -> None:
    """Compact files, fold new rows into the keyword index, and build the vector
    index once the table is big enough that a flat scan gets slow. Runs after a
    Batch job and whenever the online indexing queue drains (src/library_index)."""
    table = _table(agent)
    if table is None:
        return
    with _write_lock:
        rows = table.count_rows()
        has_vector_index = any("vector" in (getattr(ix, "columns", None) or []) for ix in table.list_indices())
        if rows >= ANN_MIN_ROWS and not has_vector_index:
            table.create_index(metric="cosine", vector_column_name="vector", index_type="IVF_PQ",
                               num_partitions=max(16, int(rows ** 0.5)), num_sub_vectors=EMBEDDING_DIM // 8)
        table.optimize()


# ── reading ──────────────────────────────────────────────────────────────

def count(agent: dict) -> int:
    table = _table(agent)
    return table.count_rows() if table is not None else 0


def _scan(table, columns: list[str]):
    """Read only `columns` of every row (no vectors)."""
    return table.search().select(columns).limit(max(1, table.count_rows())).to_arrow()


def record_keys(agent: dict) -> set[str]:
    table = _table(agent)
    if table is None:
        return set()
    return set(_scan(table, ["record_key"]).column("record_key").to_pylist())


def has_document(agent: dict, key: str) -> bool:
    """Whether any chunk of this document is in the index (served by search)."""
    if not _KEY_RE.match(key):
        return False
    table = _table(agent)
    return table is not None and table.count_rows(f"record_key = {_sql_str(key)}") > 0


def sources(agent: dict, limit: int = 200) -> list[str] | None:
    """Distinct document titles, or None when there are more than `limit` documents.
    Called on every question: a large library is recognised from the document
    counts, without reading every row of the index."""
    from src import library_store

    try:
        if library_store.status_counts(agent["id"])["counts"].get("indexed", 0) > limit:
            return None
    except Exception:
        logger.exception("document counts unavailable; reading the index instead")
    table = _table(agent)
    if table is None:
        return []
    data = _scan(table, ["record_key", "source"])
    pairs = set(zip(data.column("record_key").to_pylist(), data.column("source").to_pylist()))
    if len({k for k, _ in pairs}) > limit:
        return None
    return sorted({s for _, s in pairs})


def _to_document(row: dict, score: float | None = None) -> Document:
    meta = json.loads(row.get("meta") or "{}")
    meta.update(record_key=row["record_key"], source=row["source"], page=row["page"],
                section=row["section"], chunk_index=row["chunk_index"])
    if score is not None:
        meta["_score"] = round(score, 5)
    return Document(page_content=row["text"], metadata=meta)


def chunks_for_source(agent: dict, source: str) -> list[Document]:
    table = _table(agent)
    if table is None:
        return []
    rows = table.search().where(f"source = {_sql_str(source)}").limit(2000).to_list()
    return [_to_document(r) for r in sorted(rows, key=lambda r: (r["record_key"], r["chunk_index"]))]


def _hybrid(agent: dict, query: str, filters: dict | None, strict: bool = True) -> dict:
    """Run both searches and fuse them. With strict=False a failing search is
    reported in `errors` instead of raised (the admin search lab)."""
    from src.embeddings import embed_query

    out = {"semantic": [], "keyword": [], "fused": [], "rows": {}, "errors": {}, "where": None}
    table = _table(agent)
    if table is None or not query.strip():
        return out
    where = out["where"] = build_where(agent, filters, query)
    scores: dict[str, float] = {}

    def add(name: str, results: list[dict]) -> None:
        for rank, row in enumerate(results):
            out["rows"].setdefault(row["id"], row)
            scores[row["id"]] = scores.get(row["id"], 0.0) + 1.0 / (RRF_K + rank + 1)
        out[name] = results

    try:
        vq = table.search(embed_query(query), vector_column_name="vector").distance_type("cosine")
        if where:
            vq = vq.where(where, prefilter=True)
        add("semantic", vq.limit(CANDIDATES).to_list())
    except Exception as exc:
        if strict:
            raise
        out["errors"]["semantic"] = str(exc)[:300]

    terms = keyword_query(query)
    out["terms"] = terms
    if terms:
        try:
            kq = table.search(terms, query_type="fts", fts_columns="kw")
            if where:
                kq = kq.where(where, prefilter=True)
            add("keyword", kq.limit(CANDIDATES).to_list())
        except Exception as exc:
            logger.exception("keyword search failed; using semantic results only")
            out["errors"]["keyword"] = str(exc)[:300]

    out["fused"] = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return out


def search(agent: dict, query: str, filters: dict | None = None, k: int = 20) -> list[Document]:
    """Hybrid search: semantic + lexical, same filters, fused by reciprocal rank."""
    found = _hybrid(agent, query, filters)
    return [_to_document(found["rows"][i], score) for i, score in found["fused"][:k]]


def search_stages(agent: dict, query: str, filters: dict | None = None, k: int = 20) -> dict:
    """search(), with every stage laid out for the admin search lab: where each
    chunk ranked in the semantic and keyword lists, its raw scores, and the fused order."""
    found = _hybrid(agent, query, filters, strict=False)
    sem_rank = {r["id"]: (i + 1, r.get("_distance")) for i, r in enumerate(found["semantic"])}
    kw_rank = {r["id"]: (i + 1, r.get("_score")) for i, r in enumerate(found["keyword"])}
    rows = []
    for pos, (cid, score) in enumerate(found["fused"]):
        row = found["rows"][cid]
        rows.append({
            "id": cid, "fused_rank": pos + 1, "rrf": round(score, 5), "in_top_k": pos < k,
            "semantic_rank": sem_rank.get(cid, (None, None))[0],
            "semantic_similarity": (1 - sem_rank[cid][1]) if cid in sem_rank and sem_rank[cid][1] is not None else None,
            "keyword_rank": kw_rank.get(cid, (None, None))[0],
            "keyword_score": kw_rank.get(cid, (None, None))[1],
            **{f: row.get(f) for f in ("record_key", "source", "page", "section", "chunk_index", "text")},
        })
    return {"rows": rows, "errors": found["errors"], "where": found["where"],
            "keyword_terms": found.get("terms", ""),
            "semantic_count": len(found["semantic"]), "keyword_count": len(found["keyword"])}


def to_documents(rows: list[dict]) -> list[Document]:
    """search_stages rows back to Documents (for reranking in the search lab)."""
    return [_to_document({**r, "meta": "{}"}, r.get("rrf")) for r in rows]


def document_chunks(agent: dict, key: str) -> list[dict]:
    """Every chunk stored for one document, in order, without vectors."""
    if not _KEY_RE.match(key):
        raise ValueError(f"bad record key: {key!r}")
    table = _table(agent)
    if table is None:
        return []
    rows = (table.search().where(f"record_key = {_sql_str(key)}")
            .select(["id", "page", "section", "chunk_index", "text"]).limit(5000).to_list())
    return sorted(rows, key=lambda r: r["chunk_index"])


def expand_to_sections(agent: dict, docs: list[Document], max_chars: int = 3000) -> list[Document]:
    """Replace each hit by its whole section (neighbouring chunks of the same
    section of the same decision), up to `max_chars`, merging hits that share
    a section. Small chunks find the right place; the section gives the answer
    model the surrounding reasoning."""
    table = _table(agent)
    if table is None or not docs:
        return docs
    out, seen = [], set()
    for doc in docs:
        key, section = doc.metadata.get("record_key"), doc.metadata.get("section")
        if not key or not section:
            out.append(doc)
            continue
        if (key, section) in seen:
            continue
        seen.add((key, section))
        rows = table.search().where(
            f"record_key = {_sql_str(key)} AND section = {_sql_str(section)}").limit(200).to_list()
        rows.sort(key=lambda r: r["chunk_index"])
        hit = doc.metadata.get("chunk_index", 0)
        # grow outwards from the hit until the budget is used
        lo = hi = next((i for i, r in enumerate(rows) if r["chunk_index"] == hit), 0)
        size = len(rows[lo]["text"]) if rows else 0
        while rows and (lo > 0 or hi < len(rows) - 1):
            grew = False
            for j in (hi + 1, lo - 1):
                if 0 <= j < len(rows) and size + len(rows[j]["text"]) <= max_chars:
                    size += len(rows[j]["text"])
                    lo, hi = min(lo, j), max(hi, j)
                    grew = True
            if not grew:
                break
        parts = [rows[i]["text"] for i in range(lo, hi + 1)] if rows else [doc.page_content]
        label = f"[{section}]\n"
        body = "\n\n".join(p[len(label):] if p.startswith(label) else p for p in parts)
        out.append(Document(page_content=label + body, metadata=dict(doc.metadata)))
    return out
