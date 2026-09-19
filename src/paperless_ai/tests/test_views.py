import json
from unittest.mock import patch

import pytest
from django.contrib.auth.models import Permission
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client
from rest_framework.test import APIClient

from documents.models import Document
from paperless_ai.ratelimit import allow_request

pytestmark = pytest.mark.django_db

CLASSIFY = "/api/documents/ai_suggest/classify/"
SUGGEST = "/api/documents/ai_suggest/suggest/"


@pytest.fixture(autouse=True)
def ai_enabled():
    cache.clear()
    with patch("paperless_ai.views.AIConfig") as config:
        config.return_value.ai_enabled = True
        yield


def make_user(name: str, *codenames: str) -> User:
    user = User.objects.create_user(name, password="pw")
    for codename in codenames:
        user.user_permissions.add(Permission.objects.get(codename=codename))
    return user


@pytest.fixture
def document() -> Document:
    return Document.objects.create(
        title="Original",
        content="text " * 100,
        mime_type="application/pdf",
        checksum="abc123",
    )


def post(client: Client, path: str, payload) -> "object":
    return client.post(path, data=json.dumps(payload), content_type="application/json")


def signed_in(user: User, **kwargs) -> Client:
    client = Client(**kwargs)
    client.force_login(user)
    return client


def test_anonymous_users_are_sent_to_login(document):
    response = post(Client(), CLASSIFY, {"document_id": document.pk})

    assert response.status_code == 302
    assert "login" in response["Location"]


def test_csrf_token_is_required(document):
    user = make_user("viewer", "view_document")

    response = post(
        signed_in(user, enforce_csrf_checks=True),
        CLASSIFY,
        {"document_id": document.pk},
    )

    assert response.status_code == 403


def test_view_only_user_cannot_apply_a_classification(document):
    user = make_user("viewer", "view_document")

    with patch("paperless_ai.views.get_ai_document_classification") as classify:
        classify.return_value = {"title": "Hijacked"}
        response = post(
            signed_in(user),
            CLASSIFY,
            {"document_id": document.pk, "apply": True},
        )

    assert response.status_code == 403
    classify.assert_not_called()
    document.refresh_from_db()
    assert document.title == "Original"


def test_user_who_may_edit_can_apply_a_classification(document):
    user = make_user("editor", "view_document", "change_document")

    with (
        patch("paperless_ai.views.get_ai_document_classification") as classify,
        patch("paperless_ai.views.match_tags_by_name", return_value=[]),
        patch("paperless_ai.views.match_correspondents_by_name", return_value=[]),
        patch("paperless_ai.views.match_document_types_by_name", return_value=[]),
    ):
        classify.return_value = {"title": "New title"}
        response = post(
            signed_in(user),
            CLASSIFY,
            {"document_id": document.pk, "apply": True},
        )

    assert response.status_code == 200
    document.refresh_from_db()
    assert document.title == "New title"


def test_classifying_without_apply_never_modifies_the_document(document):
    user = make_user("viewer", "view_document")

    with patch("paperless_ai.views.get_ai_document_classification") as classify:
        classify.return_value = {"title": "Suggested"}
        response = post(
            signed_in(user),
            CLASSIFY,
            {"document_id": document.pk, "apply": "true"},
        )

    # Only a real boolean true applies changes, and this user may not edit.
    assert response.status_code == 200
    document.refresh_from_db()
    assert document.title == "Original"


def test_user_without_document_permission_is_refused(document):
    user = make_user("nobody")

    with patch("paperless_ai.views.get_ai_document_classification") as classify:
        response = post(signed_in(user), SUGGEST, {"document_id": document.pk})

    assert response.status_code == 403
    classify.assert_not_called()


def test_internal_error_details_are_not_returned(document):
    user = make_user("viewer", "view_document")

    with patch(
        "paperless_ai.views.get_ai_document_classification",
        side_effect=RuntimeError("secret-host:11434 refused the connection"),
    ):
        response = post(signed_in(user), SUGGEST, {"document_id": document.pk})

    assert response.status_code == 500
    assert "secret-host" not in response.content.decode()


def test_malformed_input_is_a_bad_request(document):
    user = make_user("viewer", "view_document")
    client = signed_in(user)

    invalid_json = client.post(SUGGEST, data="{nope", content_type="application/json")
    bad_id = post(client, SUGGEST, {"document_id": "abc"})

    assert invalid_json.status_code == 400
    assert bad_id.status_code == 400


def test_requests_over_the_limit_are_rejected(document):
    user = make_user("viewer", "view_document")

    with patch("paperless_ai.views.allow_request", return_value=False):
        response = post(signed_in(user), SUGGEST, {"document_id": document.pk})

    assert response.status_code == 429


def test_rate_limit_is_counted_per_user(settings):
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        "DEFAULT_THROTTLE_RATES": {"token": "10/min", "ai": "3/min"},
    }

    first = [allow_request(1) for _ in range(5)]
    other_user = allow_request(2)

    assert first == [True, True, True, False, False]
    assert other_user is True


def test_streaming_chat_requires_document_permission():
    client = APIClient()
    client.force_authenticate(make_user("nobody"))

    with patch("documents.views.AIConfig") as config:
        config.return_value.ai_enabled = True
        response = client.post("/api/documents/chat/", {"q": "hello"}, format="json")

    assert response.status_code == 403


def test_streaming_chat_rejects_oversized_questions():
    client = APIClient()
    client.force_authenticate(make_user("viewer", "view_document"))

    with patch("documents.views.AIConfig") as config:
        config.return_value.ai_enabled = True
        response = client.post(
            "/api/documents/chat/",
            {"q": "x" * 5000},
            format="json",
        )

    assert response.status_code == 400


def test_streaming_chat_response_is_not_compressed():
    client = APIClient()
    client.force_authenticate(make_user("viewer", "view_document"))
    Document.objects.create(
        title="T",
        content="text " * 100,
        mime_type="application/pdf",
        checksum="zzz",
    )

    with (
        patch("documents.views.AIConfig") as config,
        patch("documents.views.stream_chat_with_documents", return_value=iter(["hi"])),
    ):
        config.return_value.ai_enabled = True
        response = client.post(
            "/api/documents/chat/",
            {"q": "hello"},
            format="json",
            HTTP_ACCEPT_ENCODING="gzip",
        )

    assert response.status_code == 200
    assert response["Content-Encoding"] == "identity"
