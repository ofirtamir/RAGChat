"""Filter values from the analyzer model: the prompt section, checking the model's
answer against the value lists, merging with the regexes, and loosening filters
that match nothing. The model itself is faked; party names here are made up."""
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

import pytest  # noqa: E402
from langchain_core.messages import AIMessage, SystemMessage  # noqa: E402
from langchain_core.runnables import RunnableLambda  # noqa: E402

import test_query_filters as base  # noqa: E402  (module only: its tests are not collected twice)
from src import filter_catalog, filter_relax, filter_step, model_filters, query_analyzer  # noqa: E402

AGENT = base.AGENT


@pytest.fixture(scope="module")
def ctx():
    return filter_catalog.build_context(AGENT, base.ROWS)


@pytest.fixture(autouse=True)
def catalog_rows(monkeypatch):
    from src import library_store
    monkeypatch.setattr(library_store, "filter_rows", lambda agent_id: base.ROWS)


def parse(ctx, *items):
    hard, soft = model_filters.parse(AGENT, ctx, list(items))
    return {c.field: c for c in hard}, {c.field: c for c in soft}


# ── the prompt ───────────────────────────────────────────────────────────

def test_prompt_lists_every_allowed_value_once(ctx):
    text = model_filters.prompt_section(AGENT, ctx, today=date(2026, 11, 21))
    committees = next(line for line in text.splitlines() if line.startswith("- Committee"))
    assert "ירושלים" in committees and "בית שאן" in committees
    appraisers = next(line for line in text.splitlines() if line.startswith("- DecisiveAppraiser"))
    assert sum(p in appraisers for p in base.PAZ) == 1          # two stored spellings, one choice
    roles = next(line for line in text.splitlines() if line.startswith("- AppraiserType"))
    assert "שמאי מייעץ" in roles and "שמאי מכריע" not in roles  # the collection's own name is no filter
    assert "- Block (גוש). Digits only." in text and "- DecisionDate" in text
    assert "AppraisalHeader" not in text and "2026-11-21" in text


def test_prompt_without_a_catalog_uses_the_schema_values():
    text = model_filters.prompt_section(AGENT, None)
    assert "- Committee" in text and "שמאי מייעץ" in text


# ── the model's answer, checked ──────────────────────────────────────────

def test_listed_values_filter_with_every_stored_spelling(ctx):
    hard, soft = parse(ctx, {"field": "DecisiveAppraiser", "value": base.PAZ[0], "explicit": True},
                       {"field": "Committee", "value": "בית שאן", "explicit": True})
    assert hard["DecisiveAppraiser"].values == sorted(base.PAZ)
    assert hard["Committee"].values == ["בית שאן"] and not soft


def test_values_not_in_the_list_are_dropped(ctx):
    hard, soft = parse(ctx, {"field": "Committee", "value": "עיר שאינה קיימת"},
                       {"field": "AppraiserType", "value": "שמאי מכריע"},     # names the whole collection
                       {"field": "Nope", "value": "x"}, {"field": "AppraisalHeader", "value": "זיתוני"},
                       "not a dict", {"field": "Block", "value": "abc"},
                       {"field": "DecisionDate", "from": "2022-13-01"})
    assert not hard and not soft
    assert model_filters.parse(AGENT, ctx, {"field": "Committee"}) == ([], [])   # not a list


def test_guesses_and_typos_only_prefer(ctx):
    hard, soft = parse(ctx, {"field": "Committee", "value": "רעננא", "explicit": True},   # one typo
                       {"field": "DecisiveAppraiser", "value": base.COHENS[0], "explicit": False},
                       {"field": "DecisiveAppraiser", "value": base.COHENS[1], "explicit": False})
    assert not hard
    assert soft["Committee"].values == ["רעננה"]
    assert soft["DecisiveAppraiser"].values == base.COHENS[:2]
    hard, soft = parse(ctx, {"field": "AppraiserType", "value": "שמאי מייעץ", "explicit": False})
    assert not hard and not soft                          # a guess on a short list is dropped


def test_a_plain_committee_name_filters_even_when_the_model_hedges(ctx):
    # "מה נקבע לגבי בנייה ברחובות העיר תל אביב?": the model marked תל אביב not explicit
    hard, soft = parse(ctx, {"field": "Committee", "value": "תל אביב-יפו", "explicit": False},
                       {"field": "DecisiveAppraiser", "value": base.COHENS[0], "explicit": False})
    assert hard["Committee"].values == ["תל אביב-יפו"]
    assert soft["DecisiveAppraiser"].values == [base.COHENS[0]]      # an appraiser guess still only prefers
    hard, soft = parse(ctx, {"field": "Committee", "value": "רחובות", "explicit": False})
    assert not hard and soft["Committee"].values == ["רחובות"]        # also an everyday word


