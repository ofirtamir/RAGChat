"""Retrieval evaluation: metrics over a question set with known answers.

Pure logic, no I/O — scripts/eval_retrieval.py fetches the rankings from the
server and saves the reports; this module scores them.

Scoring is at the level of the *decision* (record_key): a result counts when it
comes from a gold decision, because the answer model gets the whole section
around a hit anyway. Positions are result positions (chunks), so recall@10
means "a chunk of the gold decision is among the ten chunks the answer model
reads", not "among ten distinct decisions".

A question item:   {"id", "question", "type", "gold": [record_key, ...],
                    "gold_section"?: str, "expected_meta"?: {field: value}, "filters"?: {...}}
A ranked result:   {"record_key", "section", "rank", "stages": [...], ...}
"""
from __future__ import annotations

import re
from statistics import mean

RECALL_AT = (5, 10, 20)
MRR_AT = 20
DISTINCT_AT = 10
FILTER_AT = 10

# the order the summary table prints them in; True = higher is better
METRICS = {
    "recall@5": True, "recall@10": True, "recall@20": True, "mrr@20": True, "hit@1": True,
    "section_hit@10": True, "distinct@10": True, "filter_acc@10": True,
    "vector_recall@20": True, "keyword_recall@20": True, "candidate_recall@20": True,
}


# ── one question ─────────────────────────────────────────────────────────

def ranked(results: list[dict]) -> list[dict]:
    """The results that hold a final rank, in rank order. Results without one were
    only found by a single search method (reported for the per-stage numbers)."""
    return sorted((r for r in results if r.get("rank")), key=lambda r: r["rank"])


def doc_ranking(results: list[dict]) -> list[str]:
    """record_key of each ranked result, in order (repeats kept: positions are chunks)."""
    return [str(r.get("record_key") or "") for r in ranked(results)]


def recall_at(ranking: list[str], gold: set[str], k: int) -> float:
    return len(gold & set(ranking[:k])) / len(gold) if gold else 0.0


def mrr_at(ranking: list[str], gold: set[str], k: int = MRR_AT) -> float:
    for pos, key in enumerate(ranking[:k], start=1):
        if key in gold:
            return 1.0 / pos
    return 0.0


