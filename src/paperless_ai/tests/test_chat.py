from types import SimpleNamespace
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from llama_index.core import VectorStoreIndex
from llama_index.core.schema import NodeWithScore
from llama_index.core.schema import TextNode

from paperless_ai.chat import INDEX_BUILD_LOCK
from paperless_ai.chat import INDEX_BUILDING_MESSAGE
from paperless_ai.chat import NO_CONTENT_MESSAGE
from paperless_ai.chat import _queue_index_build
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

        assert output == [NO_CONTENT_MESSAGE]


@pytest.fixture
def clear_index_lock():
    from django.core.cache import cache

    cache.delete(INDEX_BUILD_LOCK)
    yield
    cache.delete(INDEX_BUILD_LOCK)


def test_stream_chat_without_index_answers_immediately_and_queues_build(
    clear_index_lock,
):
    with (
        patch("paperless_ai.chat.AIClient"),
        patch("paperless_ai.chat.load_or_build_index", side_effect=ValueError),
        patch("documents.tasks.llmindex_index") as mock_task,
    ):
        first = list(stream_chat_with_documents("Any info?", [MagicMock(pk=1)]))
        second = list(stream_chat_with_documents("Any info?", [MagicMock(pk=1)]))

    assert first == second == [INDEX_BUILDING_MESSAGE]
    # The lock keeps repeated questions from queueing a build each time.
    mock_task.delay.assert_called_once()


def test_queue_index_build_releases_lock_when_broker_is_down(clear_index_lock):
    from django.core.cache import cache

    with patch("documents.tasks.llmindex_index") as mock_task:
        mock_task.delay.side_effect = ConnectionError("broker down")
        _queue_index_build()

    assert cache.get(INDEX_BUILD_LOCK) is None


def _ranked_index(disallowed: int, allowed_ids: list[str]):
    nodes = [
        TextNode(text=f"other {i}", metadata={"document_id": f"x{i}", "title": "o"})
        for i in range(disallowed)
    ] + [
        TextNode(text=f"mine {d}", metadata={"document_id": d, "title": f"T{d}"})
        for d in allowed_ids
    ]
    index = MagicMock()
    index.docstore.docs = {n.node_id: n for n in nodes}
    seen_k = []

    def as_retriever(similarity_top_k):
        seen_k.append(similarity_top_k)
        retriever = MagicMock()
        retriever.retrieve.return_value = [
            NodeWithScore(node=n, score=1.0) for n in nodes[:similarity_top_k]
        ]
        return retriever

    index.as_retriever.side_effect = as_retriever
    return index, seen_k


def test_multi_document_search_uses_stored_vectors_and_widens_until_found():
    index, seen_k = _ranked_index(disallowed=60, allowed_ids=["1", "2"])

    with (
        patch("paperless_ai.chat.AIClient") as mock_client_cls,
        patch("paperless_ai.chat.load_or_build_index", return_value=index),
        patch("paperless_ai.chat.LOCAL_RETRIEVAL_MAX_NODES", 1),
        patch("paperless_ai.chat.VectorStoreIndex") as mock_local_index,
    ):
        mock_client_cls.return_value.stream_chat.return_value = iter(["ok"])
        output = list(
            stream_chat_with_documents(
                "question",
                [MagicMock(pk=1), MagicMock(pk=2)],
            ),
        )
        prompt = mock_client_cls.return_value.stream_chat.call_args.args[0][1].content

    assert output == ["ok"]
    # Nothing was re-embedded, and the search widened once (50 -> all 62).
    mock_local_index.assert_not_called()
    assert seen_k == [50, 62]
    assert "mine 1" in prompt
    assert "mine 2" in prompt
    assert "other 0" not in prompt
    assert "other 59" not in prompt


def test_a_failed_index_search_is_reported_as_such_and_not_as_a_model_error():
    # The persisted index and its document store can disagree, e.g. after
    # documents were deleted. That used to surface as "the model could not
    # answer" although the model was never called.
    with (
        patch("paperless_ai.chat.AIClient") as mock_client_cls,
        patch("paperless_ai.chat.load_or_build_index") as mock_load_index,
        patch("paperless_ai.chat._retrieve", side_effect=KeyError("7")),
    ):
        node = TextNode(
            text="Content.",
            metadata={"document_id": "1", "title": "Document 1"},
        )
        mock_client = _prepare(mock_client_cls, mock_load_index, [node, node])

        output = list(
            stream_chat_with_documents(
                "What's up?",
                [MagicMock(pk=1), MagicMock(pk=2)],
            ),
        )

    assert len(output) == 1
    assert "ليس خطأً في النموذج" in output[0]
    assert "document_llmindex rebuild" in output[0]
    # The exception is named so the cause can be told apart without the logs.
    assert "KeyError" in output[0]
    mock_client.stream_chat.assert_not_called()


def test_a_long_document_is_still_answered_when_the_index_search_fails(
    mock_document,
    settings,
):
    # The matches only add to a document that is already in the prompt.
    settings.LLM_CHAT_MAX_CONTEXT_CHARS = 10
    mock_document.content = "0123456789" + "x" * 50

    with (
        patch("paperless_ai.chat.AIClient") as mock_client_cls,
        patch("paperless_ai.chat.load_or_build_index") as mock_load_index,
        patch("paperless_ai.chat._retrieve", side_effect=KeyError("7")),
    ):
        node = TextNode(
            text="Relevant snippet.",
            metadata={"document_id": "1", "title": "Test Document"},
        )
        mock_client = _prepare(mock_client_cls, mock_load_index, [node])

        output = list(stream_chat_with_documents("Find it", [mock_document]))

    assert output == ["chunk1", "chunk2"]
    prompt = _user_prompt(mock_client)
    assert "0123456789" in prompt
    assert "TOP MATCHES" not in prompt


