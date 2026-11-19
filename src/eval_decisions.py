"""Benchmark questions written from whole decisions, the way appraisers ask them.

The "decisions" build (src/eval_jobs.py). Every question knows its answer:

- named       find a decision by its parties and date, from its title (no model)
- block_plot  find a decision by block/plot, from its metadata (no model)
- fact        one detail of a named decision (realization trigger, determining
              date, value per m², permit number, enhancing plans, coefficients),
              with the answer and a verbatim quote; a quote that is not in the
              decision drops the question
- analysis    the reasoning or dispute of a named decision, with the points a
              good answer must cover
- topic       a professional topic across the library (parking levy, the 140 m²
              exemption, ...). Every indexed decision whose text mentions it is a
              candidate, narrowed to one committee when there are too many; the
              model keeps the ones that really discuss it, and the gold is that
              whole set — so a search that returns another relevant decision is
              not marked wrong.

Retrieval runs score every type by its gold decision(s); the stored answer is for
checking answers by hand (and, later, answer-level runs).
"""
from __future__ import annotations

import json
import math
import random
import re
from collections import Counter
from typing import Callable

from src import eval_questions as eq, retrieval_eval as rev

DOC_CHARS = 30_000         # decision text the model reads (~19k tokens)
FACTS_PER_DOC = 2
OUT_TOKENS_DOC = 700
SNIPPET_CHARS = 700
MAX_TOPIC_GOLD = 15        # more candidates than this: narrow the topic to one committee
MIN_TOPIC_GOLD = 2
SCOPES_PER_TOPIC = 2
MIN_QUOTE_WORDS = 4
MAX_COPIED_FROM_QUOTE = 4  # words a fact question may share in a row with its answer passage
SHARES = {"named": 0.15, "block_plot": 0.10, "fact": 0.40, "analysis": 0.15, "topic": 0.20}

# fictional decisions: style examples only
DOC_PROMPT = """אתה שמאי מקרקעין ותיק שמכין שאלות בחינה למערכת חיפוש בהחלטות של שמאים מכריעים.
לפניך החלטה אחת. כתוב עליה שאלות כפי ששמאי היה שואל את המערכת.

דרכים שבהן שמאי מזהה את ההחלטה הזו בשאלה: {refs}

1. facts — עד {k} שאלות, כל אחת על פרט אחד שמופיע בהחלטה. העדף: עילת המימוש (היתר, מכר וכו'), המועד הקובע, \
שווי למ"ר מבונה או לקרקע (של השמאי המכריע או של שמאי צד), מספר היתר הבנייה, התכניות המשביחות, מקדמים שנקבעו \
(שוליות, הפחתה, דחייה, בלאי), סכום ההשבחה או ההיטל.
   - כל שאלה חייבת לזהות את ההחלטה באחת הדרכים שלמעלה, אחרת אין לה תשובה אחת.
   - answer: התשובה הקצרה (ערך, תאריך, מספר או רשימה קצרה).
   - quote: ציטוט מדויק, מילה במילה, מתוך ההחלטה (10 עד 40 מילים) שמכיל את התשובה. אל תשנה בו דבר.
   - נסח את השאלה במילים שלך, בלי להעתיק ניסוחים מהציטוט.
2. analysis — שאלה אחת על ההנמקה או המחלוקת בהחלטה (על מה נחלקו שמאי הצדדים, כיצד הכריע השמאי המכריע בנושא \
ועל מה התבסס), ו-points: 2 עד 4 נקודות קצרות שתשובה טובה חייבת לכלול.
אל תמציא: פרט שאינו מופיע בהחלטה — אל תשאל עליו.

דוגמאות לסגנון (על החלטות אחרות):
- מה המועד הקובע בהחלטה בעניין כהן נ' ועדה מקומית חיפה?
- בגוש 1234 חלקה 56, מהי עילת המימוש ומה מספר ההיתר?
- באיזה שווי למ"ר מבונה קבע השמאי המכריע בהכרעה מיום 01-02-2025 בעניין לוי נ' ועדה מקומית רעננה?
- על מה נסובה המחלוקת המהותית בין שמאי הצדדים בתיק של אברהם נ' ועדה מקומית ירושלים?

החזר JSON בלבד:
{{"facts": [{{"field": "...", "question": "...", "answer": "...", "quote": "..."}}], \
"analysis": {{"question": "...", "points": ["..."]}}}}

ההחלטה:
{text}"""

