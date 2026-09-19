import asyncio
import time

import pytest

from paperless_ai.streaming import stream_from_sync


def _collect(factory):
    async def run():
        return [chunk async for chunk in stream_from_sync(factory)]

    return asyncio.run(run())


def test_yields_all_chunks_in_order():
    assert _collect(lambda: iter(["a", "b", "c"])) == ["a", "b", "c"]


def test_chunks_arrive_before_the_generator_finishes():
    def slow():
        yield "first"
        time.sleep(0.5)
        yield "second"

    async def run():
        start = time.monotonic()
        arrivals = []
        async for chunk in stream_from_sync(slow):
            arrivals.append((chunk, time.monotonic() - start))
        return arrivals

    (first, t_first), (second, t_second) = asyncio.run(run())

    assert (first, second) == ("first", "second")
    assert t_first < 0.3
    assert t_second >= 0.5


def test_generator_error_reaches_the_consumer():
    def boom():
        yield "ok"
        raise RuntimeError("model unreachable")

    async def run():
        seen = []
        async for chunk in stream_from_sync(boom):
            seen.append(chunk)
        return seen

    with pytest.raises(RuntimeError, match="model unreachable"):
        asyncio.run(run())


def test_generator_is_closed_when_consumer_stops_early():
    closed = []

    def endless():
        try:
            while True:
                yield "x"
                time.sleep(0.01)
        finally:
            closed.append(True)

    async def run():
        async for _ in stream_from_sync(endless):
            break
        await asyncio.sleep(0.3)

    asyncio.run(run())

    assert closed == [True]
