"""Count the tokens one chat request spends, across every LLM call in the pipeline."""
from __future__ import annotations

import threading

from langchain_core.callbacks import BaseCallbackHandler


class UsageCollector(BaseCallbackHandler):
    """LangChain callback: sums usage_metadata from each finished LLM call."""

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.llm_calls = 0
        self._lock = threading.Lock()

    def on_llm_end(self, response, **kwargs) -> None:
        for generations in getattr(response, "generations", None) or []:
            for gen in generations:
                usage = getattr(getattr(gen, "message", None), "usage_metadata", None)
                if not usage:
                    continue
                inp = int(usage.get("input_tokens") or 0)
                out = int(usage.get("output_tokens") or 0)
                total = int(usage.get("total_tokens") or 0)
                # Thinking tokens are billed as output; some client versions leave
                # them out of output_tokens but include them in the total.
                out = max(out, total - inp)
                with self._lock:
                    self.input_tokens += inp
                    self.output_tokens += out
                    self.llm_calls += 1
