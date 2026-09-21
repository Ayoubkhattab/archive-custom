import httpx
import pytest

from paperless_ai.llm_errors import GENERIC_ERROR
from paperless_ai.llm_errors import EmptyAnswerError
from paperless_ai.llm_errors import describe_llm_error
from paperless_ai.llm_errors import is_connection_error
from paperless_ai.llm_errors import is_timeout_error
from paperless_ai.llm_errors import is_transient_connection_error

ENDPOINT = "http://host.docker.internal:11434"
MODEL = "qwen2:0.5b"


class FakeResponseError(Exception):
    """Behaves like ollama.ResponseError, which carries `error` and `status_code`."""

    def __init__(self, error: str, status_code: int = -1):
        super().__init__(error)
        self.error = error
        self.status_code = status_code


def describe(exc, timeout=300):
    return describe_llm_error(exc, endpoint=ENDPOINT, model=MODEL, timeout=timeout)


class TestClassification:
    def test_refused_connection_is_a_connection_error(self):
        # The ollama client raises the builtin ConnectionError with this text.
        exc = ConnectionError(
            "Failed to connect to Ollama. Please check that Ollama is "
            "downloaded, running and accessible.",
        )
        assert is_connection_error(exc)
        assert is_transient_connection_error(exc)

    def test_httpx_connect_error_is_a_connection_error(self):
        assert is_connection_error(httpx.ConnectError("refused"))

    def test_connection_error_found_through_the_cause_chain(self):
        try:
            try:
                raise httpx.ConnectError("refused")
            except httpx.ConnectError as inner:
                raise RuntimeError("llm failed") from inner
        except RuntimeError as outer:
            assert is_connection_error(outer)

    def test_timeout_is_not_a_connection_error_and_is_not_retried(self):
        exc = httpx.ReadTimeout("slow")
        assert is_timeout_error(exc)
        assert not is_connection_error(exc)
        assert not is_transient_connection_error(exc)

    def test_reset_mid_answer_is_not_treated_as_unreachable(self):
        # Nothing to retry: part of the answer has already been sent.
        assert not is_connection_error(ConnectionResetError("reset"))

    def test_unrelated_error_is_neither(self):
        exc = ValueError("bad")
        assert not is_connection_error(exc)
        assert not is_timeout_error(exc)

    def test_a_cyclic_cause_chain_terminates(self):
        a, b = RuntimeError("a"), RuntimeError("b")
        a.__cause__, b.__cause__ = b, a
        assert not is_connection_error(a)


class TestDescribe:
    def test_unreachable_server_names_the_endpoint(self):
        message = describe(ConnectionError("Failed to connect to Ollama."))
        assert ENDPOINT in message
        assert "Ollama" in message

    def test_missing_model_gives_the_pull_command(self):
        message = describe(FakeResponseError(f"model '{MODEL}' not found", 404))
        assert f"ollama pull {MODEL}" in message

    def test_not_found_is_recognised_from_the_text_alone(self):
        message = describe(FakeResponseError(f"model '{MODEL}' not found"))
        assert f"ollama pull {MODEL}" in message

    def test_unsupported_thinking_is_named(self):
        message = describe(
            FakeResponseError(f"{MODEL} does not support thinking", 400),
        )
        assert "التفكير" in message
        assert MODEL in message

    @pytest.mark.parametrize(
        "detail",
        [
            "model requires more system memory (5.6 GiB) than is available",
            "CUDA out of memory",
            "unable to allocate CPU buffer",
        ],
    )
    def test_memory_shortage_is_named(self, detail):
        message = describe(FakeResponseError(detail, 500))
        assert "الذاكرة" in message
        assert MODEL in message

    def test_timeout_mentions_how_long_it_waited(self):
        message = describe(httpx.ReadTimeout("slow"), timeout=300)
        assert "300" in message

    def test_timeout_without_a_configured_limit_still_reads_well(self):
        message = describe(httpx.ReadTimeout("slow"), timeout=None)
        assert "()" not in message
        assert "وقتاً" in message

    @pytest.mark.parametrize(
        "detail",
        ["peer closed connection without sending complete message body",
         "incomplete chunked read"],
    )
    def test_interrupted_stream_points_at_memory_or_a_restart(self, detail):
        message = describe(httpx.RemoteProtocolError(detail))
        assert "انقطع" in message

    def test_an_empty_answer_points_at_the_output_limit_and_the_fast_mode(self):
        message = describe(EmptyAnswerError())
        assert "أي إجابة" in message
        assert "الإجابة السريعة" in message

    def test_an_empty_answer_is_not_mistaken_for_a_connection_problem(self):
        exc = EmptyAnswerError()
        assert not is_connection_error(exc)
        assert not is_transient_connection_error(exc)

    def test_unknown_error_falls_back_to_the_generic_message(self):
        assert describe(ValueError("something else")) == GENERIC_ERROR

    def test_every_message_is_bilingual_and_marked_as_a_warning(self):
        for exc in (
            ConnectionError("Failed to connect to Ollama."),
            FakeResponseError("model 'x' not found", 404),
            httpx.ReadTimeout("slow"),
            EmptyAnswerError(),
            ValueError("x"),
        ):
            message = describe(exc)
            assert message.startswith("⚠️")
            assert "\n" in message
