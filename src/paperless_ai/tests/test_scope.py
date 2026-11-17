import asyncio
import datetime
import json
from unittest.mock import patch

import pytest
from django.contrib.auth.models import Permission
from django.contrib.auth.models import User
from django.core.cache import cache
from rest_framework.test import APIClient

from documents.models import Correspondent
from documents.models import Document
from documents.models import DocumentType
from documents.models import StoragePath
from documents.models import Tag
from paperless_ai.scope import ScopeError
from paperless_ai.scope import apply_filters

pytestmark = pytest.mark.django_db

CHAT = "/api/documents/chat/"


def read_stream(response) -> str:
    """The body of a streamed chat answer (an async iterator under ASGI)."""

    async def collect():
        return [chunk async for chunk in response.streaming_content]

    chunks = asyncio.run(collect())
    return b"".join(c if isinstance(c, bytes) else c.encode() for c in chunks).decode()


@pytest.fixture
def archive():
    invoice = DocumentType.objects.create(name="فاتورة")
    letter = DocumentType.objects.create(name="كتاب")
    ministry = Correspondent.objects.create(name="الوزارة")
    finance = StoragePath.objects.create(name="المالية", path="finance/{title}")
    urgent = Tag.objects.create(name="عاجل")

    def make(title, *, created, document_type=None, **kwargs):
        return Document.objects.create(
            title=title,
            content=f"نص {title}",
            checksum=title,
            mime_type="application/pdf",
            created=created,
            document_type=document_type,
            **kwargs,
        )

    old_invoice = make(
        "فاتورة قديمة",
        created=datetime.date(2022, 3, 1),
        document_type=invoice,
        storage_path=finance,
    )
    new_invoice = make(
        "فاتورة حديثة",
        created=datetime.date(2024, 6, 1),
        document_type=invoice,
        correspondent=ministry,
        storage_path=finance,
    )
    new_letter = make(
        "كتاب حديث",
        created=datetime.date(2024, 7, 1),
        document_type=letter,
        correspondent=ministry,
    )
    new_letter.tags.add(urgent)
    return {
        "invoice": invoice,
        "ministry": ministry,
        "finance": finance,
        "urgent": urgent,
        "old_invoice": old_invoice,
        "new_invoice": new_invoice,
        "new_letter": new_letter,
    }


def ids(queryset):
    return set(queryset.values_list("pk", flat=True))


def test_no_filters_returns_the_same_queryset(archive):
    queryset = Document.objects.all()
    assert apply_filters(queryset, None) is queryset
    assert apply_filters(queryset, {}) is queryset
    # Paging and ordering say nothing about which documents are included.
    assert apply_filters(queryset, {"page": 2, "ordering": "-created"}) is queryset


def test_filters_by_type_date_correspondent_path_and_tag(archive):
    queryset = Document.objects.all()

    by_type = apply_filters(
        queryset,
        {"document_type__id__in": str(archive["invoice"].pk)},
    )
    assert ids(by_type) == {archive["old_invoice"].pk, archive["new_invoice"].pk}

    by_date = apply_filters(
        queryset,
        {"created__date__gte": "2024-01-01", "created__date__lte": "2024-06-30"},
    )
    assert ids(by_date) == {archive["new_invoice"].pk}

    by_correspondent = apply_filters(
        queryset,
        {"correspondent__id__in": [archive["ministry"].pk]},
    )
    assert ids(by_correspondent) == {
        archive["new_invoice"].pk,
        archive["new_letter"].pk,
    }

    by_path = apply_filters(queryset, {"storage_path__id": archive["finance"].pk})
    assert ids(by_path) == {archive["old_invoice"].pk, archive["new_invoice"].pk}

    by_tag = apply_filters(queryset, {"tags__id__all": str(archive["urgent"].pk)})
    assert ids(by_tag) == {archive["new_letter"].pk}

    combined = apply_filters(
        queryset,
        {
            "document_type__id__in": str(archive["invoice"].pk),
            "correspondent__id__in": str(archive["ministry"].pk),
        },
    )
    assert ids(combined) == {archive["new_invoice"].pk}


def test_filters_only_narrow_the_visible_documents(archive):
    visible = Document.objects.filter(pk=archive["new_letter"].pk)
    narrowed = apply_filters(
        visible,
        {"document_type__id__in": str(archive["invoice"].pk)},
    )
    assert ids(narrowed) == set()


@pytest.mark.parametrize(
    "filters",
    [
        "not an object",
        {"created__date__gte": "not a date"},
        {"document_type__id__in": {"nested": 1}},
        {"more_like_id": "abc"},
    ],
)
def test_malformed_filters_are_rejected(archive, filters):
    with pytest.raises(ScopeError):
        apply_filters(Document.objects.all(), filters)


def test_more_like_id_must_be_visible(archive):
    visible = Document.objects.exclude(pk=archive["old_invoice"].pk)
    with pytest.raises(ScopeError):
        apply_filters(visible, {"more_like_id": archive["old_invoice"].pk})


def test_full_text_query_uses_the_index(archive):
    with patch(
        "paperless_ai.scope._index_ids",
        return_value=[archive["new_letter"].pk],
    ) as index_ids:
        result = apply_filters(
            Document.objects.all(),
            {"query": "created:[-1 month to now]", "storage_path__isnull": "1"},
        )
    assert ids(result) == {archive["new_letter"].pk}
    params, restricted = index_ids.call_args.args
    assert params == {"query": "created:[-1 month to now]"}
    # The index search only sees what the database filters left.
    assert ids(restricted) == {archive["new_letter"].pk}


class TestChatWithFilters:
    @pytest.fixture(autouse=True)
    def setup(self, archive):
        cache.clear()
        self.archive = archive
        self.user = User.objects.create_user("reader", password="pw")
        self.user.user_permissions.add(
            Permission.objects.get(codename="view_document"),
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        # The view builds its model client before streaming; with the config
        # mocked a real one cannot be built, and no model is called here.
        with (
            patch("documents.views.AIConfig") as config,
            patch("documents.views.AIClient"),
        ):
            config.return_value.ai_enabled = True
            yield

    def post(self, body):
        return self.client.post(CHAT, json.dumps(body), content_type="application/json")

    def test_answer_draws_only_on_filtered_documents(self):
        with patch(
            "documents.views.stream_chat_with_documents",
            return_value=iter(["ok"]),
        ) as stream:
            response = self.post(
                {
                    "q": "لخّص",
                    "filters": {
                        "document_type__id__in": str(self.archive["invoice"].pk),
                    },
                },
            )
            body = read_stream(response)
        assert response.status_code == 200
        assert "ok" in body
        _, documents, _ = stream.call_args.args
        assert {d.pk for d in documents} == {
            self.archive["old_invoice"].pk,
            self.archive["new_invoice"].pk,
        }

    def test_no_matching_documents_says_so(self):
        with patch("documents.views.stream_chat_with_documents") as stream:
            response = self.post(
                {"q": "لخّص", "filters": {"created__date__gte": "2099-01-01"}},
            )
            body = read_stream(response)
        assert response.status_code == 200
        assert "لا توجد مستندات تطابق فلاتر البحث" in body
        stream.assert_not_called()

    def test_invalid_filters_are_a_bad_request(self):
        response = self.post({"q": "لخّص", "filters": {"created__date__gte": "x"}})
        assert response.status_code == 400
