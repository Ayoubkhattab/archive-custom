from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from llama_index.core.llms import ChatMessage
from llama_index.core.llms.llm import ToolSelection

from paperless_ai.client import AIClient
from paperless_ai.client import _parse_keep_alive
from paperless_ai.llm_errors import EmptyAnswerError


@pytest.fixture
def mock_ai_config():
    with patch("paperless_ai.client.AIConfig") as MockAIConfig:
        mock_config = MagicMock()
        MockAIConfig.return_value = mock_config
        yield mock_config


@pytest.fixture
def mock_ollama_llm():
    with patch("llama_index.llms.ollama.Ollama") as MockOllama:
        yield MockOllama


@pytest.fixture
def mock_openai_llm():
    with patch("llama_index.llms.openai.OpenAI") as MockOpenAI:
        yield MockOpenAI


def test_get_llm_ollama(mock_ai_config, mock_ollama_llm, settings):
    mock_ai_config.llm_backend = "ollama"
    mock_ai_config.llm_model = "test_model"
    mock_ai_config.llm_endpoint = "http://test-url"
    settings.LLM_REQUEST_TIMEOUT = 300.0
    settings.LLM_CONTEXT_WINDOW = 8192
    settings.LLM_MAX_OUTPUT_TOKENS = 1024
    settings.LLM_KEEP_ALIVE = "-1"
    settings.LLM_THINKING = False

    client = AIClient()

    mock_ollama_llm.assert_called_once_with(
        model="test_model",
        base_url="http://test-url",
        request_timeout=300.0,
        context_window=8192,
        keep_alive=-1.0,
        thinking=False,
        additional_kwargs={"num_predict": 1024},
    )
    assert client.llm == mock_ollama_llm.return_value


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("-1", -1.0), ("300", 300.0), ("30m", "30m"), ("24h", "24h")],
)
def test_parse_keep_alive(raw, expected):
    assert _parse_keep_alive(raw) == expected


def test_get_llm_openai(mock_ai_config, mock_openai_llm):
    mock_ai_config.llm_backend = "openai"
    mock_ai_config.llm_model = "test_model"
    mock_ai_config.llm_api_key = "test_api_key"
    mock_ai_config.llm_endpoint = "http://test-url"

    client = AIClient()

    mock_openai_llm.assert_called_once_with(
        model="test_model",
        api_base="http://test-url",
        api_key="test_api_key",
    )
    assert client.llm == mock_openai_llm.return_value


def test_get_llm_unsupported_backend(mock_ai_config):
    mock_ai_config.llm_backend = "unsupported"

    with pytest.raises(ValueError, match="Unsupported LLM backend: unsupported"):
        AIClient()


def test_run_llm_query(mock_ai_config, mock_ollama_llm):
    mock_ai_config.llm_backend = "ollama"
    mock_ai_config.llm_model = "test_model"
    mock_ai_config.llm_endpoint = "http://test-url"

    mock_llm_instance = mock_ollama_llm.return_value

    tool_selection = ToolSelection(
        tool_id="call_test",
        tool_name="DocumentClassifierSchema",
        tool_kwargs={
            "title": "Test Title",
            "tags": ["test", "document"],
            "correspondents": ["John Doe"],
            "document_types": ["report"],
            "storage_paths": ["Reports"],
            "dates": ["2023-01-01"],
        },
    )

    mock_llm_instance.chat_with_tools.return_value = MagicMock()
    mock_llm_instance.get_tool_calls_from_response.return_value = [tool_selection]

    client = AIClient()
    result = client.run_llm_query("test_prompt")

    assert result["title"] == "Test Title"


def test_run_chat(mock_ai_config, mock_ollama_llm):
    mock_ai_config.llm_backend = "ollama"
    mock_ai_config.llm_model = "test_model"
    mock_ai_config.llm_endpoint = "http://test-url"

    mock_llm_instance = mock_ollama_llm.return_value
    mock_llm_instance.chat.return_value = "test_chat_result"

    client = AIClient()
    messages = [ChatMessage(role="user", content="Hello")]
    result = client.run_chat(messages)

    mock_llm_instance.chat.assert_called_once_with(messages)
    assert result == "test_chat_result"


def test_stream_chat_yields_only_non_empty_deltas(mock_ai_config, mock_ollama_llm):
    mock_ai_config.llm_backend = "ollama"
    mock_ai_config.llm_model = "test_model"
    mock_ai_config.llm_endpoint = "http://test-url"

    mock_llm_instance = mock_ollama_llm.return_value
    mock_llm_instance.stream_chat.return_value = iter(
        [MagicMock(delta="Hel"), MagicMock(delta=""), MagicMock(delta="lo")],
    )

    client = AIClient()
    messages = [ChatMessage(role="user", content="Hello")]

    assert list(client.stream_chat(messages)) == ["Hel", "lo"]
    mock_llm_instance.stream_chat.assert_called_once_with(messages)


def test_get_llm_ollama_passes_cpu_thread_count(mock_ai_config, mock_ollama_llm, settings):
    mock_ai_config.llm_backend = "ollama"
    mock_ai_config.llm_model = "test_model"
    mock_ai_config.llm_endpoint = "http://test-url"
    settings.LLM_MAX_OUTPUT_TOKENS = 512
    settings.LLM_NUM_THREAD = 8

    AIClient()

    kwargs = mock_ollama_llm.call_args.kwargs
    assert kwargs["additional_kwargs"] == {"num_predict": 512, "num_thread": 8}


