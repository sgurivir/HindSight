"""Prompt assembly facade.

The actual prompt templates live in `hindsight/core/prompts/*.md` and are
assembled by `hindsight.core.prompts.prompt_builder.PromptBuilder`. That
package is out of scope for this rewrite — we just re-export it here so
orchestration code imports prompts from one place (`hindsight.llm.prompts`)
instead of reaching into `hindsight.core.prompts`.

The intent of having a dedicated module is twofold:

1. A single import site that orchestration depends on, so a future cache
   warm-up (reading every `.md` at session start instead of per-call) can be
   added in one place.
2. Lets us replace the underlying `PromptBuilder` later without touching
   every orchestration call site.
"""

from __future__ import annotations

from hindsight.core.prompts.prompt_builder import PromptBuilder

__all__ = ["PromptBuilder"]
