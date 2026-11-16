"""Metadata helpers for agent documents: the filter fields stored on every chunk
and the display name of a document. Used by src/library_index.py.

Documents reach an agent's index only through the admin library
(src/library_store.py → local parse worker → src/library_index.py).
"""
from src.agents import _date_to_int


def flatten_metadata(agent: dict, record_meta: dict) -> dict:
    """Keep only schema fields; add <date>_int companions for range filtering."""
    out = {}
    for field in agent.get("filters", []):
        value = record_meta.get(field["name"])
        if value in (None, ""):
            continue
        if field["type"] == "date":
            out[field["name"]] = str(value)[:10]
            out[f"{field['name']}_int"] = _date_to_int(str(value))
        else:
            out[field["name"]] = str(value)
    return out


def source_name(agent: dict, record_meta: dict, fallback: str) -> str:
    header_field = next((f["name"] for f in agent.get("filters", []) if "Header" in f["name"]), None)
    if header_field and record_meta.get(header_field):
        return str(record_meta[header_field])
    return fallback
