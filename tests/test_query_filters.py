"""Filters found in the question: Hebrew matching, the value dictionary, the
parser, and the count-then-decide step. Party names here are made up."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402

from src import filter_catalog, filter_step, filter_values, hebrew_match, hebrew_text, query_filters  # noqa: E402
from src.filter_conditions import Condition, from_dict, where_sql  # noqa: E402

AGENT = json.loads((Path(__file__).parent.parent / "agents" / "decisive_appraisal_decisions.json")
                   .read_text(encoding="utf-8"))
PAZ = ["פז יעקב (ג'קי)", "פז יעקב –ג'קי"]
COHENS = ["כהן אלי", "כהן אליהו", "כהן ארז"]


def record(i, committee, appraiser="אשד לבנה", party="לקוח כלשהו", block="1000", plot="1",
           date="2024-05-05", status="indexed", chars=20000):
    title = (f"הכרעת שמאי מכריע מיום 05-05-2024 בעניין היטל השבחה {party} נ ועדה מקומית {committee} "
             f"ג {block} ח {plot} - {appraiser}")
    meta = {"AppraisalHeader": title, "Committee": committee, "DecisiveAppraiser": appraiser,
            "AppraisalType": "היטל השבחה", "Block": block, "Plot": plot, "DecisionDate": date + "T00:00:00+03:00"}
    return (f"k{i}", title, json.dumps(meta, ensure_ascii=False), status, chars)


ROWS = [
    record(1, "ירושלים", party="זיתוני אלמוג", block="30607", plot="90", date="2025-03-01"),
    record(2, "ירושלים", party="זיתוני אלמוג", date="2023-01-01"),
    record(3, "ירושלים", appraiser=PAZ[0], plot="81,84,90"),
    record(4, "ירושלים", appraiser=PAZ[1]),
    record(5, "ירושלים", appraiser=COHENS[0]),
    record(6, "בית שאן", appraiser=COHENS[1]),
    record(7, "בני ברק", appraiser=COHENS[2]),
    record(8, "באר שבע"),
    record(9, "קרית אונו"),
    record(10, "תל אביב-יפו", status="catalog"),       # in the catalog, not indexed yet
]


@pytest.fixture(scope="module")
def ctx():
    return filter_catalog.build_context(AGENT, ROWS)


def detect(ctx, q):
    d = query_filters.detect(AGENT, q, ctx)
    return {c.field: c for c in d.hard}, {c.field: c for c in d.soft}


# ── Hebrew matching ──────────────────────────────────────────────────────

def test_normalize_spelling_marks_and_final_letters():
    n = hebrew_match.normalize
    assert n("וועדה") == n("ועדה") and n("קריית") == n("קרית") and n("בנייה") == n("בניה")
    assert n("פתח תקווה") == n("פתח תקוה")
    assert n("נ'") == n("נ׳") == "נ" and n("ג'קי") == "גקי"
    assert n("תל אביב-יפו") == n("תל  אביב – יפו") == "תל אביב יפו"
    assert n("שָׁמַאי") == "שמאי" and n("כהן") == "כהנ" and n("(בית)") == "בית"


def test_prefix_chains_are_real_hebrew_prefixes():
    splits = dict(hebrew_match.prefix_splits("ומהוועדה"))
    assert {"ו", "ומ", "ומה"} <= splits.keys()
    assert "בב" not in dict(hebrew_match.prefix_splits("בבאר"))
    assert dict(hebrew_match.prefix_splits("כשהשמאי"))["כשה"] == "שמאי"
    # dictionary-guided: a form is offered only when the shorter word is known
    assert hebrew_match.forms("בבית", {"בית"}) == [("בבית", 0), ("בית", 1)]
    assert hebrew_match.forms("בית", {"ית"}) == [("בית", 0), ("ית", 1)]


def test_one_edit():
    assert hebrew_match.within_one_edit("רעננה", "רעננא")
    assert hebrew_match.within_one_edit("ירושלים", "ירושלם")
    assert not hebrew_match.within_one_edit("חולון", "חולית")


# ── value dictionary ─────────────────────────────────────────────────────

def test_one_entity_per_value_with_every_stored_spelling(ctx):
    v = ctx.vocab
    assert filter_values.stored_forms(v, "DecisiveAppraiser", PAZ[1]) == sorted(PAZ)
    modiin = v.entity_for("Committee", "מודיעין - מכבים - רעות")
    assert len(modiin.stored) == 2 and "  " not in modiin.label
    assert ("AppraiserType", hebrew_match.normalize("שמאי מכריע")) in v.generic


def test_new_stored_values_join_the_dictionary_and_the_ui_lists():
    rows = ROWS + [record(99, "יישוב חדש לגמרי")]
    ctx2 = filter_catalog.build_context(AGENT, rows)
    hard, _ = detect(ctx2, "ועדה מקומית יישוב חדש לגמרי")
    assert hard["Committee"].values == ["יישוב חדש לגמרי"]
    ui = {f["name"]: f for f in filter_values.ui_filters(AGENT, ctx2.vocab)}
    assert "יישוב חדש לגמרי" in ui["Committee"]["values"]
    assert sum(v.startswith("פז יעקב") for v in ui["DecisiveAppraiser"]["values"]) == 1
    assert "1" in ui["AppraisalVersion"]["values"]   # schema values the dictionary skips stay


# ── the parser ───────────────────────────────────────────────────────────

def test_block_plot_and_years(ctx):
    hard, _ = detect(ctx, "גוש 30607 חלקה 90")
    assert hard["Block"].values == ["30607"] and hard["Plot"].values == ["90"]
    hard, _ = detect(ctx, "ועדה מקומית ירושלים 2025")
    assert hard["Committee"].values == ["ירושלים"]
    assert (hard["DecisionDate"].start, hard["DecisionDate"].end) == (20250101, 20251231)
    hard, _ = detect(ctx, "בתל אביב בין 2020 ל-2023")
    assert (hard["DecisionDate"].start, hard["DecisionDate"].end) == (20200101, 20231231)
    hard, _ = detect(ctx, "החלטות מאז 2022")
    assert (hard["DecisionDate"].start, hard["DecisionDate"].end) == (20220101, None)
    hard, _ = detect(ctx, "חלקות 5, 6 בגוש 6426")
    assert hard["Plot"].values == ["5", "6"] and hard["Block"].values == ["6426"]


@pytest.mark.parametrize("question, committee", [
    ("החלטות בבית שאן", "בית שאן"),
    ("מהוועדה המקומית בירושלים", "ירושלים"),
    ("לבני ברק", "בני ברק"),
    ("בבאר שבע", "באר שבע"),
    ("בקרית אונו", "קרית אונו"),
    ("קריית אונו", "קרית אונו"),
    ("הוועדה המקומית לתכנון ובנייה הגלבוע", "הגלבוע"),
    ("בגלבוע", "הגלבוע"),
    ("מה נקבע לגבי מקדם קומה בתל-אביב", "תל אביב-יפו"),
    ("החלטות בפתח תקווה", "פתח תקוה"),
])
def test_committee_with_prefixes_and_spelling(ctx, question, committee):
    hard, _ = detect(ctx, question)
    assert hard["Committee"].values == [committee]   # the stored value, never "בבית שאן"


def test_party_name_from_titles(ctx):
    hard, _ = detect(ctx, "זיתוני אלמוג נ' ועדה מקומית ירושלים")
    assert hard["Committee"].values == ["ירושלים"]
    assert hard["_decision"].values == ["k1", "k2"]
    hard, _ = detect(ctx, "זיתוני נגד ירושלים")          # one word + "נגד" is enough
    assert hard["_decision"].values == ["k1", "k2"] and hard["Committee"].values == ["ירושלים"]
    hard, _ = detect(ctx, "ההחלטה בעניין לזיתוני")        # prefix removed: the word is in titles
    assert hard["_decision"].values == ["k1", "k2"]
    hard, soft = detect(ctx, "מה קרה לזיתוני אלמוג")      # no "נ'"/"נגד": ordinary words also occur in titles
    assert "_decision" not in hard and "_decision" not in soft
    hard, soft = detect(ctx, "היטל השבחה בגין לקוח כלשהו")  # title words, but no party cue
    assert "_decision" not in hard


def test_appraiser_name_orders_and_nicknames(ctx):
    for q in ("לבנה אשד", "אשד לבנה", "החלטות של השמאית לבנה אשד"):
        hard, _ = detect(ctx, q)
        assert hard["DecisiveAppraiser"].values == ["אשד לבנה"], q
    hard, _ = detect(ctx, "ג'קי פז")
    assert hard["DecisiveAppraiser"].values == sorted(PAZ)


def test_shared_family_name_is_never_one_appraiser(ctx):
    hard, soft = detect(ctx, "כהן")                      # could be a party: ranking only
    assert "DecisiveAppraiser" not in hard and soft["DecisiveAppraiser"].values == COHENS
    hard, soft = detect(ctx, "השמאי כהן")                 # explicitly an appraiser: any of the three
    assert hard["DecisiveAppraiser"].values == COHENS


def test_uncertain_matches_do_not_filter(ctx):
    hard, soft = detect(ctx, "החלטות ברעננא")              # one typo
    assert "Committee" not in hard and soft["Committee"].values == ["רעננה"]
    hard, soft = detect(ctx, "השמאית אשך לבנה")
    assert "DecisiveAppraiser" not in hard and soft["DecisiveAppraiser"].values == ["אשד לבנה"]
    hard, soft = detect(ctx, "החלטות באזור המרכז")         # "אזור" is an everyday word
    assert "Committee" not in hard and "Committee" not in soft
    hard, soft = detect(ctx, "עסקאות ברחובות דוד ילין בתל אביב")
    assert hard["Committee"].values == ["תל אביב-יפו"] and "Committee" not in soft
    hard, _ = detect(ctx, "ועדה מקומית אזור")
    assert hard["Committee"].values == ["אזור"]


def test_types_versions_and_the_collection_name(ctx):
    hard, _ = detect(ctx, "שומה מתוקנת אחרי ערר בחולון")
    assert hard["AppraisalVersion"].values == ["שומה מתוקנת אחרי ערר"]
    hard, _ = detect(ctx, "החלטות שמאי מכריע בהיטל השבחה")
    assert "AppraiserType" not in hard                   # the corpus name, not a filter
    assert hard["AppraisalType"].values == ["היטל השבחה"]
    hard, soft = detect(ctx, "פיצויים על הפקעה")
    assert not hard and "AppraisalType" in soft


# ── conditions ───────────────────────────────────────────────────────────

def test_plot_with_several_numbers_matches_each_of_them():
    c = Condition("Plot", "num", ["90"])
    assert c.test({"Plot": "81,84,90"}) and c.test({"Plot": "90"}) and not c.test({"Plot": "190"})
    assert "regexp_like(Plot" in c.to_sql()
    assert where_sql([Condition("Committee", "in", ["תל אביב' OR 1=1"])]) == \
        "Committee IN ('תל אביב'' OR 1=1')"


def test_inherited_filters_are_validated():
    assert from_dict(AGENT, {"field": "_decision", "kind": "keys", "values": ["k1"]}).values == ["k1"]
    assert from_dict(AGENT, {"field": "_decision", "kind": "keys", "values": ["x' OR '1'='1"]}) is None
    assert from_dict(AGENT, {"field": "Plot", "kind": "num", "values": ["9 OR 1"]}) is None
    assert from_dict(AGENT, {"field": "Nope", "kind": "in", "values": ["a"]}) is None
    assert from_dict(AGENT, {"field": "DecisionDate", "kind": "range", "start": "1; DROP"}) is None


# ── count, then decide ───────────────────────────────────────────────────

def test_zero_matches_drop_the_found_filter_and_say_so(ctx):
    plan = filter_step.plan_filters(AGENT, "ועדה מקומית אשדוד", ctx=ctx)
    assert plan.mode == "relaxed" and plan.where is None
    assert plan.dropped[0].values == ["אשדוד"] and "לא נמצאו" in plan.note
    assert "Committee IN ('אשדוד')" in plan.soft_where   # still preferred in ranking


def test_few_matches_are_read_whole_and_many_are_filtered(ctx):
    plan = filter_step.plan_filters(AGENT, "גוש 30607 חלקה 90", ctx=ctx)
    assert plan.mode == "full_read" and plan.keys == ["k1"] and plan.count == 1
    assert plan.sources[0].startswith("הכרעת שמאי מכריע")
    plan = filter_step.plan_filters(AGENT, "חלקה 90", ctx=ctx)
    assert plan.keys == ["k1", "k3"]                      # "81,84,90" counts too
    plan = filter_step.plan_filters(AGENT, "היטל השבחה בירושלים", ctx=ctx)
    assert plan.mode == "filtered" and plan.count == 5
    assert "Committee IN ('ירושלים')" in plan.where
    plan = filter_step.plan_filters(AGENT, "החלטות בתל אביב", ctx=ctx)   # catalogued, not indexed
    assert plan.mode == "relaxed"


def test_manual_choice_wins_and_removed_chips_stay_removed(ctx):
    plan = filter_step.plan_filters(AGENT, "החלטות בבני ברק", {"Committee": "ירושלים"}, ctx=ctx)
    assert [c.values for c in plan.hard] == [["ירושלים"]] and plan.hard[0].source == "manual"
    plan = filter_step.plan_filters(AGENT, "ג'קי פז", {"DecisiveAppraiser": PAZ[0]}, ctx=ctx)
    assert plan.hard[0].values == sorted(PAZ)             # a panel choice covers every spelling
    plan = filter_step.plan_filters(AGENT, "החלטות בבני ברק", disabled=["Committee"], ctx=ctx)
    assert plan.mode == "none" and not plan.hard


def test_follow_up_keeps_the_previous_filters(ctx):
    first = filter_step.plan_filters(AGENT, "גוש 30607 חלקה 90", ctx=ctx).to_dict()
    assert (first["inherit"][-1]["field"], first["inherit"][-1]["values"]) == ("_decision", ["k1"])
    plan = filter_step.plan_filters(AGENT, "ומה נקבע באותה החלטה לגבי מקדם?", inherited=first["inherit"], ctx=ctx)
    assert plan.mode == "full_read" and plan.keys == ["k1"]
    assert all(c.source == "inherited" for c in plan.hard)
    plan = filter_step.plan_filters(AGENT, "מה נקבע לגבי מקדם קומה?", inherited=first["inherit"], ctx=ctx)
    assert plan.mode == "none"                           # a new question starts clean


def test_a_preference_most_decisions_share_is_dropped(ctx):
    # every decision in ROWS is "היטל השבחה": preferring it would only reshuffle the ranking
    plan = filter_step.plan_filters(AGENT, "כיצד יש לחלק את חבות היטל ההשבחה במכר", ctx=ctx)
    assert not plan.soft and plan.soft_where is None
    plan = filter_step.plan_filters(AGENT, "החלטות ברעננא", ctx=ctx)   # a narrow preference stays
    assert [c.values for c in plan.soft] == [["רעננה"]]


def test_without_a_catalog_snapshot_found_filters_still_apply():
    empty = filter_catalog.build_context(AGENT, [])
    plan = filter_step.plan_filters(AGENT, "החלטות בבית שאן", ctx=empty)
    assert plan.mode == "filtered" and plan.count is None and "בית שאן" in plan.where


# ── keyword search spelling ──────────────────────────────────────────────

def test_keyword_query_asks_for_both_spellings():
    terms = hebrew_text.keyword_query("בקרית אונו").split()
    assert {"קרית", "קריית", "אונו"} <= set(terms)
    assert "וועדה" in hebrew_text.keyword_query("ועדה").split()
    assert "בנייה" in hebrew_text.keyword_query("בניה").split()
    assert "הועדה" in hebrew_text.keyword_query("הוועדה").split()
    assert "קריית" not in hebrew_text.keyword_query("בקרית אונו", with_spellings=False).split()


def test_the_admin_switch_turns_spelling_variants_off(monkeypatch):
    from src import admin_store, hybrid_index
    monkeypatch.setattr(admin_store, "get_settings", lambda: {"keyword_spellings": False})
    assert hybrid_index._spellings_on() is False
    monkeypatch.setattr(admin_store, "get_settings", lambda: {})
    assert hybrid_index._spellings_on() is True


# ── The filter panel: matching count, list, and the title field ──────────

def test_panel_count_and_newest_first(ctx, monkeypatch):
    monkeypatch.setattr(filter_catalog, "get_context", lambda agent, wait=True: ctx)
    assert filter_step.matching_documents(AGENT, {}) == {"count": 9, "total": 9, "documents": []}
    out = filter_step.matching_documents(AGENT, {"Committee": "ירושלים"}, limit=2)
    assert out["count"] == 5 and [d["key"] for d in out["documents"]] == ["k1", "k3"]
    assert out["documents"][0]["date"] == "01.03.2025"
    dated = filter_step.matching_documents(AGENT, {"DecisionDate": {"from": "2024-01-01", "to": "2024-12-31"}})
    assert dated["count"] == 7


def test_title_field_filters_by_every_word(ctx, monkeypatch):
    monkeypatch.setattr(filter_catalog, "get_context", lambda agent, wait=True: ctx)
    out = filter_step.matching_documents(AGENT, {"AppraisalHeader": "אלמוג  זיתוני"}, limit=5)
    assert {d["key"] for d in out["documents"]} == {"k1", "k2"}
    plan = filter_step.plan_filters(AGENT, "מה נקבע?", {"AppraisalHeader": "זיתוני 50%_x"}, ctx=ctx)
    assert plan.where == "source LIKE '%זיתוני%' AND source LIKE '%50%' AND source LIKE '%x%'"