def test_several_values_of_one_field_are_any_of_them(ctx):
    hard, _ = parse(ctx, *({"field": "DecisiveAppraiser", "value": v, "explicit": True} for v in base.COHENS),
                    {"field": "Plot", "value": "81"}, {"field": "Plot", "value": "84, 90"})
    assert hard["DecisiveAppraiser"].values == base.COHENS
    assert hard["Plot"].values == ["81", "84", "90"] and hard["Plot"].kind == "num"


def test_dates(ctx):
    hard, _ = parse(ctx, {"field": "DecisionDate", "from": "2022-01-01", "to": "2022-12-31"})
    c = hard["DecisionDate"]
    assert (c.start, c.end, c.display) == (20220101, 20221231, "2022")
    hard, _ = parse(ctx, {"field": "PublicityDate", "to": "2021-06-30", "from": "2023-01-01"})   # swapped
    assert (hard["PublicityDate"].start, hard["PublicityDate"].end) == (20210630, 20230101)
    hard, soft = parse(ctx, {"field": "DecisionDate", "from": "2024-11-21", "explicit": False},   # as the model
                       {"field": "DecisionDate", "to": "2025-11-20", "explicit": False})       # answers "בשנתיים האחרונות"
    assert (hard["DecisionDate"].start, hard["DecisionDate"].end) == (20241121, None) and not soft
    hard, _ = parse(ctx, {"field": "DecisionDate", "from": "2020"})
    assert (hard["DecisionDate"].start, hard["DecisionDate"].end, hard["DecisionDate"].display) == (20200101, None, "מ-2020")


def test_range_display():
    show = model_filters.range_display
    assert show(20200101, 20221231) == "2020–2022"
    assert show(20210315, 20210601) == "15.03.2021–01.06.2021"
    assert show(None, 20181231) == "עד 2018" and show(20210315, None) == "מ-15.03.2021"


# ── merged into the plan ─────────────────────────────────────────────────

def test_the_regexes_add_numbers_and_dates_the_model_missed(ctx):
    items = [{"field": "Committee", "value": "ירושלים", "explicit": True}]
    plan = filter_step.plan_filters(AGENT, "גוש 30607 בירושלים", ctx=ctx, model_items=items)
    assert {c.field for c in plan.hard} == {"Committee", "Block"} and plan.keys == ["k1"]
    items.append({"field": "DecisionDate", "from": "2025-01-01", "to": "2025-12-31"})
    plan = filter_step.plan_filters(AGENT, "בירושלים בשנה שעברה", ctx=ctx, model_items=items)
    assert [c.display for c in plan.hard if c.kind == "range"] == ["2025"]   # a relative date: the model's


def test_written_years_and_numbers_come_from_the_regexes(ctx):
    items = [{"field": "Committee", "value": "ירושלים"},                     # the model read "מ-2022" as 2022
             {"field": "DecisionDate", "from": "2022-01-01", "to": "2022-12-31"}]
    plan = filter_step.plan_filters(AGENT, "בירושלים מ-2022", ctx=ctx, model_items=items)
    assert [(c.start, c.end) for c in plan.hard if c.kind == "range"] == [(20220101, None)]
    items = [{"field": "Plot", "value": "90"}]                                # "מגרש 90" is no plot
    plan = filter_step.plan_filters(AGENT, "זכויות במגרש 90", ctx=ctx, model_items=items)
    assert not plan.hard and [c.field for c in plan.soft] == ["Plot"]
    items = [{"field": "PublicityDate", "from": "2024-01-01", "to": "2024-12-31"}]
    found = filter_step._detect(AGENT, "שפורסמו ב-2025", ctx, items)
    assert [(c.field, c.display) for c in found.hard] == [("PublicityDate", "2025")]   # the model's field


def test_the_models_answer_replaces_the_dictionary_parser(ctx):
    plan = filter_step.plan_filters(AGENT, "החלטות בבית שאן", ctx=ctx, model_items=[])
    assert plan.mode == "none" and not plan.hard          # the model saw no filter
    plan = filter_step.plan_filters(AGENT, "החלטות בבית שאן", ctx=ctx, model_items=None)
    assert plan.hard[0].values == ["בית שאן"]             # no model answer: the parser decides


# ── zero matches: loosen step by step ────────────────────────────────────