def _configure_ollama(mock_ai_config):
    mock_ai_config.llm_backend = "ollama"
    mock_ai_config.llm_model = "test_model"
    mock_ai_config.llm_endpoint = "http://test-url"


def test_thinking_is_dropped_when_the_model_cannot_think(
    mock_ai_config,
    mock_ollama_llm,
):
    # Ollama rejects `think` for a model without the capability, which would
    # fail the whole answer instead of just skipping the reasoning step.
    _configure_ollama(mock_ai_config)

    with patch(
        "paperless_ai.client.model_supports_thinking",
        return_value=False,
    ) as supports:
        client = AIClient(thinking=True)

    supports.assert_called_once_with("http://test-url", "test_model")
    assert client.thinking is False
    assert mock_ollama_llm.call_args.kwargs["thinking"] is False


def test_thinking_is_kept_when_the_model_can_think(mock_ai_config, mock_ollama_llm):
    _configure_ollama(mock_ai_config)

    with patch("paperless_ai.client.model_supports_thinking", return_value=True):
        client = AIClient(thinking=True)

    assert client.thinking is True
    assert mock_ollama_llm.call_args.kwargs["thinking"] is True


def test_capabilities_are_not_looked_up_unless_thinking_is_requested(
    mock_ai_config,
    mock_ollama_llm,
    settings,
):
    _configure_ollama(mock_ai_config)
    settings.LLM_THINKING = False

    with patch("paperless_ai.client.model_supports_thinking") as supports:
        AIClient()
        AIClient(thinking=False)

    supports.assert_not_called()


def test_capabilities_are_not_looked_up_for_other_backends(
    mock_ai_config,
    mock_openai_llm,
):
    mock_ai_config.llm_backend = "openai"
    mock_ai_config.llm_model = "gpt"
    mock_ai_config.llm_endpoint = None

    with patch("paperless_ai.client.model_supports_thinking") as supports:
        AIClient(thinking=True)

    supports.assert_not_called()


def test_stream_chat_retries_once_when_the_server_cannot_be_reached(
    mock_ai_config,
    mock_ollama_llm,
):
    _configure_ollama(mock_ai_config)
    llm = mock_ollama_llm.return_value
    llm.stream_chat.side_effect = [
        ConnectionError("Failed to connect to Ollama."),
        iter([MagicMock(delta="ok")]),
    ]
    client = AIClient()

    with patch("paperless_ai.client.time.sleep") as sleep:
        result = list(client.stream_chat([ChatMessage(role="user", content="hi")]))

    assert result == ["ok"]
    assert llm.stream_chat.call_count == 2
    sleep.assert_called_once()


def test_stream_chat_gives_up_after_the_retry(mock_ai_config, mock_ollama_llm):
    _configure_ollama(mock_ai_config)
    llm = mock_ollama_llm.return_value
    llm.stream_chat.side_effect = ConnectionError("Failed to connect to Ollama.")
    client = AIClient()

    with (
        patch("paperless_ai.client.time.sleep"),
        pytest.raises(ConnectionError),
    ):
        list(client.stream_chat([ChatMessage(role="user", content="hi")]))

    assert llm.stream_chat.call_count == 2


def test_stream_chat_does_not_restart_an_answer_already_under_way(
    mock_ai_config,
    mock_ollama_llm,
):
    # Starting over would repeat text the caller has already received.
    _configure_ollama(mock_ai_config)
    llm = mock_ollama_llm.return_value

    def broken_stream():
        yield MagicMock(delta="par")
        raise ConnectionError("Failed to connect to Ollama.")

    llm.stream_chat.return_value = broken_stream()
    client = AIClient()
    received = []

    with pytest.raises(ConnectionError):
        for delta in client.stream_chat([ChatMessage(role="user", content="hi")]):
            received.append(delta)

    assert received == ["par"]
    assert llm.stream_chat.call_count == 1


def test_stream_chat_does_not_retry_other_errors(mock_ai_config, mock_ollama_llm):
    _configure_ollama(mock_ai_config)
    llm = mock_ollama_llm.return_value
    llm.stream_chat.side_effect = ValueError("bad request")
    client = AIClient()

    with pytest.raises(ValueError, match="bad request"):
        list(client.stream_chat([ChatMessage(role="user", content="hi")]))

    assert llm.stream_chat.call_count == 1


def test_stream_chat_reports_an_answer_with_no_text(mock_ai_config, mock_ollama_llm):
    # A model that reasons first can spend its whole output limit thinking and
    # finish with nothing to show, which must not look like a blank success.
    _configure_ollama(mock_ai_config)
    llm = mock_ollama_llm.return_value
    llm.stream_chat.return_value = iter([MagicMock(delta=""), MagicMock(delta="")])
    client = AIClient()

    with pytest.raises(EmptyAnswerError):
        list(client.stream_chat([ChatMessage(role="user", content="hi")]))

    # Nothing to gain from asking again.
    assert llm.stream_chat.call_count == 1
