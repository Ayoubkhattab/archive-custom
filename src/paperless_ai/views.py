import json
import logging
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from documents.models import Document
from documents.permissions import get_objects_for_user_owner_aware
from documents.permissions import has_perms_owner_aware
from paperless.config import AIConfig
from paperless_ai.ai_classifier import get_ai_document_classification
from paperless_ai.chat import stream_chat_with_documents
from paperless_ai.matching import match_correspondents_by_name
from paperless_ai.matching import match_document_types_by_name
from paperless_ai.matching import match_tags_by_name
from paperless_ai.ratelimit import allow_request

logger = logging.getLogger("paperless_ai.views")

MAX_MESSAGE_CHARS = 4000


class _BadRequest(Exception):
    pass


class _Forbidden(Exception):
    pass


def _get_visible_document(request, document_id):
    # Unowned documents are visible to every signed-in user at object level, so
    # the model-level permission has to be checked here as the REST API does.
    if not request.user.has_perm("documents.view_document"):
        raise _Forbidden
    try:
        document_id = int(document_id)
    except (TypeError, ValueError):
        raise _BadRequest("document_id must be a number") from None
    return get_objects_for_user_owner_aware(
        request.user,
        "documents.view_document",
        Document,
    ).get(id=document_id)


def _json_body(request) -> dict:
    try:
        data = json.loads(request.body)
    except ValueError:
        raise _BadRequest("Invalid JSON body") from None
    if not isinstance(data, dict):
        raise _BadRequest("Invalid JSON body")
    return data


def ai_endpoint(view):
    """
    Common guard for the AI views: POST only, signed-in users only, CSRF checked
    (the Angular client sends the X-CSRFToken header), rate limited per user, and
    no internal error text sent back to the browser.
    """

    @require_http_methods(["POST"])
    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not allow_request(request.user.pk):
            return JsonResponse({"error": "Too many requests"}, status=429)
        if not AIConfig().ai_enabled:
            return JsonResponse({"error": "AI is not enabled"}, status=400)
        try:
            return view(request, *args, **kwargs)
        except _BadRequest as e:
            return JsonResponse({"error": str(e)}, status=400)
        except _Forbidden:
            return JsonResponse({"error": "Insufficient permissions"}, status=403)
        except Document.DoesNotExist:
            return JsonResponse({"error": "Document not found"}, status=404)
        except Exception:
            logger.exception("AI request failed")
            return JsonResponse({"error": "AI request failed"}, status=500)

    return wrapper


@ai_endpoint
def ai_suggest(request):
    """AI-generated suggestions (tags/correspondent/document type/title) for a document."""
    data = _json_body(request)
    if not data.get("document_id"):
        raise _BadRequest("document_id required")

    document = _get_visible_document(request, data["document_id"])
    suggestions = get_ai_document_classification(document, request.user)

    return JsonResponse({"success": True, "suggestions": suggestions})


@ai_endpoint
def ai_chat(request):
    """Ask the configured LLM a question about a document, using RAG over its content."""
    data = _json_body(request)
    message = data.get("message")
    if not data.get("document_id") or not message:
        raise _BadRequest("document_id and message required")
    if not isinstance(message, str) or len(message) > MAX_MESSAGE_CHARS:
        raise _BadRequest(f"message must be text of at most {MAX_MESSAGE_CHARS} characters")

    document = _get_visible_document(request, data["document_id"])
    response_text = "".join(stream_chat_with_documents(message, [document]))

    return JsonResponse({"success": True, "response": response_text})


@ai_endpoint
def ai_classify(request):
    """Classify a document with AI, optionally applying the suggested tags/correspondent/type."""
    data = _json_body(request)
    if not data.get("document_id"):
        raise _BadRequest("document_id required")

    apply = data.get("apply", False) is True
    document = _get_visible_document(request, data["document_id"])

    # Applying changes the document, so being allowed to see it is not enough.
    if apply and not (
        request.user.has_perm("documents.change_document")
        and has_perms_owner_aware(request.user, "change_document", document)
    ):
        return JsonResponse({"error": "Insufficient permissions"}, status=403)

    classification = get_ai_document_classification(document, request.user)

    if apply:
        if classification.get("title"):
            document.title = classification["title"]

        tags = match_tags_by_name(
            classification.get("tags", []),
            request.user,
        )
        if tags:
            document.tags.add(*tags)

        correspondents = match_correspondents_by_name(
            classification.get("correspondents", []),
            request.user,
        )
        if correspondents:
            document.correspondent = correspondents[0]

        document_types = match_document_types_by_name(
            classification.get("document_types", []),
            request.user,
        )
        if document_types:
            document.document_type = document_types[0]

        document.save()

    return JsonResponse({"success": True, "classification": classification})