def test_zero_matches_drop_the_least_telling_filter_only(ctx):
    items = [{"field": "Committee", "value": "ירושלים"}, {"field": "DecisiveAppraiser", "value": base.COHENS[1]}]
    plan = filter_step.plan_filters(AGENT, "השמאי כהן אליהו בירושלים", ctx=ctx, model_items=items)
    assert [c.field for c in plan.hard] == ["Committee"] and plan.count == 5
    assert [c.field for c in plan.dropped] == ["DecisiveAppraiser"] and plan.mode == "filtered"
    assert "הוסר הסינון לפי שמאי מכריע" in plan.note
    assert "DecisiveAppraiser IN ('כהן אליהו')" in plan.soft_where   # still preferred in ranking


def test_zero_matches_widen_the_dates_first(ctx):
    items = [{"field": "Committee", "value": "ירושלים"}, {"field": "DecisionDate", "from": "2022-01-01",
                                                          "to": "2022-12-31"}]
    plan = filter_step.plan_filters(AGENT, "ירושלים 2022", ctx=ctx, model_items=items)
    assert plan.keys == ["k2"] and plan.mode == "full_read" and not plan.dropped
    assert "הורחב ל-2021–2023" in plan.note
    assert [(c.start, c.end) for c in plan.hard if c.kind == "range"] == [(20210101, 20231231)]


def test_zero_matches_say_when_decisions_are_not_indexed_yet(ctx):
    items = [{"field": "Committee", "value": "תל אביב-יפו"}]
    plan = filter_step.plan_filters(AGENT, "החלטות בתל אביב", ctx=ctx, model_items=items)
    assert plan.mode == "relaxed" and "עוד לא נקלטה לחיפוש" in plan.note


def test_a_manual_choice_is_never_loosened(ctx):
    plan = filter_step.plan_filters(AGENT, "מה נקבע?", {"Committee": "אשדוד"}, ctx=ctx, model_items=[])
    assert plan.hard[0].values == ["אשדוד"] and plan.count == 0 and "שבחרת" in plan.note
    items = [{"field": "DecisiveAppraiser", "value": base.COHENS[1]}]   # found + manual: only the found one goes
    plan = filter_step.plan_filters(AGENT, "השמאי כהן אליהו", {"Committee": "ירושלים"}, ctx=ctx, model_items=items)
    assert [c.source for c in plan.hard] == ["manual"] and plan.dropped[0].field == "DecisiveAppraiser"


def test_relax_gives_up_when_only_dropping_everything_helps(ctx):
    found = filter_step.plan_filters(AGENT, "גוש 1 חלקה 2", ctx=ctx, model_items=[
        {"field": "Block", "value": "1"}, {"field": "Plot", "value": "2"}]).dropped
    assert filter_relax.relax(ctx, [], found) is None   # nothing matches either number


# ── the analyzer call ────────────────────────────────────────────────────

def _fake_llm(answer: dict, seen: list):
    def run(prompt_value):
        seen.extend(prompt_value.to_messages())
        return AIMessage(content=json.dumps(answer, ensure_ascii=False))
    return RunnableLambda(run)


def test_analyzer_asks_for_filters_in_the_same_call(ctx, monkeypatch):
    seen = []
    answer = {"needs_retrieval": True, "rewritten_queries": ["מקדם קומה"], "reasoning": "x",
              "filters": [{"field": "Committee", "value": "ירושלים", "explicit": True}, "junk"]}
    monkeypatch.setattr(query_analyzer, "_get_analyzer_llm", lambda: _fake_llm(answer, seen))
    monkeypatch.setattr(query_analyzer, "load_prompt", lambda name, fallback: fallback)
    monkeypatch.setattr(filter_catalog, "get_context", lambda agent, wait=True: ctx)
    out = query_analyzer.analyze_query("מקדם קומה בירושלים", [], agent=AGENT)
    assert out.filters == [{"field": "Committee", "value": "ירושלים", "explicit": True}]
    systems = [m for m in seen if isinstance(m, SystemMessage)]
    assert len(systems) == 2 and "TASK 3: FILTERS" in systems[1].content


def test_analyzer_without_an_agent_or_with_a_bad_answer(ctx, monkeypatch):
    seen = []
    answer = {"needs_retrieval": True, "rewritten_queries": ["x"], "reasoning": "x", "filters": {"bad": 1}}
    monkeypatch.setattr(query_analyzer, "_get_analyzer_llm", lambda: _fake_llm(answer, seen))
    monkeypatch.setattr(query_analyzer, "load_prompt", lambda name, fallback: fallback)
    monkeypatch.setattr(filter_catalog, "get_context", lambda agent, wait=True: ctx)
    assert query_analyzer.analyze_query("x", [], agent=AGENT).filters == []   # malformed: none found
    out = query_analyzer.analyze_query("x", [])
    assert out.filters is None and out.rewritten_queries == ["x"]            # not asked: parser fallback