TOPIC_PROMPT = """אתה שמאי מקרקעין. הנושא: {label}.
לפניך קטעים מהחלטות של שמאים מכריעים, כל קטע עם מספר. החזר את המספרים של הקטעים שבהם ההחלטה באמת דנה \
בנושא (טענה של צד, הכרעה או חישוב בנושא), ולא רק מזכירה אותו בדרך אגב או ברשימה.
החזר JSON בלבד: {{"relevant": [1, 4]}}

{snippets}"""

# question templates take {scope}: "" or " בוועדה המקומית X"
TOPICS = [
    {"id": "parking", "label": "כופר חניה", "patterns": ["כופר חני"],
     "question": "תמצא לי החלטות של שמאי מכריע שדנות בכופר חניה{scope}. מה נקבע לגבי קיזוז הכופר מההשבחה?"},
    {"id": "exempt140", "label": "פטור להרחבת דירת מגורים עד 140 מ\"ר", "patterns": ["140 מ\"ר", "140 מ״ר", "140 מטר"],
     "question": "יש החלטות{scope} שעוסקות בפטור עד 140 מ\"ר? מה החליט השמאי המכריע?"},
    {"id": "zeira", "label": "נוסחת זעירא במימוש חלקי", "patterns": ["זעירא"],
     "question": "באילו החלטות{scope} המימוש היה חלקי והשתמשו בנוסחת זעירא?"},
    {"id": "marginal", "label": "מקדם שוליות", "patterns": ["שוליות"],
     "question": "איזה מקדם שוליות נותנים בין דירות קטנות לגדולות{scope}, ובאילו החלטות?"},
    {"id": "preservation", "label": "עלויות שימור ומבנה לשימור", "patterns": ["לשימור", "עלויות השימור", "עלויות שימור"],
     "question": "תמצא לי החלטות{scope} שבהן עלויות שימור השפיעו על ההשבחה. מה הוכרע?"},
    {"id": "condition", "label": "מצב פיזי ובלאי של המבנה", "patterns": ["מצב פיזי", "מצבו הפיזי", "בלאי"],
     "question": "האם יש החלטות{scope} שבהן המצב הפיזי של הנכס השפיע על השווי? איזה מקדם הפחתה ניתן?"},
    {"id": "tama38", "label": "תמ\"א 38", "patterns": ["תמ\"א 38", "תמ״א 38", "תמא 38"],
     "question": "מה הכריעו שמאים מכריעים{scope} לגבי השבחה מתמ\"א 38?"},
    {"id": "deferral", "label": "מקדם דחייה למימוש עתידי", "patterns": ["מקדם דחי", "היוון"],
     "question": "באילו החלטות{scope} הופעל מקדם דחייה או היוון למימוש עתידי, ובאיזה שיעור?"},
    {"id": "nonconforming", "label": "שימוש חורג", "patterns": ["שימוש חורג"],
     "question": "תביא לי החלטות{scope} על היטל השבחה בגין שימוש חורג. איך חושבה ההשבחה?"},
    {"id": "reparcel", "label": "איחוד וחלוקה", "patterns": ["איחוד וחלוקה"],
     "question": "יש החלטות{scope} שבהן ההשבחה נבעה מתכנית איחוד וחלוקה? מה נקבע?"},
]


# ── referring to a decision ──────────────────────────────────────────────

_TITLE = re.compile(r"מיום\s+(\d{1,2}[-./]\d{1,2}[-./]\d{4})\s+בעניין\s+(.+)$")
_VERSUS = re.compile(r"\s+נ['׳]?\s+")


