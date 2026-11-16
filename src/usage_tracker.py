"""Count the tokens and estimated cost of one chat request, across every LLM call in the pipeline."""
from __future__ import annotations

import threading

from langchain_core.callbacks import BaseCallbackHandler

from config import MODEL_PRICES


def model_price(model: str | None, fallback: tuple[float, float]) -> tuple[float, float]:
    """(input, output) USD per 1M tokens. Matches the longest known prefix, so a
    versioned name like "gemini-3.1-flash-lite-001" still finds its price."""
    name = (model or "").removeprefix("models/")
    best = max((m for m in MODEL_PRICES if name.startswith(m)), key=len, default=None)
    return MODEL_PRICES[best] if best else fallback


class UsageCollector(BaseCallbackHandler):
    """LangChain callback: sums tokens and cost from each finished LLM call.

    Different calls in one request may run on different models (a cheap model
    for routing, another for the answer), so cost is priced per call.
    """

    def __init__(self, fallback_prices: tuple[float, float] = (0.0, 0.0)) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.llm_calls = 0
        self.cost_usd = 0.0
        self.models: set[str] = set()
        self._fallback = fallback_prices
        self._lock = threading.Lock()

    def on_llm_end(self, response, **kwargs) -> None:
        for generations in getattr(response, "generations", None) or []:
            for gen in generations:
                message = getattr(gen, "message", None)
                usage = getattr(message, "usage_metadata", None)
                if not usage:
                    continue
                inp = int(usage.get("input_tokens") or 0)
                out = int(usage.get("output_tokens") or 0)
                total = int(usage.get("total_tokens") or 0)
                # Thinking tokens are billed as output; some client versions leave
                # them out of output_tokens but include them in the total.
                out = max(out, total - inp)
                model = (getattr(message, "response_metadata", None) or {}).get("model_name")
                price_in, price_out = model_price(model, self._fallback)
                with self._lock:
                    self.input_tokens += inp
                    self.output_tokens += out
                    self.llm_calls += 1
                    self.cost_usd += (inp * price_in + out * price_out) / 1_000_000
                    if model:
                        self.models.add(model.removeprefix("models/"))
