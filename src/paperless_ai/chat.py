import logging
import time

from django.conf import settings
from django.core.cache import cache
from llama_index.core import VectorStoreIndex
from llama_index.core.llms import ChatMessage

from documents.models import Document
from paperless_ai.client import AIClient
from paperless_ai.indexing import load_or_build_index

logger = logging.getLogger("paperless_ai.chat")

# At or below this many chunks, embedding them again is cheap. Above it, search
# the persisted vector index instead of re-embedding on every question.
LOCAL_RETRIEVAL_MAX_NODES = 40

INDEX_BUILD_LOCK = "paperless_ai.index_build_queued"
INDEX_BUILD_LOCK_SECONDS = 1800
INDEX_BUILDING_MESSAGE = (
    "جارٍ بناء فهرس البحث بالذكاء الاصطناعي في الخلفية، "
    "يرجى المحاولة مجدداً بعد بضع دقائق.\n"
    "The AI search index is being built in the background. "
    "Please try again in a few minutes."
)

# Answers are always Arabic, whatever language the question or the documents are
# in. A small model follows the instruction that comes last, so it is repeated
# at the end of the user message too.
ARABIC_ONLY = (
    "Always answer in Arabic (Modern Standard Arabic) only, even if the question "
    "or the documents are in another language. Use another language only for "
    "proper names and technical terms that have no Arabic equivalent. "
    "أجب بالعربية الفصحى فقط."
)

NO_CONTENT_MESSAGE = "عذراً، لم أجد في المستندات ما يمكن أن أجيب به عن سؤالك."

# Answer modes. FAST is a short, direct reply; DEEP retrieves more context and
# asks for a structured report, which is slower but far more useful to export.
MODE_FAST = "fast"
MODE_DEEP = "deep"
CHAT_MODES = (MODE_FAST, MODE_DEEP)


def normalize_mode(value) -> str:
    """Fall back to the fast mode for anything unrecognised."""
    return value if value in CHAT_MODES else MODE_FAST


_BASE_SYSTEM_PROMPT = (
    "You are a document assistant. Answer using ONLY the context provided. "
    "If the context does not contain the answer, say (in Arabic) that you don't "
    "know. "
)

SYSTEM_PROMPT = _BASE_SYSTEM_PROMPT + ARABIC_ONLY

FAST_SYSTEM_PROMPT = (
    _BASE_SYSTEM_PROMPT
    + "Answer directly and briefly: a few sentences, or a short list when the "
    "question asks for several items. Do not add headings or preamble. "
    + ARABIC_ONLY
)

DEEP_SYSTEM_PROMPT = (
    _BASE_SYSTEM_PROMPT
    + "Produce a thorough, well-organised report rather than a short reply. "
    "Work through the context carefully before answering, compare what the "
    "different documents say, and call out any disagreement or missing "
    "information explicitly. Structure the answer in Markdown with a short "
    "summary first, then headed sections, and name the document titles you "
    "relied on. Never invent a fact that is not in the context. "
    + ARABIC_ONLY
)

_USER_PROMPT_BODY = (
    "Context information is below.\n"
    "---------------------\n"
    "{context}\n"
    "---------------------\n"
    "Given the context information and not prior knowledge, answer the query.\n"
    "Query: {query}\n\n"
)

USER_PROMPT_TMPL = _USER_PROMPT_BODY + ARABIC_ONLY

DEEP_USER_PROMPT_TMPL = (
    _USER_PROMPT_BODY
    + "اكتب تقريراً مفصّلاً ومنظّماً بعناوين، "
    "يبدأ بخلاصة موجزة، ويذكر عناوين المستندات التي اعتمدت عليها.\n\n"
    + ARABIC_ONLY
)


# Per-mode knobs. A deep answer needs a wider view of the archive and room to
# write the report; the fast one keeps the defaults so it stays quick.
MODE_SETTINGS = {
    MODE_FAST: {
        "system_prompt": FAST_SYSTEM_PROMPT,
        "user_prompt": USER_PROMPT_TMPL,
        "top_k_multiplier": 1,
        "max_output_tokens": None,
        "thinking": False,
        "single_document_style": (
            "Answer directly and briefly, without headings or preamble. "
        ),
    },
    MODE_DEEP: {
        "system_prompt": DEEP_SYSTEM_PROMPT,
        "user_prompt": DEEP_USER_PROMPT_TMPL,
        "top_k_multiplier": 3,
        "max_output_tokens": settings.LLM_MAX_OUTPUT_TOKENS * 3,
        "thinking": True,
        "single_document_style": (
            "Write a thorough, well-organised report rather than a short reply: "
            "a brief summary first, then headed Markdown sections, and state "
            "explicitly anything the document does not cover. "
        ),
    },
}


def _queue_index_build() -> None:
    """
    Build the index in a celery task. Doing it inside the chat request embeds
    every document on the CPU while the user stares at a frozen chat window.
    """
    if not cache.add(INDEX_BUILD_LOCK, True, timeout=INDEX_BUILD_LOCK_SECONDS):
        return
    try:
        from documents.tasks import llmindex_index

        llmindex_index.delay(
            progress_bar_disable=True,
            rebuild=False,
            scheduled=False,
            auto=True,
        )
    except Exception:
        cache.delete(INDEX_BUILD_LOCK)
        logger.warning("Could not queue the LLM index build", exc_info=True)


