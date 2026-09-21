import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llama_index.core.llms import ChatMessage
    from llama_index.llms.openai import OpenAI

from django.conf import settings

from paperless.config import AIConfig
from paperless_ai.base_model import DocumentClassifierSchema
from paperless_ai.llm_errors import EmptyAnswerError
from paperless_ai.llm_errors import is_transient_connection_error
from paperless_ai.ollama_info import model_supports_thinking

logger = logging.getLogger("paperless_ai.client")

DEFAULT_OLLAMA_ENDPOINT = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "llama3.1"

# One more try when the model server cannot be reached at all, e.g. while
# Ollama restarts after being stopped. Anything slower would leave the person
# waiting on a server that is not coming back.
CONNECT_RETRIES = 1
CONNECT_RETRY_DELAY_SECONDS = 2.0


def _parse_keep_alive(value: str) -> float | str:
    """Ollama takes seconds as a number ("-1" = forever) or a duration string."""
    try:
        return float(value)
    except ValueError:
        return value


class AIClient:
    """
    A client for interacting with an LLM backend.
    """

    def __init__(
        self,
        *,
        max_output_tokens: int | None = None,
        thinking: bool | None = None,
    ):
        """
        Args:
            max_output_tokens: Overrides PAPERLESS_AI_LLM_MAX_TOKENS. The deep
                answer mode needs a longer budget than a quick reply.
            thinking: Overrides PAPERLESS_AI_LLM_THINKING, so a single request
                can ask the model to reason before answering.
        """
        self.settings = AIConfig()
        self.max_output_tokens = max_output_tokens or settings.LLM_MAX_OUTPUT_TOKENS
        self.thinking = settings.LLM_THINKING if thinking is None else thinking
        if self.thinking and self.settings.llm_backend == "ollama":
            self.thinking = self._model_can_think()
        self.llm = self.get_llm()

    def _model_can_think(self) -> bool:
        """
        Ollama rejects `think` for a model without the capability, which would
        fail the whole answer, so the request is downgraded to a normal one.
        """
        endpoint = self.settings.llm_endpoint or DEFAULT_OLLAMA_ENDPOINT
        model = self.settings.llm_model or DEFAULT_OLLAMA_MODEL
        if model_supports_thinking(endpoint, model):
            return True
        logger.info(
            "Model %s does not report the thinking capability; answering without it.",
            model,
        )
        return False

    def get_llm(self):
        if self.settings.llm_backend == "ollama":
            try:
                from llama_index.llms.ollama import Ollama
            except ModuleNotFoundError as exc:
                raise ModuleNotFoundError(
                    "llama-index-llms-ollama is required for llm_backend=ollama",
                ) from exc
            options = {"num_predict": self.max_output_tokens}
            if settings.LLM_NUM_THREAD > 0:
                # Left unset, Ollama guesses the core count, which is often
                # wrong on virtual machines.
                options["num_thread"] = settings.LLM_NUM_THREAD
            return Ollama(
                model=self.settings.llm_model or "llama3.1",
                base_url=self.settings.llm_endpoint or "http://localhost:11434",
                request_timeout=settings.LLM_REQUEST_TIMEOUT,
                context_window=settings.LLM_CONTEXT_WINDOW,
                keep_alive=_parse_keep_alive(settings.LLM_KEEP_ALIVE),
                thinking=self.thinking,
                additional_kwargs=options,
            )
        elif self.settings.llm_backend == "openai":
            try:
                from llama_index.llms.openai import OpenAI
            except ModuleNotFoundError as exc:
                raise ModuleNotFoundError(
                    "llama-index-llms-openai is required for llm_backend=openai",
                ) from exc
            return OpenAI(
                model=self.settings.llm_model or "gpt-3.5-turbo",
                api_base=self.settings.llm_endpoint or None,
                api_key=self.settings.llm_api_key,
            )
        else:
            raise ValueError(f"Unsupported LLM backend: {self.settings.llm_backend}")

    def run_llm_query(self, prompt: str) -> str:
        from llama_index.core.llms import ChatMessage
        from llama_index.core.program.function_program import get_function_tool

        logger.debug(
            "Running LLM query against %s with model %s",
            self.settings.llm_backend,
            self.settings.llm_model,
        )

        user_msg = ChatMessage(role="user", content=prompt)
        tool = get_function_tool(DocumentClassifierSchema)
        result = self.llm.chat_with_tools(
            tools=[tool],
            user_msg=user_msg,
            chat_history=[],
        )
        tool_calls = self.llm.get_tool_calls_from_response(
            result,
            error_on_no_tool_call=True,
        )
        logger.debug("LLM query result: %s", tool_calls)
        parsed = DocumentClassifierSchema(**tool_calls[0].tool_kwargs)
        return parsed.model_dump()

    def run_chat(self, messages: list) -> str:
        logger.debug(
            "Running chat query against %s with model %s",
            self.settings.llm_backend,
            self.settings.llm_model,
        )
        result = self.llm.chat(messages)
        logger.debug("Chat result: %s", result)
        return result

    def stream_chat(self, messages: list):
        """Yield the model's answer incrementally as text deltas."""
        logger.debug(
            "Streaming chat query against %s with model %s",
            self.settings.llm_backend,
            self.settings.llm_model,
        )
        for attempt in range(CONNECT_RETRIES + 1):
            received = False
            try:
                for chunk in self.llm.stream_chat(messages):
                    if chunk.delta:
                        received = True
                        yield chunk.delta
                if not received:
                    raise EmptyAnswerError
                return
            except Exception as exc:
                # Only when nothing has been sent yet: after that, starting over
                # would repeat text the caller already has.
                if (
                    received
                    or attempt >= CONNECT_RETRIES
                    or not is_transient_connection_error(exc)
                ):
                    raise
                logger.warning(
                    "Model server unreachable, retrying in %.0fs: %s",
                    CONNECT_RETRY_DELAY_SECONDS,
                    exc,
                )
                time.sleep(CONNECT_RETRY_DELAY_SECONDS)
