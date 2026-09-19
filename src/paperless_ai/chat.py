import logging

from django.conf import settings
from llama_index.core import VectorStoreIndex
from llama_index.core.llms import ChatMessage

from documents.models import Document
from paperless_ai.client import AIClient
from paperless_ai.indexing import load_or_build_index
from paperless_ai.indexing import update_llm_index

logger = logging.getLogger("paperless_ai.chat")

SINGLE_DOC_SNIPPET_CHARS = 800

SYSTEM_PROMPT = (
    "You are a document assistant. Answer using ONLY the context provided. "
    "If the context does not contain the answer, say you don't know. "
    "Respond in the same language as the user's question."
)

USER_PROMPT_TMPL = (
    "Context information is below.\n"
    "---------------------\n"
    "{context}\n"
    "---------------------\n"
    "Given the context information and not prior knowledge, answer the query.\n"
    "Query: {query}"
)


def _retrieve(nodes, query_str: str, top_k: int):
    # Embeds only the selected documents' nodes, so build it only when needed.
    return (
        VectorStoreIndex(nodes=nodes)
        .as_retriever(similarity_top_k=top_k)
        .retrieve(query_str)
    )


def _format_matches(top_nodes) -> str:
    return "\n\n".join(
        f"TITLE: {node.metadata.get('title')}\n{node.text[:SINGLE_DOC_SNIPPET_CHARS]}"
        for node in top_nodes
    )


def stream_chat_with_documents(query_str: str, documents: list[Document]):
    client = AIClient()
    try:
        index = load_or_build_index()
    except ValueError:
        # No index exists on disk yet (first use) — build it from all
        # documents now instead of failing the request.
        logger.info("No LLM index found; building it now for the first time.")
        update_llm_index()
        try:
            index = load_or_build_index()
        except ValueError:
            yield "There are no indexed documents to search yet."
            return

    doc_ids = [str(doc.pk) for doc in documents]

    # Filter only the node(s) that match the document IDs
    nodes = [
        node
        for node in index.docstore.docs.values()
        if node.metadata.get("document_id") in doc_ids
    ]

    if len(nodes) == 0:
        logger.warning("No nodes found for the given documents.")
        yield "Sorry, I couldn't find any content to answer your question."
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

            top_nodes = _retrieve(nodes, query_str, top_k=3)
            if len(top_nodes) > 0:
                context_body = (
                    f"{context_body}\n\nTOP MATCHES:\n{_format_matches(top_nodes)}"
                )

        context = f"TITLE: {doc.title or doc.filename}\n{context_body}"
    else:
        top_nodes = _retrieve(nodes, query_str, top_k=5)

        if len(top_nodes) == 0:
            logger.warning("Retriever returned no nodes for the given documents.")
            yield "Sorry, I couldn't find any content to answer your question."
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

    yield from client.stream_chat(messages)