INDEX_SEARCH_FAILED_MESSAGE = (
    "⚠️ تعذّر البحث في فهرس المستندات، وهذا ليس خطأً في النموذج. غالباً الفهرس "
    "غير متزامن مع المستندات (بعد حذف بعضها أو تعديله) ويحتاج إعادة بناء: "
    "python manage.py document_llmindex rebuild\n"
    "Searching the document index failed; this is not a model error. The index "
    "is probably out of sync with the documents and needs rebuilding.\n"
    "التفاصيل / Details: {detail}"
)


def _index_search_failure(exc: Exception) -> str:
    """
    Log a failed index search and word it for the chat.

    The exception type and message are included because this is the failure the
    generic "the model could not answer" used to hide, and they are what is
    needed to tell an index that is out of sync from, say, an embedding model
    that could not be loaded.
    """
    logger.error("Searching the LLM index failed", exc_info=exc)
    detail = f"{type(exc).__name__}: {exc}"[:200]
    return INDEX_SEARCH_FAILED_MESSAGE.format(detail=detail)


def _retrieve(index, nodes, allowed_ids: set[str], query_str: str, top_k: int):
    if len(nodes) <= LOCAL_RETRIEVAL_MAX_NODES:
        return (
            VectorStoreIndex(nodes=nodes)
            .as_retriever(similarity_top_k=top_k)
            .retrieve(query_str)
        )

    # FAISS can't filter on metadata, so search wider and filter afterwards,
    # widening until enough of the user's own documents are found.
    total = len(index.docstore.docs)
    k = min(max(top_k * 10, 50), total)
    while True:
        results = index.as_retriever(similarity_top_k=k).retrieve(query_str)
        matches = [r for r in results if r.metadata.get("document_id") in allowed_ids]
        if len(matches) >= top_k or k >= total:
            return matches[:top_k]
        k = min(k * 4, total)


def _format_matches(top_nodes) -> str:
    return "\n\n".join(
        f"TITLE: {node.metadata.get('title')}\n{node.text[: settings.LLM_CHAT_SNIPPET_CHARS]}"
        for node in top_nodes
    )


def stream_chat_with_documents(
    query_str: str,
    documents: list[Document],
    mode: str = MODE_FAST,
):
    started = time.monotonic()
    mode = normalize_mode(mode)
    mode_settings = MODE_SETTINGS[mode]
    client = AIClient(
        max_output_tokens=mode_settings["max_output_tokens"],
        thinking=mode_settings["thinking"],
    )
    top_k = settings.LLM_CHAT_TOP_K * mode_settings["top_k_multiplier"]
    try:
        index = load_or_build_index()
    except ValueError:
        logger.info("No LLM index found; queueing a background build.")
        _queue_index_build()
        yield INDEX_BUILDING_MESSAGE
        return

    allowed_ids = {str(doc.pk) for doc in documents}

    # Filter only the node(s) that match the document IDs
    nodes = [
        node
        for node in index.docstore.docs.values()
        if node.metadata.get("document_id") in allowed_ids
    ]

    if len(nodes) == 0:
        logger.warning("No nodes found for the given documents.")
        yield NO_CONTENT_MESSAGE
        return

    if len(documents) == 1:
        # Just one doc — provide its content directly
        doc = documents[0]
        content = doc.content or ""
        context_body = content

        max_chars = settings.LLM_CHAT_MAX_CONTEXT_CHARS
        if len(content) > max_chars:
            logger.info(
                "Truncating single-document context from %s to %s characters",
                len(content),
                max_chars,
            )
            context_body = content[:max_chars]

            # The matches only add to a document already in the prompt, so a
            # broken index is logged and skipped instead of failing the answer.
            try:
                top_nodes = _retrieve(
                    index,
                    nodes,
                    allowed_ids,
                    query_str,
                    top_k=min(3, top_k),
                )
            except Exception as exc:
                _index_search_failure(exc)
                top_nodes = []
            if len(top_nodes) > 0:
                context_body = (
                    f"{context_body}\n\nTOP MATCHES:\n{_format_matches(top_nodes)}"
                )

        context = f"TITLE: {doc.title or doc.filename}\n{context_body}"
    else:
        try:
            top_nodes = _retrieve(
                index,
                nodes,
                allowed_ids,
                query_str,
                top_k=top_k,
            )
        except Exception as exc:
            yield _index_search_failure(exc)
            return

        if len(top_nodes) == 0:
            logger.warning("Retriever returned no nodes for the given documents.")
            yield NO_CONTENT_MESSAGE
            return

        context = _format_matches(top_nodes)

    messages = [
        ChatMessage(role="system", content=mode_settings["system_prompt"]),
        ChatMessage(
            role="user",
            content=mode_settings["user_prompt"].format(
                context=context,
                query=query_str,
            ),
        ),
    ]
    logger.debug("Document chat messages: %s", messages)

    prepared = time.monotonic()
    first_token_logged = False
    for chunk in client.stream_chat(messages):
        if not first_token_logged:
            first_token_logged = True
            logger.info(
                "AI chat (%s): context ready after %.2fs, first token after %.2fs",
                mode,
                prepared - started,
                time.monotonic() - started,
            )
        yield chunk
    logger.info("AI chat: finished in %.2fs", time.monotonic() - started)