def _ocr_document(pk, title, content, created="2026-01-01"):
    return SimpleNamespace(
        pk=pk,
        title=title,
        filename=f"{pk}.pdf",
        content=content,
        created=created,
    )


OCR_DOCUMENTS = [
    _ocr_document(1, "عقد إيجار المكتب", "شروط عقد الإيجار: المبلغ الشهري 5000 ريال."),
    _ocr_document(2, "كشف الرواتب", "رواتب الموظفين.\n\nإجمالي الرواتب 90000 ريال."),
    _ocr_document(3, "محضر اجتماع", "تمت مناقشة خطة العام القادم."),
]


def test_a_question_over_several_documents_is_answered_from_their_ocr_text():
    # No embedding model or vector index is needed when the stored OCR text
    # already contains the answer, which is what makes this route fast.
    with (
        patch("paperless_ai.chat.AIClient") as mock_client_cls,
        patch("paperless_ai.chat.load_or_build_index") as mock_load_index,
    ):
        mock_client = _prepare(mock_client_cls, mock_load_index, [])

        output = list(stream_chat_with_documents("ما إجمالي الرواتب؟", OCR_DOCUMENTS))

    assert output == ["chunk1", "chunk2"]
    mock_load_index.assert_not_called()
    prompt = _user_prompt(mock_client)
    assert "90000" in prompt
    assert "كشف الرواتب" in prompt
    assert "محضر اجتماع" not in prompt


def test_a_question_naming_nothing_describes_the_archive():
    with (
        patch("paperless_ai.chat.AIClient") as mock_client_cls,
        patch("paperless_ai.chat.load_or_build_index") as mock_load_index,
    ):
        mock_client = _prepare(mock_client_cls, mock_load_index, [])

        list(stream_chat_with_documents("ماذا لدي؟", OCR_DOCUMENTS))

    mock_load_index.assert_not_called()
    prompt = _user_prompt(mock_client)
    assert "عدد المستندات: 3" in prompt
    assert "عقد إيجار المكتب" in prompt


def test_a_question_the_ocr_text_cannot_answer_falls_back_to_the_index():
    # A paraphrase shares no words with the documents; the semantic search can
    # still find it.
    with (
        patch("paperless_ai.chat.AIClient") as mock_client_cls,
        patch("paperless_ai.chat.load_or_build_index") as mock_load_index,
        patch.object(VectorStoreIndex, "as_retriever") as mock_as_retriever,
    ):
        node1 = TextNode(text="Node 1.", metadata={"document_id": "1", "title": "A"})
        node2 = TextNode(text="Node 2.", metadata={"document_id": "2", "title": "B"})
        mock_client = _prepare(mock_client_cls, mock_load_index, [node1, node2])
        mock_as_retriever.return_value.retrieve.return_value = [node1]

        output = list(stream_chat_with_documents("سيارة الشركة", OCR_DOCUMENTS[:2]))

    assert output == ["chunk1", "chunk2"]
    mock_load_index.assert_called_once()
    assert "Node 1." in _user_prompt(mock_client)


def test_a_failing_ocr_search_falls_back_instead_of_failing_the_chat():
    with (
        patch("paperless_ai.chat.AIClient"),
        patch("paperless_ai.chat.load_or_build_index", side_effect=ValueError),
        patch("paperless_ai.chat.search_documents", side_effect=RuntimeError("boom")),
        patch("paperless_ai.chat._queue_index_build"),
    ):
        output = list(stream_chat_with_documents("ما إجمالي الرواتب؟", OCR_DOCUMENTS))

    assert output == [INDEX_BUILDING_MESSAGE]


def test_the_model_is_instructed_in_arabic_and_asked_for_an_arabic_answer():
    # Small models answer in the language the instructions are written in.
    with (
        patch("paperless_ai.chat.AIClient") as mock_client_cls,
        patch("paperless_ai.chat.load_or_build_index") as mock_load_index,
    ):
        mock_client = _prepare(mock_client_cls, mock_load_index, [])

        list(stream_chat_with_documents("ما إجمالي الرواتب؟", OCR_DOCUMENTS))

    system, user = mock_client.stream_chat.call_args.args[0]
    assert "أنت مساعد" in system.content
    assert "الإجابة بالعربية" in system.content
    assert user.content.rstrip().endswith("الإجابة بالعربية:")
    assert "Query: ما إجمالي الرواتب؟" in user.content


def test_the_deep_mode_reads_more_of_the_ocr_text_than_the_fast_mode():
    long_documents = [
        _ocr_document(i, f"عقد {i}", "عقد مهم " * 300 + f" رقم {i}") for i in range(10)
    ]
    sizes = {}
    for mode in ("fast", "deep"):
        with (
            patch("paperless_ai.chat.AIClient") as mock_client_cls,
            patch("paperless_ai.chat.load_or_build_index") as mock_load_index,
        ):
            mock_client = _prepare(mock_client_cls, mock_load_index, [])
            list(stream_chat_with_documents("ما بنود العقد؟", long_documents, mode))
            sizes[mode] = len(_user_prompt(mock_client))

    assert sizes["fast"] < sizes["deep"]
