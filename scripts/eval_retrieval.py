"""Retrieval evaluation harness: measure a retrieval change before it ships.

Runs on the owner's PC against the production server (the index lives only
there) through the worker endpoints /api/worker/eval/*. Question sets, real
user questions and run reports are kept under DATA_DIR/eval/ — never in the
repository, which is public.

Setup: the same .env entries as scripts/index_worker.py (WORKER_API_URL,
WORKER_API_KEY); `build` also needs GOOGLE_API_KEY.

Usage:
    python scripts/eval_retrieval.py build --n 300            sample + show the cost (spends nothing)
    python scripts/eval_retrieval.py build --n 300 --yes      ...and write the questions (paid, cheap model)
    python scripts/eval_retrieval.py import-real              real questions -> sets/real.json, to label by hand
    python scripts/eval_retrieval.py run --mode hybrid --label baseline          free
    python scripts/eval_retrieval.py run --mode pipeline --label baseline --yes  paid (server-side Gemini)
    python scripts/eval_retrieval.py run --set real --mode hybrid
    python scripts/eval_retrieval.py compare <run A> <run B>   file name, path, or part of a name

Synthetic questions measure whether retrieval finds the decision a question was
written from. Other decisions may answer it as well, so absolute recall is a
floor; compare runs on the same set rather than reading one number alone.
"""
import argparse
import hashlib
import json
import os
import random
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from config import DATA_DIR, LLM_MODEL_FAST, MODEL_PRICES  # noqa: E402
from src import retrieval_eval as rev  # noqa: E402

DEFAULT_AGENT = "decisive_appraisal_decisions"
EVAL_DIR = DATA_DIR / "eval"
SETS_DIR, RUNS_DIR = EVAL_DIR / "sets", EVAL_DIR / "runs"
QUESTION_MODEL = os.getenv("EVAL_QUESTION_MODEL", "gemini-3.1-flash-lite")
CHARS_PER_TOKEN = 1.6          # Hebrew, measured on the corpus (src/embeddings.py)
OUT_TOKENS_PER_QUESTION = 150  # JSON answer plus minimal thinking
MAX_COPIED_WORDS = 6           # a longer run copied from the chunk drops the question
SAMPLE_BATCH = 200             # server cap per /sample call
BATCH = {"hybrid": 25, "pipeline": 5}

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


class Server:
    def __init__(self):
        self.base = os.getenv("WORKER_API_URL", "http://localhost:8000").rstrip("/")
        key = os.getenv("WORKER_API_KEY")
        if not key:
            raise SystemExit("WORKER_API_KEY is missing — add it to .env (same value as on the server)")
        self.session = requests.Session()
        self.session.headers["X-Worker-Key"] = key

    def _check(self, path: str, resp) -> dict:
        if resp.status_code >= 400:
            raise RuntimeError(f"{path}: HTTP {resp.status_code} {resp.text[:200]}")
        return resp.json()

    def post(self, path: str, payload: dict, timeout: int = 300) -> dict:
        return self._check(path, self.session.post(f"{self.base}/api/worker/eval/{path}", json=payload,
                                                   timeout=timeout))

    def get(self, path: str, params: dict) -> dict:
        return self._check(path, self.session.get(f"{self.base}/api/worker/eval/{path}", params=params,
                                                  timeout=120))


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def set_path(name: str) -> Path:
    path = Path(name)
    return path if path.suffix == ".json" and path.exists() else SETS_DIR / f"{name}.json"


def price(model: str) -> tuple[float, float]:
    best = max((m for m in MODEL_PRICES if model.startswith(m)), key=len, default=None)
    return MODEL_PRICES[best] if best else (0.0, 0.0)


# ── build ────────────────────────────────────────────────────────────────

def sample(server: Server, agent_id: str, n: int, seed: int) -> list[dict]:
    chunks, used = [], []
    while len(chunks) < n:
        got = server.post("sample", {"agent_id": agent_id, "n": min(SAMPLE_BATCH, n - len(chunks)),
                                     "seed": seed + len(chunks), "exclude": used})["chunks"]
        if not got:
            break
        chunks += got
        used += [c["record_key"] for c in got]
    return chunks


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