def _norm(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def meta_matches(meta: dict | None, expected: dict) -> bool:
    """Every expected field matches. Dates match on a prefix ("2023" matches
    "2023-05-01"); other values must be equal after whitespace clean-up."""
    if not meta:
        return False
    for field, want in expected.items():
        have, want = _norm(meta.get(field)), _norm(want)
        if not have:
            return False
        if field.endswith("Date"):
            if not have.startswith(want):
                return False
        elif have != want:
            return False
    return True


def filter_accuracy(results: list[dict], expected: dict, metas: dict[str, dict],
                    k: int = FILTER_AT) -> float | None:
    """Share of the top-k results whose decision has the expected metadata.
    None when nothing was returned or no metadata is known for the results."""
    top = ranked(results)[:k]
    known =[r for r in top if r.get("record_key") in metas]
    if not known:
        return None
    return sum(meta_matches(metas[r["record_key"]], expected) for r in known) / len(known)


def stage_ranking(results: list[dict], stage: str) -> list[str]:
    """The ranking one search method produced on its own (vector / keyword),
    rebuilt from the per-result stage ranks the server reports."""
    field = f"{stage}_rank"
    ranked = sorted((r for r in results if r.get(field)), key=lambda r: r[field])
    return [str(r.get("record_key") or "") for r in ranked]


def score_item(item: dict, outcome: dict) -> dict:
    """Metrics for one question. `outcome` is one entry of the server's /search
    answer: {"results", "candidates"?, "documents"?: {record_key: meta}, "error"?}."""
    results = outcome.get("results") or []
    gold = {g for g in item.get("gold") or [] if g}
    ranking = doc_ranking(results)
    out: dict[str, float | None] = {}
    if gold:
        for k in RECALL_AT:
            out[f"recall@{k}"] = recall_at(ranking, gold, k)
        out["mrr@20"] = mrr_at(ranking, gold)
        out["hit@1"] = 1.0 if ranking[:1] and ranking[0] in gold else 0.0
        if item.get("gold_section"):
            top = ranked(results)[:10]
            out["section_hit@10"] = 1.0 if any(
                r.get("record_key") in gold and r.get("section") == item["gold_section"] for r in top) else 0.0
        if any(r.get("vector_rank") for r in results):
            out["vector_recall@20"] = recall_at(stage_ranking(results, "vector"), gold, 20)
        if any(r.get("keyword_rank") for r in results):
            out["keyword_recall@20"] = recall_at(stage_ranking(results, "keyword"), gold, 20)
        if outcome.get("candidates") is not None:
            out["candidate_recall@20"] = recall_at(doc_ranking(outcome["candidates"]), gold, 20)
    out["distinct@10"] = float(len({k for k in ranking[:DISTINCT_AT] if k}))
    if item.get("expected_meta"):
        out["filter_acc@10"] = filter_accuracy(results, item["expected_meta"], outcome.get("documents") or {})
    return out


def is_scorable(item: dict) -> bool:
    """A question can be scored once it has a gold decision or expected metadata
    (hand-imported real questions start with neither)."""
    return bool([g for g in item.get("gold") or [] if g] or item.get("expected_meta"))


# ── a whole run ──────────────────────────────────────────────────────────

def aggregate(scored: list[dict]) -> dict[str, dict]:
    """Mean of every metric per question type, plus "all". Each scored entry is
    {"type", "metrics", "error"?}. A metric's mean only covers the questions
    where it is defined; "n" counts the questions of the type."""
    groups: dict[str, list[dict]] = {"all": []}
    for entry in scored:
        groups["all"].append(entry)
        groups.setdefault(entry.get("type") or "other", []).append(entry)
    summary = {}
    for name, entries in groups.items():
        row: dict[str, float | int | None] = {"n": len(entries),
                                              "errors": sum(1 for e in entries if e.get("error"))}
        for metric in METRICS:
            values = [e["metrics"][metric] for e in entries if e["metrics"].get(metric) is not None]
            if values:
                row[metric] = round(mean(values), 4)
        summary[name] = row
    return summary


def compare(a: dict, b: dict, flip_metric: str = "recall@10") -> dict:
    """Run B against run A (reports as saved by the script). Returns the delta
    of every metric per type, and the questions that flipped on `flip_metric`
    (gained / lost) — matched by question id, so both runs must use the same set."""
    deltas: dict[str, dict] = {}
    for qtype in sorted(set(a["summary"]) | set(b["summary"]), key=lambda t: (t != "all", t)):
        sa, sb = a["summary"].get(qtype, {}), b["summary"].get(qtype, {})
        rows = {}
        for metric in ("n", *METRICS):
            va, vb = sa.get(metric), sb.get(metric)
            if va is None and vb is None:
                continue
            rows[metric] = {"a": va, "b": vb,
                            "delta": round(vb - va, 4) if va is not None and vb is not None else None}
        deltas[qtype] = rows
    a_items = {i["id"]: i for i in a.get("items", [])}
    gained, lost = [], []
    for item in b.get("items", []):
        before = a_items.get(item["id"])
        if before is None:
            continue
        va, vb = before["metrics"].get(flip_metric), item["metrics"].get(flip_metric)
        if va is None or vb is None or va == vb:
            continue
        entry = {"id": item["id"], "type": item.get("type"), "question": item.get("question"), "a": va, "b": vb}
        (gained if vb > va else lost).append(entry)
    shared = len(set(a_items) & {i["id"] for i in b.get("items", [])})
    return {"deltas": deltas, "gained": gained, "lost": lost, "flip_metric": flip_metric, "shared": shared}


# ── text tables ──────────────────────────────────────────────────────────

def _fmt(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, int):
        return str(value)
    return f"{value:.3f}"


def summary_table(summary: dict[str, dict]) -> str:
    """Compact metrics x question-type table for the terminal."""
    metrics = [m for m in ("n", "errors", *METRICS) if any(m in row for row in summary.values())]
    types = sorted(summary, key=lambda t: (t != "all", t))
    width = max(14, *(len(m) + 1 for m in metrics))
    lines = ["".ljust(width) + "".join(t[:12].rjust(13) for t in types)]
    for m in metrics:
        lines.append(m.ljust(width) + "".join(_fmt(summary[t].get(m)).rjust(13) for t in types))
    return "\n".join(lines)


def delta_table(comparison: dict) -> str:
    """A, B and B-A per metric, one block per question type; worse values marked."""
    lines = []
    for qtype, rows in comparison["deltas"].items():
        lines.append(f"[{qtype}]")
        lines.append("metric".ljust(22) + "A".rjust(9) + "B".rjust(9) + "B-A".rjust(9))
        for metric, v in rows.items():
            mark = ""
            if v["delta"] and metric in METRICS:
                worse = v["delta"] < 0 if METRICS[metric] else v["delta"] > 0
                mark = "  <-- worse" if worse and metric != "distinct@10" else ""
            delta = "-" if v["delta"] is None else f"{v['delta']:+.3f}"
            lines.append(metric.ljust(22) + _fmt(v["a"]).rjust(9) + _fmt(v["b"]).rjust(9) + delta.rjust(9) + mark)
        lines.append("")
    return "\n".join(lines).rstrip()


# ── question-set hygiene ─────────────────────────────────────────────────

def _words(text: str) -> list[str]:
    return re.findall(r"\d+(?:[.,]\d+)*|[\w\"'״׳]+", text or "")


def longest_shared_run(question: str, source: str) -> int:
    """Length, in words, of the longest word sequence the question copies from
    the source. Used to drop generated questions that just quote the chunk —
    those measure string matching, not retrieval of meaning."""
    q, s = _words(question), _words(source)
    if not q or not s:
        return 0
    best, prev = 0, [0] * (len(s) + 1)
    for qw in q:
        cur = [0] * (len(s) + 1)
        for j, sw in enumerate(s, start=1):
            if qw == sw:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best
