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

SYSTEM_PROMPT = (
    "You are a document assistant. Answer using ONLY the context provided. "
    "If the context does not contain the answer, say (in Arabic) that you don't "
    "know. " + ARABIC_ONLY
)

USER_PROMPT_TMPL = (
    "Context information is below.\n"
    "---------------------\n"
    "{context}\n"
    "---------------------\n"
    "Given the context information and not prior knowledge, answer the query.\n"
    "Query: {query}\n\n" + ARABIC_ONLY
)


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


def stream_chat_with_documents(query_str: str, documents: list[Document]):
    started = time.monotonic()
    client = AIClient()
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

            top_nodes = _retrieve(
                index,
                nodes,
                allowed_ids,
                query_str,
                top_k=min(3, settings.LLM_CHAT_TOP_K),
            )
            if len(top_nodes) > 0:
                context_body = (
                    f"{context_body}\n\nTOP MATCHES:\n{_format_matches(top_nodes)}"
                )

        context = f"TITLE: {doc.title or doc.filename}\n{context_body}"
    else:
        top_nodes = _retrieve(
            index,
            nodes,
            allowed_ids,
            query_str,
            top_k=settings.LLM_CHAT_TOP_K,
        )

        if len(top_nodes) == 0:
            logger.warning("Retriever returned no nodes for the given documents.")
            yield NO_CONTENT_MESSAGE
            return

        context = _format_matches(top_nodes)

    messages = [
        ChatMessage(role="system", content=SYSTEM_PROMPT),
        ChatMessage(
            role="user",
            content=USER_PROMPT_TMPL.format(context=context, query=query_str),
        ),
    ]
    logger.debug("Document chat messages: %s", messages)

    prepared = time.monotonic()
    first_token_logged = False
    for chunk in client.stream_chat(messages):
        if not first_token_logged:
            first_token_logged = True
            logger.info(
                "AI chat: context ready after %.2fs, first token after %.2fs",
                prepared - started,
                time.monotonic() - started,
            )
        yield chunk
    logger.info("AI chat: finished in %.2fs", time.monotonic() - started)
