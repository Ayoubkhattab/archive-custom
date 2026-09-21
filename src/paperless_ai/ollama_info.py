"""
What a locally served Ollama model can do.

Sending `think: true` to a model without the thinking capability is rejected by
Ollama ("does not support thinking"), so a request that asks for it must first
know the answer. Ollama reports it under `capabilities` in `/api/show`.
"""

import logging
import time

import httpx

logger = logging.getLogger("paperless_ai.ollama_info")

# A positive or negative answer is trusted this long. Failures to ask are not
# cached, so a server that was briefly down is asked again on the next request.
CACHE_SECONDS = 300

_cache: dict[tuple[str, str], tuple[float, bool]] = {}


def clear_cache() -> None:
    _cache.clear()


def model_supports_thinking(
    endpoint: str,
    model: str,
    *,
    timeout: float = 5.0,
    client: httpx.Client | None = None,
) -> bool:
    """
    Whether the model can reason before answering.

    Answers False whenever it cannot be established: an unreachable server, an
    unknown model, or an Ollama too old to report capabilities. Skipping the
    thinking step costs some depth; requesting it wrongly costs the whole answer.
    """
    key = (endpoint.rstrip("/"), model)
    cached = _cache.get(key)
    if cached and cached[0] > time.monotonic():
        return cached[1]

    owns_client = client is None
    client = client or httpx.Client(timeout=timeout)
    try:
        response = client.post(f"{key[0]}/api/show", json={"model": model})
        response.raise_for_status()
        capabilities = response.json().get("capabilities")
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(
            "Could not read the capabilities of %s from %s: %s",
            model,
            endpoint,
            exc,
        )
        return False
    finally:
        if owns_client:
            client.close()

    supported = isinstance(capabilities, list) and "thinking" in capabilities
    _cache[key] = (time.monotonic() + CACHE_SECONDS, supported)
    return supported
