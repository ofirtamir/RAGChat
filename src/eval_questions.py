"""Writing evaluation questions from sampled chunks, and pricing the work.

Shared by scripts/eval_retrieval.py (owner's PC, file-based) and the admin
benchmark jobs (src/eval_jobs.py, on the server). About 40% of a set is built
from the decision's metadata alone (no model): block/plot lookups and
appraiser/committee/year searches. The rest is written by a cheap Gemini model,
one question per chunk; a question that copies a long run of the chunk is dropped.
"""
from __future__ import annotations

import json
import os
import random
from typing import Callable

from config import LLM_MODEL_FAST, MODEL_PRICES
from src import retrieval_eval as rev

QUESTION_MODEL = os.getenv("EVAL_QUESTION_MODEL", "gemini-3.1-flash-lite")
CHARS_PER_TOKEN = 1.6          # Hebrew, measured on the corpus (src/embeddings.py)
OUT_TOKENS_PER_QUESTION = 150  # JSON answer plus minimal thinking
MAX_COPIED_WORDS = 6           # a longer run copied from the chunk drops the question
SPARE = 1.15                   # sample extra decisions to replace dropped questions
META_SHARE = 0.4               # share of questions built from metadata, with no model call
STOP_ON = ("402", "RESOURCE_EXHAUSTED", "PERMISSION_DENIED", "API_KEY_INVALID")

QUESTION_PROMPT = """אתה שמאי מקרקעין מוסמך שמחפש במאגר החלטות של שמאים מכריעים.
לפניך קטע מתוך החלטה אחת. כתוב שאלה אחת, טבעית, בעברית, כפי ששמאי היה מקליד בחיפוש, שהקטע הזה עונה עליה.
- {focus}
- נסח במילים שלך. אל תעתיק מהקטע רצף של יותר מארבע מילים.
- השאלה צריכה להיות ממוקדת מספיק כדי שהקטע הזה יהיה תשובה טובה לה. אפשר לציין עיר או סוג שומה אם זה טבעי, \
אבל לא את כותרת ההחלטה, מספר התיק או שמות הצדדים.
- אם הקטע אינו עונה על שאלה מקצועית (כותרת, חתימה, תוכן עניינים, רשימת נספחים) החזר answerable=false.
החזר JSON בלבד: {{"answerable": true, "question": "..."}}

פרטי ההחלטה: {details}
הקטע:
{text}"""
FOCUS = {
    "table": "בקטע יש טבלה או נתונים מספריים: שאל על נתון, מקדם, שיעור, מחיר למ\"ר או סכום שמופיע בו, "
             "בלי לכתוב בשאלה את המספר שהוא התשובה.",
    "principle": "שאל על עיקרון, כלל משפטי, שיטת שומה או הכרעה עקרונית שהקטע מסביר.",
}


def price(model: str) -> tuple[float, float]:
    best = max((m for m in MODEL_PRICES if model.startswith(m)), key=len, default=None)
    return MODEL_PRICES[best] if best else (0.0, 0.0)


def embedding_cost(texts: list[str]) -> float:
    """Question embeddings (the only paid part of a hybrid run), from their length:
    the embedding API does not report token counts."""
    from src import admin_store

    tokens = sum(len(t or "") for t in texts) / CHARS_PER_TOKEN
    return tokens * admin_store.get_settings()["price_embedding_per_m"] / 1_000_000


def pipeline_cost(n: int) -> float:
    """Rough server-side spend of a pipeline run: one analysis call (~1.2k tokens)
    and ~1.5 search queries, each reranking 20 chunks x 300 chars (~4k tokens)."""
    p_in, p_out = price(LLM_MODEL_FAST)
    return n * (7_200 * p_in + 200 * p_out) / 1_000_000


# ── one chunk ────────────────────────────────────────────────────────────

def chunk_kind(text: str) -> str:
    return "table" if "|---" in text or text.count("|") >= 8 else "principle"


def details(meta: dict) -> str:
    parts = [f"{label}: {meta[f]}" for f, label in (("Committee", "ועדה"), ("AppraisalType", "סוג"))
             if meta.get(f)]
    return "; ".join(parts) or "—"


def question_prompt(chunk: dict) -> str:
    return QUESTION_PROMPT.format(focus=FOCUS[chunk_kind(chunk["text"])], details=details(chunk["meta"]),
                                  text=chunk["text"][:4000])


def meta_questions(chunk: dict, i: int, prefer_block: bool = True) -> list[dict]:
    """Questions built from the decision's metadata alone (no LLM), preferred kind
    first: block/plot lookups have one gold decision; appraiser/committee/year
    questions are scored by how many results carry that metadata."""
    kinds = (_block_question, _filter_question) if prefer_block else (_filter_question, _block_question)
    return [q for q in (kind(chunk, i) for kind in kinds) if q]


