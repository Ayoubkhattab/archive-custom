"""
Turn a failed model call into something a person can act on.

A chat answer is streamed, and once streaming has started an exception can no
longer become an HTTP error response, so the reason has to be written into the
stream as text. Saying only "the model could not answer" hides whether Ollama is
down, the model was never pulled, the request timed out or the machine ran out of
memory, and each of those has a different fix.

Kept free of Django and of any LLM library so it can be tested on its own. The
Ollama client is matched by its behaviour (`status_code`, `error`) instead of by
importing its exception types.
"""

import httpx

GENERIC_ERROR = (
    "⚠️ تعذّر الحصول على رد من نموذج الذكاء الاصطناعي، يرجى المحاولة مجدداً.\n"
    "The AI model could not answer. Please try again."
)

_MEMORY_MARKERS = (
    "requires more system memory",
    "out of memory",
    "insufficient memory",
    "cudamalloc failed",
    "unable to allocate",
)

# What an interrupted stream looks like when Ollama is killed part-way through
# an answer, which the kernel does when the machine runs out of memory.
_INTERRUPTED_MARKERS = (
    "peer closed connection",
    "incomplete chunked read",
    "server disconnected",
    "connection reset",
    "remote end closed",
)


def _chain(exc: BaseException):
    """The exception and everything it was raised from, without looping."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def _text(exc: BaseException) -> str:
    return " ".join(
        str(getattr(e, "error", None) or e) for e in _chain(exc)
    ).lower()


def _status(exc: BaseException) -> int | None:
    for e in _chain(exc):
        status = getattr(e, "status_code", None)
        if isinstance(status, int):
            return status
    return None


def is_connection_error(exc: BaseException) -> bool:
    """The server could not be reached at all."""
    return any(
        isinstance(e, ConnectionError | httpx.ConnectError | httpx.ConnectTimeout)
        or "failed to connect to ollama" in str(e).lower()
        for e in _chain(exc)
        # A reset in the middle of an answer is a different failure.
        if not isinstance(e, ConnectionResetError | ConnectionAbortedError)
    )


def is_timeout_error(exc: BaseException) -> bool:
    return any(
        isinstance(e, TimeoutError | httpx.TimeoutException) for e in _chain(exc)
    ) and not is_connection_error(exc)


def is_transient_connection_error(exc: BaseException) -> bool:
    """
    Worth retrying straight away: nothing was received and the server may simply
    be restarting. A timeout is not, because it has already cost the full wait.
    """
    return is_connection_error(exc)


def describe_llm_error(
    exc: BaseException,
    *,
    endpoint: str,
    model: str,
    timeout: float | int | None = None,
) -> str:
    """A message, Arabic first and English second, for the person using the chat."""
    text = _text(exc)
    status = _status(exc)

    if is_connection_error(exc):
        return (
            f"⚠️ تعذّر الاتصال بخادم النموذج ({endpoint}). "
            "تأكد أن Ollama يعمل ثم أعد المحاولة.\n"
            f"Could not reach the model server at {endpoint}. "
            "Check that Ollama is running."
        )

    if any(marker in text for marker in _MEMORY_MARKERS):
        return (
            f"⚠️ لا تكفي الذاكرة لتشغيل النموذج «{model}». "
            "أغلق ما لا تحتاجه أو استخدم نموذجاً أصغر.\n"
            f"Not enough memory to run the model {model}."
        )

    if status == 404 or ("model" in text and "not found" in text):
        return (
            f"⚠️ النموذج «{model}» غير مثبّت على خادم Ollama. "
            f"ثبّته بالأمر: ollama pull {model}\n"
            f"The model {model} is not installed. Run: ollama pull {model}"
        )

    if "does not support thinking" in text or (
        status == 400 and "think" in text
    ):
        return (
            f"⚠️ النموذج «{model}» لا يدعم وضع التفكير العميق.\n"
            f"The model {model} does not support thinking."
        )

    if is_timeout_error(exc):
        waited = f" ({int(timeout)} ثانية)" if timeout else ""
        return (
            f"⚠️ استغرق النموذج وقتاً أطول من المسموح{waited} دون أن يرد. "
            "جرّب سؤالاً أقصر أو نمط الإجابة السريعة.\n"
            "The model took too long to answer. Try a shorter question."
        )

    if any(marker in text for marker in _INTERRUPTED_MARKERS):
        return (
            "⚠️ انقطع الاتصال بالنموذج أثناء الإجابة. غالباً نفدت الذاكرة "
            "أو أُعيد تشغيل Ollama، فحاول مجدداً.\n"
            "The connection to the model was lost while answering, most often "
            "because it ran out of memory or Ollama restarted."
        )

    return GENERIC_ERROR
