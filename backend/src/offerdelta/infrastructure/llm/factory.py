"""Building a provider from the environment.

Separate from `anthropic.py` so the client itself stays independent of how
configuration reaches it: the adapter takes a config object, and only this file
knows that one of the ways to get it is an environment variable.

`build_provider` returns `None` rather than raising when no key is set. Every
caller so far - the evaluation runner, the report - has a useful thing to do
without a model, which is to run the rule baseline and say plainly that the LLM
row is missing. Raising here would turn "no key today" into a crash in a script
that had other work to do.
"""

from __future__ import annotations

from offerdelta.config import Settings, get_settings
from offerdelta.infrastructure.llm.anthropic import (
    DEFAULT_MODEL,
    AnthropicConfig,
    AnthropicProvider,
)


def build_provider(settings: Settings | None = None) -> AnthropicProvider | None:
    """A live provider, or `None` when no key is configured."""
    settings = settings or get_settings()

    if not settings.anthropic_api_key:
        return None

    config = AnthropicConfig(
        api_key=settings.anthropic_api_key,
        model=settings.anthropic_model or DEFAULT_MODEL,
    )
    return AnthropicProvider(config=config)
