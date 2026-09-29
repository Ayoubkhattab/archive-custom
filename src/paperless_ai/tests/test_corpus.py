from unittest.mock import patch

import pytest

from documents.models import Document
from paperless_ai import corpus

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def empty_corpus():
    corpus.clear()
    yield
    corpus.clear()


def make(title: str, content: str) -> Document:
    return Document.objects.create(
        title=title,
        content=content,
        mime_type="application/pdf",
        checksum=title,
    )


def test_returns_the_text_of_the_given_documents():
    a = make("أ", "عقد توريد")
    make("ب", "فاتورة")

    documents = corpus.corpus_for(Document.objects.filter(pk=a.pk))

    assert [(d.pk, d.title, d.content) for d in documents] == [
        (a.pk, "أ", "عقد توريد"),
    ]


def test_unchanged_documents_are_not_read_again():
    make("أ", "عقد توريد")
    first = corpus.corpus_for(Document.objects.all())

    with patch.object(Document.objects, "filter", wraps=Document.objects.filter) as reads:
        second = corpus.corpus_for(Document.objects.all())

    reads.assert_not_called()
    # The very same string objects, so the matcher's caches hit.
    assert second[0].content is first[0].content


def test_edited_and_deleted_documents_are_refreshed():
    a = make("أ", "نص قديم")
    b = make("ب", "سيحذف")
    corpus.corpus_for(Document.objects.all())

    a.content = "نص جديد"
    a.save()
    b.delete()
    documents = corpus.corpus_for(Document.objects.all())

    assert [(d.pk, d.content) for d in documents] == [(a.pk, "نص جديد")]


def test_a_plain_list_is_passed_through():
    items = [object()]
    assert corpus.corpus_for(items) == items
