import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llama_index.core.llms import ChatMessage
    from llama_index.llms.openai import OpenAI

from django.conf import settings

from paperless.config import AIConfig
from paperless_ai.base_model import DocumentClassifierSchema

logger = logging.getLogger("paperless_ai.client")


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

    def __init__(self):
        self.settings = AIConfig()
        self.llm = self.get_llm()

    def get_llm(self):
        if self.settings.llm_backend == "ollama":
            try:
                from llama_index.llms.ollama import Ollama
            except ModuleNotFoundError as exc:
                raise ModuleNotFoundError(
                    "llama-index-llms-ollama is required for llm_backend=ollama",
                ) from exc
            options = {"num_predict": settings.LLM_MAX_OUTPUT_TOKENS}
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
                thinking=settings.LLM_THINKING,
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
        for chunk in self.llm.stream_chat(messages):
            if chunk.delta:
                yield chunk.delta
