#!/usr/bin/env python3
"""
One-shot script: upload the application's system prompts to Langfuse
Prompt Management with label ``production``.

Run this whenever a hard-coded prompt in the source code changes and you
want to seed Langfuse with the new content.  Subsequent edits should be
made in the Langfuse UI; this script is just a bootstrap and a safety net
for restoring lost prompts.

Usage:
    python scripts/sync_prompts_to_langfuse.py            # upload all
    python scripts/sync_prompts_to_langfuse.py analyzer   # subset by name
    python scripts/sync_prompts_to_langfuse.py --dry-run  # print, don't write

Requirements: LANGFUSE_SECRET_KEY, LANGFUSE_PUBLIC_KEY, LANGFUSE_HOST set
in the environment (typically via the project's ``.env`` file).
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# Allow running the script from the repo root without PYTHONPATH gymnastics.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

# Load .env early so config.py sees the keys.
try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass  # dotenv is optional; env vars may already be set.


def _langchain_to_mustache(prompt: str) -> str:
    """
    Convert a LangChain-template string (``{var}`` variables, ``{{`` and
    ``}}`` for literal braces) to Langfuse's Mustache syntax (``{{var}}``
    variables, single braces for literals).

    The transformation walks the input once and tracks state to avoid the
    classic "double-replace" pitfalls.  Examples:

        "Answer: {context}"            -> "Answer: {{context}}"
        '{"key": "value"}'              -> '{"key": "value"}'         (unchanged)
        '{{"key": "value"}}'            -> '{"key": "value"}'         (literal)
        "Use {var} in {{json: true}}"   -> "Use {{var}} in {json: true}"
    """
    out: list[str] = []
    i = 0
    n = len(prompt)
    while i < n:
        ch = prompt[i]
        nxt = prompt[i + 1] if i + 1 < n else ""
        if ch == "{" and nxt == "{":
            # LangChain escape for literal "{"
            out.append("{")
            i += 2
        elif ch == "}" and nxt == "}":
            # LangChain escape for literal "}"
            out.append("}")
            i += 2
        elif ch == "{":
            # Variable reference {name} — find the matching "}".
            j = prompt.find("}", i + 1)
            if j == -1:
                # Unbalanced — emit as-is rather than corrupting the prompt.
                out.append(ch)
                i += 1
            else:
                var = prompt[i + 1 : j].strip()
                # Heuristic: a real variable name is identifier-like.
                # JSON-shaped content is detected by the presence of quotes
                # or whitespace inside and is kept as literal text.
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", var):
                    out.append("{{" + var + "}}")
                    i = j + 1
                else:
                    # Treat as literal text — emit the opening brace, let
                    # the loop process the rest character-by-character.
                    out.append(ch)
                    i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


@dataclass(frozen=True)
class PromptSpec:
    """Bind a Langfuse prompt name to the literal source-code string."""

    name: str
    source: str  # The raw Python string from the codebase (LangChain syntax).
    tags: tuple[str, ...] = ()


def _collect_prompts() -> list[PromptSpec]:
    """
    Pull the live prompt strings from the application modules.  Importing
    here (rather than at module top) keeps the script safe to call even
    when the rest of the application isn't fully installable.
    """
    from src.query_analyzer import ANALYZER_SYSTEM_PROMPT
    from src.graph import (
        ANSWER_SYSTEM_PROMPT,
        SYNTHESIS_SYSTEM_PROMPT,
        CHITCHAT_SYSTEM_PROMPT,
        FULL_DOC_SYSTEM_PROMPT as FULL_DOC_GENERATOR_PROMPT,
    )
    from src.full_doc_detector import (
        FULL_DOC_SYSTEM_PROMPT as FULL_DOC_DETECTOR_PROMPT,
    )
    from src.reranker import RERANK_PROMPT

    return [
        PromptSpec(
            name="analyzer-system",
            source=ANALYZER_SYSTEM_PROMPT,
            tags=("rag", "routing", "rewriting"),
        ),
        PromptSpec(
            name="answer-system",
            source=ANSWER_SYSTEM_PROMPT,
            tags=("rag", "generator"),
        ),
        PromptSpec(
            name="synthesis-system",
            source=SYNTHESIS_SYSTEM_PROMPT,
            tags=("rag", "generator", "synthesis"),
        ),
        PromptSpec(
            name="chitchat-system",
            source=CHITCHAT_SYSTEM_PROMPT,
            tags=("rag", "chitchat"),
        ),
        PromptSpec(
            name="reranker-system",
            source=RERANK_PROMPT,
            tags=("rag", "reranker"),
        ),
        PromptSpec(
            name="full-doc-detector-system",
            source=FULL_DOC_DETECTOR_PROMPT,
            tags=("rag", "full-doc", "router"),
        ),
        PromptSpec(
            name="full-doc-generator-system",
            source=FULL_DOC_GENERATOR_PROMPT,
            tags=("rag", "full-doc", "generator"),
        ),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "names",
        nargs="*",
        help="Optional subset of prompt names (matches by substring).",
    )
    parser.add_argument(
        "--label",
        default="production",
        help="Langfuse label to attach to the new version (default: production).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be uploaded without contacting Langfuse.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if not (os.getenv("LANGFUSE_SECRET_KEY") and os.getenv("LANGFUSE_PUBLIC_KEY")):
        print(
            "❌ LANGFUSE_SECRET_KEY / LANGFUSE_PUBLIC_KEY not found in environment.\n"
            "   Put them in .env or export them and try again.",
            file=sys.stderr,
        )
        return 2

    all_prompts = _collect_prompts()
    if args.names:
        wanted = {n.lower() for n in args.names}
        prompts = [
            p for p in all_prompts if any(w in p.name.lower() for w in wanted)
        ]
        if not prompts:
            print(f"❌ No prompts matched: {sorted(wanted)}", file=sys.stderr)
            return 1
    else:
        prompts = all_prompts

    if args.dry_run:
        for p in prompts:
            converted = _langchain_to_mustache(p.source)
            print(f"=== {p.name} ({len(converted)} chars, label={args.label!r}) ===")
            print(converted)
            print()
        return 0

    # Real upload path.
    from langfuse import get_client

    client = get_client()
    if not client.auth_check():
        print("❌ Langfuse auth_check failed; refusing to upload.", file=sys.stderr)
        return 3

    for p in prompts:
        converted = _langchain_to_mustache(p.source)
        client.create_prompt(
            name=p.name,
            prompt=converted,
            type="text",
            labels=[args.label],
            tags=list(p.tags) if p.tags else None,
        )
        print(f"✅ Uploaded {p.name}  ({len(converted)} chars, label={args.label!r})")

    client.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
