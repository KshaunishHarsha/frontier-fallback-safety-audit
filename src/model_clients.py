"""
model_clients.py — unified interface for calling models via Groq.

All models in models.yaml use provider: groq, which routes to:
    https://api.groq.com/openai/v1

This is the ONLY file that contains provider-specific logic.
The rest of the pipeline only calls call_model() and never touches SDKs directly.
"""

import os
import time
import logging
from typing import Optional

from openai import OpenAI, RateLimitError, APIStatusError, APIConnectionError

logger = logging.getLogger(__name__)

_groq_client: Optional[OpenAI] = None


def _get_groq_client() -> OpenAI:
    global _groq_client
    if _groq_client is None:
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "GROQ_API_KEY is not set. "
                "Add it to your .env file and make sure it is loaded before running."
            )
        _groq_client = OpenAI(
            api_key=api_key,
            base_url="https://api.groq.com/openai/v1",
        )
    return _groq_client


def call_model(
    model_config: dict,
    prompt: str,
    conversation_history: Optional[list] = None,
    max_retries: int = 5,
    initial_backoff: float = 2.0,
) -> str:
    provider = model_config.get("provider", "").lower()
    if provider == "groq":
        return _call_groq(
            model_config=model_config,
            prompt=prompt,
            conversation_history=conversation_history,
            max_retries=max_retries,
            initial_backoff=initial_backoff,
        )
    else:
        raise ValueError(
            f"Unsupported provider '{provider}' for model '{model_config.get('id')}'. "
            "Only 'groq' is currently supported."
        )


def _call_groq(
    model_config: dict,
    prompt: str,
    conversation_history: Optional[list],
    max_retries: int,
    initial_backoff: float,
) -> str:
    client = _get_groq_client()
    model_name = model_config["model_name"]

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
                max_tokens=1024,
            )
            text = response.choices[0].message.content or ""
            return text.strip()

        except RateLimitError as exc:
            last_error = exc
            wait = max(30.0, backoff) * (1 + 0.1 * (attempt - 1))
            logger.warning(
                "Rate limit hit on model=%s (attempt %d/%d). Retrying in %.1fs…",
                model_name, attempt, max_retries, wait,
            )
            time.sleep(wait)
            backoff *= 2

        except APIConnectionError as exc:
            last_error = exc
            logger.warning(
                "Connection error on model=%s (attempt %d/%d): %s. Retrying in %.1fs…",
                model_name, attempt, max_retries, exc, backoff,
            )
            time.sleep(backoff)
            backoff *= 2

        except APIStatusError as exc:
            if exc.status_code >= 500:
                last_error = exc
                logger.warning(
                    "Server error %d on model=%s (attempt %d/%d). Retrying in %.1fs…",
                    exc.status_code, model_name, attempt, max_retries, backoff,
                )
                time.sleep(backoff)
                backoff *= 2
            else:
                raise

    raise RuntimeError(
        f"All {max_retries} retries exhausted for model '{model_name}'. "
        f"Last error: {last_error}"
    )
