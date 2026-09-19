from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from llama_index.core import VectorStoreIndex
from llama_index.core.schema import TextNode

from paperless_ai.chat import stream_chat_with_documents


@pytest.fixture(autouse=True)
def patch_embed_model():
    from llama_index.core import settings as llama_settings
    from llama_index.core.embeddings.mock_embed_model import MockEmbedding

    # Use a real BaseEmbedding subclass to satisfy llama-index 0.14 validation
    llama_settings.Settings.embed_model = MockEmbedding(embed_dim=1536)
    yield
    llama_settings.Settings.embed_model = None


@pytest.fixture(autouse=True)
def patch_embed_nodes():
    with patch(
        "llama_index.core.indices.vector_store.base.embed_nodes",
    ) as mock_embed_nodes:
        mock_embed_nodes.side_effect = lambda nodes, *_args, **_kwargs: {
            node.node_id: [0.1] * 1536 for node in nodes
        }
        yield


@pytest.fixture
def mock_document():
    doc = MagicMock()
    doc.pk = 1
    doc.title = "Test Document"
    doc.filename = "test_file.pdf"
    doc.content = "This is the document content."
    return doc


def _prepare(mock_client_cls, mock_load_index, nodes, chunks=("chunk1", "chunk2")):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.stream_chat.return_value = iter(chunks)

    mock_index = MagicMock()
    mock_index.docstore.docs.values.return_value = nodes
    mock_load_index.return_value = mock_index
    return mock_client


def _user_prompt(mock_client) -> str:
    messages = mock_client.stream_chat.call_args.args[0]
    return messages[1].content


def test_stream_chat_with_one_document_full_content(mock_document):
    with (
        patch("paperless_ai.chat.AIClient") as mock_client_cls,
        patch("paperless_ai.chat.load_or_build_index") as mock_load_index,
        patch.object(VectorStoreIndex, "as_retriever") as mock_as_retriever,
    ):
        node = TextNode(
            text="This is node content.",
            metadata={"document_id": str(mock_document.pk), "title": "Test Document"},
        )
        mock_client = _prepare(mock_client_cls, mock_load_index, [node])

        output = list(stream_chat_with_documents("What is this?", [mock_document]))

        assert output == ["chunk1", "chunk2"]
        prompt = _user_prompt(mock_client)
        assert "This is the document content." in prompt
        assert "Query: What is this?" in prompt
        # Content fits the budget, so no retrieval (and no embedding) happens.
        mock_as_retriever.assert_not_called()


def test_stream_chat_with_long_document_adds_top_matches(mock_document, settings):
    settings.LLM_CHAT_MAX_CONTEXT_CHARS = 10
    mock_document.content = "0123456789" + "x" * 50

    with (
        patch("paperless_ai.chat.AIClient") as mock_client_cls,
        patch("paperless_ai.chat.load_or_build_index") as mock_load_index,
        patch.object(VectorStoreIndex, "as_retriever") as mock_as_retriever,
    ):
        node = TextNode(
            text="Relevant snippet.",
            metadata={"document_id": "1", "title": "Test Document"},
        )
        mock_client = _prepare(mock_client_cls, mock_load_index, [node])
        mock_as_retriever.return_value.retrieve.return_value = [node]

        list(stream_chat_with_documents("Find it", [mock_document]))

        prompt = _user_prompt(mock_client)
        assert "0123456789" in prompt
        assert "x" * 50 not in prompt
        assert "TOP MATCHES" in prompt
        assert "Relevant snippet." in prompt


def test_stream_chat_with_multiple_documents_retrieval():
    with (
        patch("paperless_ai.chat.AIClient") as mock_client_cls,
        patch("paperless_ai.chat.load_or_build_index") as mock_load_index,
        patch.object(VectorStoreIndex, "as_retriever") as mock_as_retriever,
    ):
        node1 = TextNode(
            text="Content for doc 1.",
            metadata={"document_id": "1", "title": "Document 1"},
        )
        node2 = TextNode(
            text="Content for doc 2.",
            metadata={"document_id": "2", "title": "Document 2"},
        )
        mock_client = _prepare(mock_client_cls, mock_load_index, [node1, node2])
        mock_as_retriever.return_value.retrieve.return_value = [node1, node2]

        output = list(
            stream_chat_with_documents(
                "What's up?",
                [MagicMock(pk=1), MagicMock(pk=2)],
            ),
        )

        assert output == ["chunk1", "chunk2"]
        prompt = _user_prompt(mock_client)
        assert "Content for doc 1." in prompt
        assert "Content for doc 2." in prompt


def test_stream_chat_no_matching_nodes():
    with (
        patch("paperless_ai.chat.AIClient") as mock_client_cls,
        patch("paperless_ai.chat.load_or_build_index") as mock_load_index,
    ):
        _prepare(mock_client_cls, mock_load_index, [])

        output = list(stream_chat_with_documents("Any info?", [MagicMock(pk=1)]))

        assert output == ["Sorry, I couldn't find any content to answer your question."]