def ask_gemini(client, prompt: str) -> tuple[dict, float]:
    from google.genai import types

    resp = client.models.generate_content(
        model=QUESTION_MODEL, contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0, max_output_tokens=400, response_mime_type="application/json",
            thinking_config=types.ThinkingConfig(thinking_level="minimal")))
    usage = resp.usage_metadata
    p_in, p_out = price(QUESTION_MODEL)
    out_tokens = (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)
    cost = ((usage.prompt_token_count or 0) * p_in + out_tokens * p_out) / 1_000_000
    return json.loads(resp.text or "{}"), cost


def cmd_build(args) -> None:
    path = set_path(args.name)
    if path.exists() and not args.force:
        raise SystemExit(f"{path} exists — choose another --name or pass --force")
    server = Server()
    chunks = sample(server, args.agent, int(args.n * 1.15) + 2, args.seed)  # spare for dropped questions
    rng = random.Random(args.seed)
    rng.shuffle(chunks)
    n_meta = round(args.n * args.meta_share)
    meta_items, llm_chunks = [], []
    for chunk in chunks:
        blocks = sum(1 for m in meta_items if m["type"] == "block_plot")
        options = (meta_questions(chunk, len(meta_items), prefer_block=blocks * 2 <= len(meta_items))
                   if len(meta_items) < n_meta else [])
        asked = {m["question"] for m in meta_items}
        item = next((q for q in options if q["question"] not in asked), None)
        if item:
            meta_items.append({**item, "source": "meta", "record_key": chunk["record_key"]})
        else:
            llm_chunks.append(chunk)
    n_llm = args.n - len(meta_items)
    prompts = [question_prompt(c) for c in llm_chunks]
    in_tokens = sum(len(p) for p in prompts[:n_llm]) / CHARS_PER_TOKEN
    p_in, p_out = price(QUESTION_MODEL)
    cost = (in_tokens * p_in + n_llm * OUT_TOKENS_PER_QUESTION * p_out) / 1_000_000
    print(f"Sampled {len(chunks)} decisions. {len(meta_items)} metadata questions (free), "
          f"{n_llm} written by {QUESTION_MODEL}: ~{in_tokens:,.0f} input + ~{n_llm * OUT_TOKENS_PER_QUESTION:,} "
          f"output tokens, estimated ${cost:.2f} (spare chunks are used only to replace dropped questions).")
    if not args.yes:
        print("Nothing written and nothing spent. Re-run with --yes to generate the set.")
        return

    from src.embeddings import genai_client

    client, items, spent, failures, stopped = genai_client(), [], 0.0, 0, None
    for chunk, prompt in zip(llm_chunks, prompts):
        if len(items) >= n_llm:
            break
        try:
            answer, call_cost = ask_gemini(client, prompt)
            failures = 0
        except Exception as exc:
            failures += 1
            message = str(exc)[:200]
            if failures >= 3 or any(s in message for s in ("402", "RESOURCE_EXHAUSTED", "PERMISSION_DENIED")):
                stopped = message
                break
            continue
        spent += call_cost
        question = str(answer.get("question") or "").strip()
        if not answer.get("answerable") or len(question) < 8:
            continue
        if rev.longest_shared_run(question, chunk["text"]) > MAX_COPIED_WORDS:
            continue
        items.append({"question": question, "type": chunk_kind(chunk["text"]), "gold": [chunk["record_key"]],
                      "gold_section": chunk.get("section"), "source": "llm", "record_key": chunk["record_key"],
                      "chunk_id": chunk["id"]})
        if len(items) % 25 == 0:
            print(f"  {len(items)}/{n_llm} questions, ${spent:.3f} so far")
    all_items = items + meta_items
    for i, item in enumerate(all_items):
        item["id"] = f"s{i:04d}"
    save_json(path, {"created": datetime.now().isoformat(timespec="seconds"), "agent_id": args.agent,
                     "seed": args.seed, "model": QUESTION_MODEL, "cost_usd": round(spent, 4),
                     "partial": bool(stopped), "items": all_items})
    if stopped:
        print(f"Stopped early — Gemini failed: {stopped}")
    print(f"Wrote {len(all_items)} questions ({len(items)} written by Gemini, ${spent:.3f}) to {path}")


