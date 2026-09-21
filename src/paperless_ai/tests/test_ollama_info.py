import httpx
import pytest

from paperless_ai import ollama_info
from paperless_ai.ollama_info import model_supports_thinking

ENDPOINT = "http://ollama:11434"


@pytest.fixture(autouse=True)
def _fresh_cache():
    ollama_info.clear_cache()
    yield
    ollama_info.clear_cache()


def client_answering(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_reports_thinking_when_the_model_lists_it():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content
        return httpx.Response(200, json={"capabilities": ["completion", "thinking"]})

    assert model_supports_thinking(
        ENDPOINT,
        "qwen3:4b",
        client=client_answering(handler),
    )
    assert seen["url"] == f"{ENDPOINT}/api/show"
    assert b"qwen3:4b" in seen["body"]


def test_reports_no_thinking_for_a_plain_model():
    def handler(request):
        return httpx.Response(200, json={"capabilities": ["completion"]})

    assert not model_supports_thinking(
        ENDPOINT,
        "qwen2:0.5b",
        client=client_answering(handler),
    )


def test_an_ollama_that_reports_no_capabilities_counts_as_unsupported():
    # Older releases do not return the field; asking for thinking blind would
    # fail the whole answer.
    def handler(request):
        return httpx.Response(200, json={"details": {}})

    assert not model_supports_thinking(
        ENDPOINT,
        "qwen2:0.5b",
        client=client_answering(handler),
    )


@pytest.mark.parametrize("status", [404, 500])
def test_an_error_answer_counts_as_unsupported(status):
    def handler(request):
        return httpx.Response(status, json={"error": "nope"})

    assert not model_supports_thinking(
        ENDPOINT,
        "missing",
        client=client_answering(handler),
    )


def test_an_unreachable_server_counts_as_unsupported():
    def handler(request):
        raise httpx.ConnectError("refused")

    assert not model_supports_thinking(
        ENDPOINT,
        "qwen3:4b",
        client=client_answering(handler),
    )


def test_a_garbled_answer_counts_as_unsupported():
    def handler(request):
        return httpx.Response(200, content=b"not json")

    assert not model_supports_thinking(
        ENDPOINT,
        "qwen3:4b",
        client=client_answering(handler),
    )


def test_a_successful_answer_is_cached():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json={"capabilities": ["thinking"]})

    client = client_answering(handler)
    assert model_supports_thinking(ENDPOINT, "m", client=client)
    assert model_supports_thinking(ENDPOINT, "m", client=client)
    assert len(calls) == 1


def test_a_failure_is_not_cached_so_a_recovered_server_is_asked_again():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json={"capabilities": ["thinking"]})

    client = client_answering(handler)
    assert not model_supports_thinking(ENDPOINT, "m", client=client)
    assert model_supports_thinking(ENDPOINT, "m", client=client)
    assert len(calls) == 2


def test_the_endpoint_trailing_slash_does_not_matter():
    urls = []

    def handler(request):
        urls.append(str(request.url))
        return httpx.Response(200, json={"capabilities": []})

    model_supports_thinking(f"{ENDPOINT}/", "m", client=client_answering(handler))
    assert urls == [f"{ENDPOINT}/api/show"]
