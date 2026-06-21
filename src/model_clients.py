"""
model_clients.py — unified interface for calling models via OpenRouter.

All models in models.yaml use provider: openrouter, which routes to:
    https://openrouter.ai/api/v1

This is the ONLY file that contains provider-specific logic.
The rest of the pipeline only calls call_model() and never touches SDKs directly.
"""

import os
import time
import logging
from typing import Optional

from openai import OpenAI, RateLimitError, APIStatusError, APIConnectionError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OpenRouter client (singleton — reuse across calls to avoid reconnect overhead)
# ---------------------------------------------------------------------------
_openrouter_client: Optional[OpenAI] = None


def _get_openrouter_client() -> OpenAI:
    """Return (or lazily create) the OpenRouter OpenAI-compatible client."""
    global _openrouter_client
    if _openrouter_client is None:
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "OPENROUTER_API_KEY is not set. "
                "Add it to your .env file and make sure it is loaded before running."
            )
        _openrouter_client = OpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
        )
    return _openrouter_client


# ---------------------------------------------------------------------------
# Core public function
# ---------------------------------------------------------------------------

def call_model(
    model_config: dict,
    prompt: str,
    conversation_history: Optional[list] = None,
    max_retries: int = 5,
    initial_backoff: float = 2.0,
) -> str:
    """
    Call an LLM and return its raw text response.

    Parameters
    ----------
    model_config : dict
        One entry from config/models.yaml, e.g.:
            {"id": "frontier-closed", "provider": "openrouter",
             "model_name": "anthropic/claude-opus-4", "tier": "frontier_closed"}

    prompt : str
        The user-turn prompt to send.  For the sycophancy battery's turn 2,
        pass the turn_2_pushback text here and supply conversation_history
        so the model sees the earlier exchange.

    conversation_history : list or None
        A list of prior message dicts: [{"role": "user", "content": "..."}, ...].
        If provided, these are prepended before the new prompt.
        Used by the runner for multi-turn sycophancy items.

    max_retries : int
        How many times to retry on rate-limit or transient errors.

    initial_backoff : float
        Seconds to wait before the first retry; doubles on each subsequent retry
        (exponential backoff with jitter).

    Returns
    -------
    str
        The model's text response, stripped of leading/trailing whitespace.

    Raises
    ------
    RuntimeError
        If all retries are exhausted without a successful response.
    """
    provider = model_config.get("provider", "").lower()

    if provider == "openrouter":
        return _call_openrouter(
            model_config=model_config,
            prompt=prompt,
            conversation_history=conversation_history,
            max_retries=max_retries,
            initial_backoff=initial_backoff,
        )
    else:
        raise ValueError(
            f"Unsupported provider '{provider}' for model '{model_config.get('id')}'. "
            "Only 'openrouter' is currently supported."
        )


# ---------------------------------------------------------------------------
# OpenRouter-specific implementation
# ---------------------------------------------------------------------------

def _call_openrouter(
    model_config: dict,
    prompt: str,
    conversation_history: Optional[list],
    max_retries: int,
    initial_backoff: float,
) -> str:
    """Route the call through OpenRouter using the openai SDK."""
    client = _get_openrouter_client()
    model_name = model_config["model_name"]

    # Build message list
    messages: list[dict] = []
    if conversation_history:
        messages.extend(conversation_history)
    messages.append({"role": "user", "content": prompt})

    backoff = initial_backoff
    last_error: Exception = RuntimeError("No attempts made")

    for attempt in range(1, max_retries + 1):
        try:
            logger.debug(
                "Calling model=%s attempt=%d messages=%d",
                model_name, attempt, len(messages),
            )
            response = client.chat.completions.create(
                model=model_name,
                messages=messages,
            )
            text = response.choices[0].message.content or ""
            return text.strip()

        except RateLimitError as exc:
            last_error = exc
            wait = backoff * (1 + 0.1 * (attempt - 1))  # slight jitter
            logger.warning(
                "Rate limit hit on model=%s (attempt %d/%d). Retrying in %.1fs…",
                model_name, attempt, max_retries, wait,
            )
            time.sleep(wait)
            backoff *= 2  # exponential backoff

        except APIConnectionError as exc:
            last_error = exc
            logger.warning(
                "Connection error on model=%s (attempt %d/%d): %s. Retrying in %.1fs…",
                model_name, attempt, max_retries, exc, backoff,
            )
            time.sleep(backoff)
            backoff *= 2

        except APIStatusError as exc:
            # 5xx server errors are transient; 4xx (except 429) are not — don't retry 4xx
            if exc.status_code >= 500:
                last_error = exc
                logger.warning(
                    "Server error %d on model=%s (attempt %d/%d). Retrying in %.1fs…",
                    exc.status_code, model_name, attempt, max_retries, backoff,
                )
                time.sleep(backoff)
                backoff *= 2
            else:
                raise  # 4xx client errors: surface immediately

    raise RuntimeError(
        f"All {max_retries} retries exhausted for model '{model_name}'. "
        f"Last error: {last_error}"
    )