# ── import-real ──────────────────────────────────────────────────────────

REAL_INSTRUCTIONS = (
    "Real questions from the chat. To label one, put the record_key of every decision that answers it in "
    "'gold' (the admin console's question log shows which decisions each answer cited; the chunk viewer "
    "shows a decision's sections), optionally 'gold_section', 'expected_meta' ({field: value}) and a 'type' "
    "(principle / table / block_plot / metadata). Questions with an empty 'gold' and no 'expected_meta' are "
    "skipped by `run`. Re-running import-real adds new questions and keeps your labels.")


def cmd_import_real(args) -> None:
    path = set_path(args.name)
    current = load_json(path) if path.exists() else {"items": []}
    known = {i["question"] for i in current["items"]}
    fetched = Server().get("questions", {"days": args.days, "limit": args.limit})["questions"]
    added = 0
    for q in fetched:
        if q["question"] in known:
            continue
        known.add(q["question"])
        qid = "r" + hashlib.sha1(q["question"].encode("utf-8")).hexdigest()[:10]
        current["items"].append({"id": qid, "question": q["question"], "type": "real", "asked_at": q["ts"],
                                 "gold": [], "gold_section": "", "expected_meta": {}, "note": ""})
        added += 1
    current.update(instructions=REAL_INSTRUCTIONS, agent_id=args.agent,
                   updated=datetime.now().isoformat(timespec="seconds"))
    save_json(path, current)
    labelled = sum(1 for i in current["items"] if rev.is_scorable(i))
    print(f"Added {added} new questions; {len(current['items'])} in {path}, {labelled} labelled.")
    print("Label them by hand (see 'instructions' in the file). The file holds real user questions: keep it private.")


# ── run / compare ────────────────────────────────────────────────────────

def pipeline_cost(n: int) -> float:
    """Rough server-side spend: one analysis call (~1.2k tokens) and ~1.5 search
    queries, each reranking 20 chunks x 300 chars (~4k tokens), on LLM_MODEL_FAST."""
    p_in, p_out = price(LLM_MODEL_FAST)
    return n * (7_200 * p_in + 200 * p_out) / 1_000_000


def cmd_run(args) -> None:
    path = set_path(args.set)
    if not path.exists():
        raise SystemExit(f"No question set at {path} — run `build` or `import-real` first")
    qset = load_json(path)
    items = [i for i in qset["items"] if rev.is_scorable(i)][: args.limit or None]
    if not items:
        raise SystemExit("No labelled questions in this set")
    agent_id = args.agent or qset.get("agent_id") or DEFAULT_AGENT
    if args.mode == "pipeline":
        print(f"{len(items)} questions through the full pipeline: estimated ${pipeline_cost(len(items)):.2f} "
              f"of server-side Gemini ({LLM_MODEL_FAST}).")
        if not args.yes:
            print("Nothing run. Re-run with --yes to spend it.")
            return
    server, outcomes = Server(), []
    size = BATCH[args.mode]
    for start in range(0, len(items), size):
        batch = items[start:start + size]
        payload = {"agent_id": agent_id, "mode": args.mode, "k": args.k,
                   "items": [{"query": i["question"], "filters": i.get("filters")} for i in batch]}
        try:
            outcomes += server.post("search", payload)["items"]
        except Exception as exc:  # one retry, then record the batch as failed
            try:
                outcomes += server.post("search", payload)["items"]
            except Exception:
                outcomes += [{"results": [], "error": str(exc)[:200]} for _ in batch]
        print(f"  {min(start + size, len(items))}/{len(items)}", end="\r")
    print()

    report_items, degraded = [], 0
    for item, outcome in zip(items, outcomes):
        errors = outcome.get("errors") or {}
        degraded += any("semantic" in k for k in errors)
        report_items.append({
            "id": item["id"], "type": item.get("type"), "question": item["question"], "gold": item.get("gold"),
            "metrics": rev.score_item(item, outcome),
            "error": outcome.get("error") or (json.dumps(errors, ensure_ascii=False) if errors else None),
            "search_queries": outcome.get("search_queries"), "route": outcome.get("route"),
            "top": rev.doc_ranking(outcome.get("results") or [])[:10],
            "results": outcome.get("results") or [],
        })
    summary = rev.aggregate(report_items)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    label = "".join(ch for ch in (args.label or "run") if ch.isalnum() or ch in "-_")[:40] or "run"
    out = RUNS_DIR / f"{stamp}_{path.stem}_{args.mode}_{label}.json"
    save_json(out, {"label": args.label, "mode": args.mode, "agent_id": agent_id, "k": args.k,
                    "set": path.stem, "set_created": qset.get("created"), "created": stamp,
                    "summary": summary, "items": report_items})
    print(rev.summary_table(summary))
    if degraded:
        print(f"\nWARNING: {degraded} questions ran without the vector search (embedding failed) — "
              "this run is not comparable with a normal one.")
    print(f"\nSaved {out}")