def parties(title: str, appraisal_type: str | None = None) -> dict | None:
    """{date, applicant, respondent} from a title such as "הכרעת שמאי מכריע מיום
    04-05-2026 בעניין היטל השבחה כהן משה נ ועדה מקומית חיפה"."""
    m = _TITLE.search(" ".join((title or "").split()))
    if not m:
        return None
    rest = m.group(2).strip()
    for prefix in filter(None, [appraisal_type, "היטל השבחה", "פיצויים", "ירידת ערך"]):
        if rest.startswith(prefix + " "):
            rest = rest[len(prefix):].strip()
            break
    sides = _VERSUS.split(rest, maxsplit=1)
    if len(sides) != 2 or not sides[0] or not sides[1]:
        return None
    return {"date": m.group(1), "applicant": sides[0].strip(), "respondent": sides[1].strip()}


def references(doc: dict) -> list[str]:
    """Ways an appraiser names this decision in a question."""
    meta, out = doc.get("meta") or {}, []
    p = parties(doc.get("title") or "", meta.get("AppraisalType"))
    if p:
        out += [f"{p['applicant']} נ' {p['respondent']}", f"ההכרעה מיום {p['date']} בעניין {p['applicant']}"]
    if meta.get("Block") and meta.get("Plot"):
        out.append(f"גוש {meta['Block']} חלקה {meta['Plot']}")
    return out


def named_question(doc: dict, i: int) -> dict | None:
    p = parties(doc.get("title") or "", (doc.get("meta") or {}).get("AppraisalType"))
    if not p:
        return None
    a, r, d = p["applicant"], p["respondent"], p["date"]
    text = [f"מה הכריע השמאי המכריע בתיק {a} נ' {r}?",
            f"תביא לי את ההכרעה מיום {d} בעניין {a}",
            f"מה נקבע בהחלטה של {a} מול {r}?"][i % 3]
    return {"question": text, "type": "named", "gold": [doc["record_key"]], "origin": "title",
            "record_key": doc["record_key"]}


def block_question(doc: dict, i: int) -> dict | None:
    item = eq._block_question({"meta": doc.get("meta") or {}, "record_key": doc["record_key"]}, i)
    return {**item, "origin": "meta", "record_key": doc["record_key"]} if item else None


# ── checking the model's answers ─────────────────────────────────────────

_QUOTES = str.maketrans({"״": '"', "”": '"', "“": '"', "׳": "'", "’": "'", "‘": "'"})


def normalize(text: str) -> str:
    """For quote matching: unified quote marks, no Markdown table/emphasis marks, single spaces."""
    text = (text or "").translate(_QUOTES)
    return " ".join(re.sub(r"[|*#_]+", " ", text).split())


def quote_found(quote: str, text: str) -> bool:
    q = normalize(quote).strip(' ."\'')
    return len(q.split()) >= MIN_QUOTE_WORDS and q in normalize(text)


def doc_items(doc: dict, answer: dict, k: int = FACTS_PER_DOC) -> list[dict]:
    """The checked questions from one decision's model answer."""
    key, text, out = doc["record_key"], doc["text"], []
    for fact in (answer.get("facts") or [])[:k]:
        if not isinstance(fact, dict):
            continue
        question, value, quote = (str(fact.get(f) or "").strip() for f in ("question", "answer", "quote"))
        if len(question) < 8 or not value or not quote_found(quote, text):
            continue
        # a question that quotes the answer passage, or holds the answer, measures string matching
        if rev.longest_shared_run(question, quote) > MAX_COPIED_FROM_QUOTE or normalize(value) in normalize(question):
            continue
        out.append({"question": question, "type": "fact", "gold": [key], "origin": "llm", "record_key": key,
                    "answer": {"field": str(fact.get("field") or "")[:60], "value": value[:400], "quote": quote[:600]}})
    analysis = answer.get("analysis")
    if isinstance(analysis, dict):
        question = str(analysis.get("question") or "").strip()
        points = [str(p).strip()[:300] for p in analysis.get("points") or [] if str(p).strip()][:4]
        if len(question) >= 8 and points:
            out.append({"question": question, "type": "analysis", "gold": [key], "origin": "llm",
                        "record_key": key, "answer": {"points": points}})
    return out


