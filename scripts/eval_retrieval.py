"""Retrieval evaluation harness: measure a retrieval change before it ships.

Runs on the owner's PC against the production server (the index lives only
there) through the worker endpoints /api/worker/eval/*. Question sets, real
user questions and run reports are kept under DATA_DIR/eval/ — never in the
repository, which is public. (The admin console's "בנצ'מרק" tab runs the same
logic on the server and keeps its sets and runs in the server's database.)

Setup: the same .env entries as scripts/index_worker.py (WORKER_API_URL,
WORKER_API_KEY); `build` also needs GOOGLE_API_KEY.

Usage:
    python scripts/eval_retrieval.py build --n 60             sample + show the cost (spends nothing)
    python scripts/eval_retrieval.py build --n 60 --yes       ...and write the questions (paid, cheap model)
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
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from config import DATA_DIR, LLM_MODEL_FAST  # noqa: E402
from src import eval_questions as eq, retrieval_eval as rev  # noqa: E402

DEFAULT_AGENT = "decisive_appraisal_decisions"
EVAL_DIR = DATA_DIR / "eval"
SETS_DIR, RUNS_DIR = EVAL_DIR / "sets", EVAL_DIR / "runs"
SAMPLE_BATCH = 200             # server cap per /sample call
BATCH = {"hybrid": 25, "pipeline": 5}


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


def _file_item(item: dict) -> dict:
    """The set file keeps the harness's original field name for the origin."""
    out = {k: v for k, v in item.items() if k != "origin"}
    out["source"] = item.get("origin")
    return out


def cmd_build(args) -> None:
    path = set_path(args.name)
    if path.exists() and not args.force:
        raise SystemExit(f"{path} exists — choose another --name or pass --force")
    server = Server()
    chunks = sample(server, args.agent, int(args.n * eq.SPARE) + 2, args.seed)  # spare for dropped questions
    planned = eq.plan(chunks, args.n, args.meta_share, args.seed)
    print(f"Sampled {planned['sampled']} decisions. {len(planned['meta_items'])} metadata questions (free), "
          f"{planned['n_llm']} written by {planned['model']}: ~{planned['input_tokens']:,} input + "
          f"~{planned['output_tokens']:,} output tokens, estimated ${planned['cost_usd']:.2f} "
          "(spare chunks are used only to replace dropped questions).")
    if not args.yes:
        print("Nothing written and nothing spent. Re-run with --yes to generate the set.")
        return

    def progress(item: dict, spent: float) -> None:
        print(f"  ${spent:.3f} so far", end="\r")

    written = eq.write_questions(planned, on_item=progress)
    all_items = [_file_item(i) for i in written["items"] + planned["meta_items"]]
    for i, item in enumerate(all_items):
        item["id"] = f"s{i:04d}"
    save_json(path, {"created": datetime.now().isoformat(timespec="seconds"), "agent_id": args.agent,
                     "seed": args.seed, "model": planned["model"], "cost_usd": round(written["spent"], 4),
                     "partial": bool(written["stopped"]), "items": all_items})
    if written["stopped"]:
        print(f"Stopped early — Gemini failed: {written['stopped']}")
    print(f"Wrote {len(all_items)} questions ({len(written['items'])} written by Gemini, "
          f"${written['spent']:.3f}) to {path}")


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
        print(f"{len(items)} questions through the full pipeline: estimated ${eq.pipeline_cost(len(items)):.2f} "
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
    b.add_argument("--n", type=int, default=60)
    b.add_argument("--name", default="synthetic")
    b.add_argument("--seed", type=int, default=7)
    b.add_argument("--meta-share", type=float, default=eq.META_SHARE, help="share of metadata questions (no LLM)")
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
