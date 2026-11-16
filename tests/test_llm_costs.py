"""The cheaper-model setup: which model each job uses, its thinking settings, and cost accounting."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, LLMResult  # noqa: E402

import config  # noqa: E402
from src import llm  # noqa: E402
from src.usage_tracker import UsageCollector, model_price  # noqa: E402


def thinking(model) -> dict:
    cfg = model._prepare_params(stop=None).thinking_config
    return {k: v for k, v in cfg.model_dump().items() if v is not None} if cfg else {}


def test_defaults_are_the_cheap_models():
    assert config.LLM_MODEL == "gemini-3.1-flash-lite"
    assert config.LLM_MODEL_FAST == "gemini-2.5-flash-lite"
    for model in (config.LLM_MODEL, config.LLM_MODEL_FAST):
        price_in, price_out = config.MODEL_PRICES[model]
        assert price_in <= 0.25 and price_out <= 1.50


def test_answer_model_thinks_little_and_decision_model_not_at_all():
    answer, fast = llm.answer_llm(), llm.fast_llm()
    assert answer.model.endswith(config.LLM_MODEL) and fast.model.endswith(config.LLM_MODEL_FAST)
    assert str(thinking(answer)["thinking_level"]).lower().endswith("low")
    assert thinking(fast)["thinking_budget"] == 0
    assert fast.temperature == 0


def test_thinking_parameter_matches_the_model_family():
    assert llm._thinking_kwargs("gemini-2.5-flash-lite", "off") == {"thinking_budget": 0}
    assert llm._thinking_kwargs("gemini-3.1-flash-lite", "off") == {"thinking_level": "minimal"}
    assert llm._thinking_kwargs("gemini-3.8-flash", "medium") == {"thinking_level": "medium"}


def test_every_pipeline_step_uses_the_shared_factories():
    from src import full_doc_detector, graph, query_analyzer, reranker

    assert graph._get_llm().model.endswith(config.LLM_MODEL)
    for factory in (query_analyzer._get_analyzer_llm, full_doc_detector._get_detector_llm,
                    reranker._get_reranker_llm):
        assert factory().model.endswith(config.LLM_MODEL_FAST)


def test_model_price_lookup():
    assert model_price("gemini-3.1-flash-lite", (9, 9)) == (0.25, 1.50)
    assert model_price("models/gemini-2.5-flash-lite-001", (9, 9)) == (0.10, 0.40)
    assert model_price("gemini-3.5-flash-lite", (9, 9)) == (0.30, 2.50)  # not the 3.5-flash price
    assert model_price("some-new-model", (1.0, 2.0)) == (1.0, 2.0)
    assert model_price(None, (1.0, 2.0)) == (1.0, 2.0)


def result(model: str, inp: int, out: int, total: int | None = None) -> LLMResult:
    message = AIMessage(
        content="x",
        usage_metadata={"input_tokens": inp, "output_tokens": out, "total_tokens": total or inp + out},
        response_metadata={"model_name": model},
    )
    return LLMResult(generations=[[ChatGeneration(message=message)]])


def test_collector_prices_each_call_by_its_own_model():
    usage = UsageCollector(fallback_prices=(10.0, 10.0))
    usage.on_llm_end(result("gemini-2.5-flash-lite", 1_000_000, 0))      # $0.10
    usage.on_llm_end(result("gemini-3.1-flash-lite", 0, 100, 1_000_100))  # thinking hidden in total
    assert usage.llm_calls == 2
    assert usage.output_tokens == 1_000_100
    assert usage.cost_usd == pytest.approx(0.10 + 1_000_100 * 1.50 / 1_000_000)
    assert usage.models == {"gemini-2.5-flash-lite", "gemini-3.1-flash-lite"}

    unknown = UsageCollector(fallback_prices=(2.0, 4.0))
    unknown.on_llm_end(result("mystery", 1_000_000, 1_000_000))
    assert unknown.cost_usd == pytest.approx(6.0)
