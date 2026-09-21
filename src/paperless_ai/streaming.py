import asyncio
import threading
from collections.abc import AsyncIterator
from collections.abc import Callable
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.db import connections

_DONE = object()
_executor: ThreadPoolExecutor | None = None

# A reverse proxy gives up on a response that stays silent for too long:
# Cloudflare answers 524 after 120 seconds without a byte, and a model that
# reasons before it writes can be quiet for longer than that. A zero-width space
# keeps the connection alive and is invisible if it ever reaches the screen.
STREAM_HEARTBEAT = "\u200b"
HEARTBEAT_INTERVAL_SECONDS = 10.0


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(
            max_workers=settings.LLM_STREAM_THREADS,
            thread_name_prefix="ai-stream",
        )
    return _executor


async def stream_from_sync(
    factory: Callable[[], Iterator[str]],
    *,
    heartbeat: str | None = None,
    heartbeat_interval: float = HEARTBEAT_INTERVAL_SECONDS,
) -> AsyncIterator[str]:
    """
    Expose a blocking generator as an async iterator, so chunks reach the
    client as they are produced. Django's ASGI handler otherwise drains a
    synchronous streaming body into a list before sending the first byte.

    The generator runs start to finish in one dedicated thread, which keeps
    its database connection usable and lets it be closed cleanly.

    With `heartbeat`, that text is sent at once and again whenever
    `heartbeat_interval` seconds pass with nothing else to send, so a slow first
    token does not look like a dead connection to a proxy in front.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    stop = threading.Event()

    def put(item) -> None:
        try:
            loop.call_soon_threadsafe(queue.put_nowait, item)
        except RuntimeError:  # event loop already closed (client went away)
            stop.set()

    def produce() -> None:
        generator = None
        try:
            generator = factory()
            for chunk in generator:
                if stop.is_set():
                    break
                put(chunk)
        except BaseException as exc:
            put(exc)
        finally:
            close = getattr(generator, "close", None)
            if close is not None:
                close()
            connections.close_all()
            put(_DONE)

    loop.run_in_executor(_get_executor(), produce)
    getter: asyncio.Future | None = None
    try:
        if heartbeat is not None:
            yield heartbeat
        while True:
            if getter is None:
                getter = asyncio.ensure_future(queue.get())
            if heartbeat is None:
                item = await getter
            else:
                # Waiting on the same pending get, never cancelling it, so an
                # item that arrives at the deadline cannot be lost.
                done, _ = await asyncio.wait({getter}, timeout=heartbeat_interval)
                if not done:
                    yield heartbeat
                    continue
                item = getter.result()
            getter = None
            if item is _DONE:
                break
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        stop.set()
        if getter is not None:
            getter.cancel()
