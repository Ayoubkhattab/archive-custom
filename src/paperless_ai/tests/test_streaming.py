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


def _collect_with_heartbeat(factory, **kwargs):
    async def run():
        return [
            chunk
            async for chunk in stream_from_sync(factory, heartbeat="~", **kwargs)
        ]

    return asyncio.run(run())


def test_heartbeat_is_sent_at_once_and_does_not_disturb_the_answer():
    # A proxy in front waits for the first byte; sending one straight away is
    # what keeps a slow model from looking like a dead connection.
    chunks = _collect_with_heartbeat(lambda: iter(["a", "b"]))
    assert chunks[0] == "~"
    assert [c for c in chunks if c != "~"] == ["a", "b"]


def test_heartbeat_repeats_while_the_generator_is_silent():
    def slow():
        time.sleep(0.5)
        yield "answer"

    chunks = _collect_with_heartbeat(slow, heartbeat_interval=0.1)

    assert chunks[-1] == "answer"
    # One at the start and several while waiting.
    assert chunks.count("~") >= 4


def test_an_item_arriving_at_the_deadline_is_not_lost():
    def bursts():
        for i in range(20):
            time.sleep(0.05)
            yield str(i)

    chunks = _collect_with_heartbeat(bursts, heartbeat_interval=0.05)

    assert [c for c in chunks if c != "~"] == [str(i) for i in range(20)]


def test_no_heartbeat_is_sent_by_default():
    assert _collect(lambda: iter(["a"])) == ["a"]


def test_an_error_still_reaches_the_consumer_with_heartbeat_on():
    def boom():
        time.sleep(0.2)
        raise RuntimeError("model unreachable")
        yield  # pragma: no cover

    with pytest.raises(RuntimeError, match="model unreachable"):
        _collect_with_heartbeat(boom, heartbeat_interval=0.05)


def test_generator_is_closed_when_the_consumer_stops_during_the_heartbeat():
    closed = []

    def silent():
        try:
            time.sleep(1)
            yield "late"
        finally:
            closed.append(True)

    async def run():
        async for _ in stream_from_sync(silent, heartbeat="~", heartbeat_interval=0.05):
            break  # the first heartbeat is enough
        await asyncio.sleep(1.3)

    asyncio.run(run())

    assert closed == [True]
