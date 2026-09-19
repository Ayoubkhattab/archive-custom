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
) -> AsyncIterator[str]:
    """
    Expose a blocking generator as an async iterator, so chunks reach the
    client as they are produced. Django's ASGI handler otherwise drains a
    synchronous streaming body into a list before sending the first byte.

    The generator runs start to finish in one dedicated thread, which keeps
    its database connection usable and lets it be closed cleanly.
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
    try:
        while True:
            item = await queue.get()
            if item is _DONE:
                break
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        stop.set()