def resolve_run(name: str) -> Path:
    path = Path(name)
    if path.exists():
        return path
    matches = sorted(RUNS_DIR.glob(f"*{name}*.json")) if RUNS_DIR.exists() else []
    if not matches:
        raise SystemExit(f"No run matching '{name}' in {RUNS_DIR}")
    return matches[-1]


def cmd_compare(args) -> None:
    pa, pb = resolve_run(args.a), resolve_run(args.b)
    a, b = load_json(pa), load_json(pb)
    if a.get("set") != b.get("set"):
        print(f"WARNING: different question sets ({a.get('set')} vs {b.get('set')})")
    result = rev.compare(a, b, flip_metric=args.metric)
    print(f"A = {pa.name}\nB = {pb.name}\n{result['shared']} shared questions\n")
    print(rev.delta_table(result))
    for title, rows in (("Lost", result["lost"]), ("Gained", result["gained"])):
        if rows:
            print(f"\n{title} on {result['flip_metric']} ({len(rows)}):")
            for r in rows[:args.show]:
                print(f"  [{r['type']}] {r['a']:.2f} -> {r['b']:.2f}  {r['question'][:90]}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="sample chunks and write a synthetic question set")
    b.add_argument("--n", type=int, default=300)
    b.add_argument("--name", default="synthetic")
    b.add_argument("--seed", type=int, default=7)
    b.add_argument("--meta-share", type=float, default=0.25, help="share of metadata questions (no LLM)")
    b.add_argument("--agent", default=DEFAULT_AGENT)
    b.add_argument("--yes", action="store_true", help="spend the estimated cost")
    b.add_argument("--force", action="store_true", help="overwrite an existing set")
    r = sub.add_parser("import-real", help="pull real questions to label by hand")
    r.add_argument("--name", default="real")
    r.add_argument("--days", type=int, default=90)
    r.add_argument("--limit", type=int, default=500)
    r.add_argument("--agent", default=DEFAULT_AGENT)
    u = sub.add_parser("run", help="run a set against the server and score it")
    u.add_argument("--mode", choices=("hybrid", "pipeline"), default="hybrid")
    u.add_argument("--set", default="synthetic")
    u.add_argument("--label")
    u.add_argument("--k", type=int, default=20)
    u.add_argument("--limit", type=int, default=0)
    u.add_argument("--agent")
    u.add_argument("--yes", action="store_true", help="pipeline mode: spend the estimated cost")
    c = sub.add_parser("compare", help="compare two saved runs (B against A)")
    c.add_argument("a")
    c.add_argument("b")
    c.add_argument("--metric", default="recall@10")
    c.add_argument("--show", type=int, default=10)
    args = p.parse_args()
    {"build": cmd_build, "import-real": cmd_import_real, "run": cmd_run, "compare": cmd_compare}[args.cmd](args)


if __name__ == "__main__":
    main()