# ── topics ───────────────────────────────────────────────────────────────

def _snippet(text: str, patterns: list[str]) -> str:
    hits = [text.find(p) for p in patterns if p in text]
    at = min(hits) if hits else 0
    start = max(0, at - SNIPPET_CHARS // 2)
    return " ".join(text[start:start + SNIPPET_CHARS].split())


def topic_candidates(rows: list[dict], topic: dict) -> dict[str, dict]:
    """{record_key: {snippet, committee}} from index rows {record_key, text, meta}
    whose text mentions the topic, first matching chunk per decision."""
    out: dict[str, dict] = {}
    for row in rows:
        key, text = row.get("record_key"), row.get("text") or ""
        if key in out or not any(p in text for p in topic["patterns"]):
            continue
        meta = row["meta"] if isinstance(row.get("meta"), dict) else json.loads(row.get("meta") or "{}")
        out[key] = {"snippet": _snippet(text, topic["patterns"]), "committee": meta.get("Committee") or ""}
    return out


def topic_scopes(candidates: dict[str, dict], limit: int = SCOPES_PER_TOPIC) -> list[tuple[str, list[str]]]:
    """(scope, candidate keys): the whole library when few enough decisions mention
    the topic, otherwise the committees with a usable number of them."""
    if MIN_TOPIC_GOLD <= len(candidates) <= MAX_TOPIC_GOLD:
        return [("", sorted(candidates))]
    if len(candidates) < MIN_TOPIC_GOLD:
        return []
    counts = Counter(c["committee"] for c in candidates.values() if c["committee"])
    usable = [c for c, n in counts.most_common() if MIN_TOPIC_GOLD <= n <= MAX_TOPIC_GOLD][:limit]
    return [(c, sorted(k for k, v in candidates.items() if v["committee"] == c)) for c in usable]


def scope_text(committee: str) -> str:
    if not committee:
        return ""
    return f" ב{committee}" if committee.startswith(("ועדה", "הוועדה", "ועדת")) else f" בוועדה המקומית {committee}"


def topic_prompt(topic: dict, snippets: list[str]) -> str:
    body = "\n\n".join(f"[{i}] {s}" for i, s in enumerate(snippets, start=1))
    return TOPIC_PROMPT.format(label=topic["label"], snippets=body)


def topic_item(topic: dict, committee: str, keys: list[str], answer: dict) -> dict | None:
    picked = {int(i) for i in answer.get("relevant") or [] if str(i).isdigit()}
    gold = [k for i, k in enumerate(keys, start=1) if i in picked]
    if len(gold) < 1:
        return None
    return {"question": topic["question"].format(scope=scope_text(committee)), "type": "topic", "gold": gold,
            "origin": "topic", "answer": {"topic": topic["label"], "committee": committee or None,
                                          "candidates": len(keys)},
            **({"expected_meta": {"Committee": committee}} if committee else {})}


# ── a whole set ──────────────────────────────────────────────────────────

def counts(n: int) -> dict[str, int]:
    out = {t: int(round(n * s)) for t, s in SHARES.items()}
    out["fact"] += n - sum(out.values())
    return out


def doc_prompt(doc: dict) -> str:
    refs = "; ".join(references(doc)) or "לפי כותרת ההחלטה"
    return DOC_PROMPT.format(refs=refs, k=FACTS_PER_DOC, text=doc["text"][:DOC_CHARS])


def plan(docs: list[dict], n: int, seed: int = 7) -> dict:
    """Split sampled decisions ({record_key, title, meta, text}) between the types and
    estimate the model's cost. Spends nothing. Topics are priced at their upper bound."""
    want = counts(n)
    docs = list(docs)
    random.Random(seed).shuffle(docs)
    free, used = [], set()
    for kind, make in (("named", named_question), ("block_plot", block_question)):
        made = 0
        for doc in docs:
            if made >= want[kind] or doc["record_key"] in used:
                continue
            item = make(doc, made)
            if item:
                free.append(item)
                used.add(doc["record_key"])
                made += 1
    n_docs = max(math.ceil(want["fact"] / FACTS_PER_DOC), want["analysis"])
    rest = [d for d in docs if d["record_key"] not in used and len(d.get("text") or "") >= 2000]
    model_docs = rest[:int(n_docs * eq.SPARE) + 1]
    prompts = [doc_prompt(d) for d in model_docs]
    n_topics = min(want["topic"], len(TOPICS) * SCOPES_PER_TOPIC)
    topic_chars = n_topics * (MAX_TOPIC_GOLD * (SNIPPET_CHARS + 8) + len(TOPIC_PROMPT))
    in_tokens = (sum(len(p) for p in prompts[:n_docs]) + topic_chars) / eq.CHARS_PER_TOKEN
    out_tokens = n_docs * OUT_TOKENS_DOC + n_topics * 60
    p_in, p_out = eq.price(eq.QUESTION_MODEL)
    return {"free_items": free, "docs": model_docs, "prompts": prompts, "want": want, "n_docs": n_docs,
            "n_topics": n_topics, "sampled": len(docs), "model": eq.QUESTION_MODEL,
            "input_tokens": round(in_tokens), "output_tokens": out_tokens,
            "cost_usd": round((in_tokens * p_in + out_tokens * p_out) / 1_000_000, 4)}


def _ask(ask: Callable, prompt: str, state: dict) -> dict | None:
    """One model call; counts failures and remembers a billing error (state['stopped'])."""
    try:
        answer, cost = ask(prompt)
    except Exception as exc:
        state["failures"] += 1
        message = str(exc)[:300]
        if state["failures"] >= 3 or any(s in message for s in eq.STOP_ON):
            state["stopped"] = message
        return None
    state["failures"] = 0
    state["spent"] += cost
    return answer if isinstance(answer, dict) else None


def write(planned: dict, topic_rows: Callable[[dict], list[dict]] | None = None,
          ask: Callable[[str], tuple[dict, float]] | None = None,
          on_item: Callable[[dict, float], None] | None = None,
          should_stop: Callable[[float], bool] | None = None) -> dict:
    """Write the model's questions: facts and analyses decision by decision, then topics.
    `topic_rows(topic)` returns the index rows that mention a topic. Stops like
    eval_questions.write_questions. Returns {items, spent, stopped}."""
    ask = ask or (lambda p: eq.ask_gemini(p, max_tokens=1500))
    want, items, state = planned["want"], [], {"spent": 0.0, "failures": 0, "stopped": None}
    have = Counter()

    def keep(item: dict) -> None:
        items.append(item)
        have[item["type"]] += 1
        if on_item:
            on_item(item, state["spent"])

    def halted() -> bool:
        return bool(state["stopped"]) or bool(should_stop and should_stop(state["spent"]))

    for doc, prompt in zip(planned["docs"], planned["prompts"]):
        if halted() or (have["fact"] >= want["fact"] and have["analysis"] >= want["analysis"]):
            break
        answer = _ask(ask, prompt, state)
        for item in doc_items(doc, answer or {}, k=max(0, min(FACTS_PER_DOC, want["fact"] - have["fact"]))):
            if item["type"] == "analysis" and have["analysis"] >= want["analysis"]:
                continue
            keep(item)

    for topic in TOPICS if topic_rows else []:
        if halted() or have["topic"] >= planned["n_topics"]:
            break
        try:
            candidates = topic_candidates(topic_rows(topic), topic)
        except Exception as exc:
            state["stopped"] = f"topic search: {str(exc)[:200]}"
            break
        for committee, keys in topic_scopes(candidates):
            if halted() or have["topic"] >= planned["n_topics"]:
                break
            answer = _ask(ask, topic_prompt(topic, [candidates[k]["snippet"] for k in keys]), state)
            item = topic_item(topic, committee, keys, answer or {})
            if item:
                keep(item)
    return {"items": items, "spent": round(state["spent"], 6), "stopped": state["stopped"]}