def _block_question(chunk: dict, i: int) -> dict | None:
    meta, key = chunk["meta"], chunk["record_key"]
    block, plot = meta.get("Block"), meta.get("Plot")
    if block:
        if plot:
            text = [f"מה הכריע השמאי המכריע בגוש {block} חלקה {plot}?",
                    f"גוש {block} חלקה {plot} - מה נקבע בשומה?",
                    f"יש החלטה של שמאי מכריע לגבי גוש {block} חלקה {plot}?"][i % 3]
            expected = {"Block": block, "Plot": plot}
        else:
            text, expected = f"מה נקבע בהחלטת השמאי המכריע בגוש {block}?", {"Block": block}
        return {"question": text, "type": "block_plot", "gold": [key], "expected_meta": expected}
    return None


def _filter_question(chunk: dict, i: int) -> dict | None:
    meta = chunk["meta"]
    appraiser, committee, date = meta.get("DecisiveAppraiser"), meta.get("Committee"), meta.get("DecisionDate")
    if appraiser and committee and date:
        topic = meta.get("AppraisalType") or "החלטות"
        return {"question": f"{topic} של השמאי המכריע {appraiser}, {committee}, {date[:4]}",
                "type": "metadata", "gold": [],
                "expected_meta": {"DecisiveAppraiser": appraiser, "Committee": committee,
                                  "DecisionDate": date[:4]}}
    return None


# ── a whole set ──────────────────────────────────────────────────────────

def plan(chunks: list[dict], n: int, meta_share: float = META_SHARE, seed: int = 7) -> dict:
    """Split sampled chunks into metadata questions (free) and chunks to send to
    the model, and estimate the model's cost. Spends nothing."""
    chunks = list(chunks)
    random.Random(seed).shuffle(chunks)
    n_meta = round(n * meta_share)
    meta_items, llm_chunks = [], []
    for chunk in chunks:
        blocks = sum(1 for m in meta_items if m["type"] == "block_plot")
        options = (meta_questions(chunk, len(meta_items), prefer_block=blocks * 2 <= len(meta_items))
                   if len(meta_items) < n_meta else [])
        asked = {m["question"] for m in meta_items}
        item = next((q for q in options if q["question"] not in asked), None)
        if item:
            meta_items.append({**item, "origin": "meta", "record_key": chunk["record_key"]})
        else:
            llm_chunks.append(chunk)
    n_llm = max(0, min(n - len(meta_items), len(llm_chunks)))
    prompts = [question_prompt(c) for c in llm_chunks]
    in_tokens = sum(len(p) for p in prompts[:n_llm]) / CHARS_PER_TOKEN
    p_in, p_out = price(QUESTION_MODEL)
    cost = (in_tokens * p_in + n_llm * OUT_TOKENS_PER_QUESTION * p_out) / 1_000_000
    return {"meta_items": meta_items, "llm_chunks": llm_chunks, "prompts": prompts, "n_llm": n_llm,
            "sampled": len(chunks), "model": QUESTION_MODEL, "input_tokens": round(in_tokens),
            "output_tokens": n_llm * OUT_TOKENS_PER_QUESTION, "cost_usd": round(cost, 4)}


def ask_gemini(prompt: str, max_tokens: int = 400) -> tuple[dict, float]:
    """One JSON answer from the model: (parsed answer, cost in USD)."""
    from google.genai import types
    from src.embeddings import genai_client

    resp = genai_client().models.generate_content(
        model=QUESTION_MODEL, contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0, max_output_tokens=max_tokens, response_mime_type="application/json",
            thinking_config=types.ThinkingConfig(thinking_level="minimal")))
    usage = resp.usage_metadata
    p_in, p_out = price(QUESTION_MODEL)
    out_tokens = (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)
    cost = ((usage.prompt_token_count or 0) * p_in + out_tokens * p_out) / 1_000_000
    return json.loads(resp.text or "{}"), cost


def write_questions(planned: dict, ask: Callable[[str], tuple[dict, float]] | None = None,
                    on_item: Callable[[dict, float], None] | None = None,
                    should_stop: Callable[[float], bool] | None = None) -> dict:
    """Ask the model for the planned questions. Stops on a billing/permission error
    or three failures in a row, and when `should_stop(spent so far)` says so; what
    was written so far is kept. `on_item(item, spent)` is called for every accepted
    question. Returns {items, spent, stopped}."""
    ask = ask or ask_gemini
    items, spent, failures, stopped = [], 0.0, 0, None
    for chunk, prompt in zip(planned["llm_chunks"], planned["prompts"]):
        if len(items) >= planned["n_llm"] or (should_stop and should_stop(spent)):
            break
        try:
            answer, call_cost = ask(prompt)
            failures = 0
        except Exception as exc:
            failures += 1
            message = str(exc)[:300]
            if failures >= 3 or any(s in message for s in STOP_ON):
                stopped = message
                break
            continue
        spent += call_cost
        question = str(answer.get("question") or "").strip()
        if not answer.get("answerable") or len(question) < 8:
            continue
        if rev.longest_shared_run(question, chunk["text"]) > MAX_COPIED_WORDS:
            continue
        item = {"question": question, "type": chunk_kind(chunk["text"]), "gold": [chunk["record_key"]],
                "gold_section": chunk.get("section"), "origin": "llm", "record_key": chunk["record_key"],
                "chunk_id": chunk["id"]}
        items.append(item)
        if on_item:
            on_item(item, spent)
    return {"items": items, "spent": round(spent, 6), "stopped": stopped}
