import json
import logging
from functools import wraps

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import JsonResponse
from django.http import StreamingHttpResponse
from django.views.decorators.http import require_http_methods

from documents.models import Document
from documents.permissions import get_objects_for_user_owner_aware
from documents.permissions import has_perms_owner_aware
from paperless.config import AIConfig
from paperless_ai.ai_classifier import get_ai_document_classification
from paperless_ai.chat import stream_chat_with_documents
from paperless_ai.client import DEFAULT_OLLAMA_ENDPOINT
from paperless_ai.client import DEFAULT_OLLAMA_MODEL
from paperless_ai.llm_errors import describe_llm_error
from paperless_ai.matching import match_correspondents_by_name
from paperless_ai.matching import match_document_types_by_name
from paperless_ai.matching import match_tags_by_name
from paperless_ai.ratelimit import allow_request
from paperless_ai.streaming import STREAM_HEARTBEAT
from paperless_ai.streaming import stream_from_sync

logger = logging.getLogger("paperless_ai.views")

MAX_MESSAGE_CHARS = 4000

# A classification is kept until the document changes (its modification time is
# part of the key) or a day passes, so asking again, or applying what was just
# suggested, doesn't make the model read the document a second time.
CLASSIFICATION_CACHE_SECONDS = 24 * 60 * 60


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


def _classification_cache_key(document, user) -> str:
    config = AIConfig()
    modified = document.modified.timestamp() if document.modified else 0
    # Per user: with retrieval enabled the prompt includes similar documents,
    # and which of those a user may see differs.
    return (
        f"paperless_ai.classification:{document.pk}:{modified}:"
        f"{user.pk}:{config.llm_backend}:{config.llm_model}"
    )


def _classify(document, user) -> tuple[dict, bool]:
    """The classification and whether it came from the cache."""
    key = _classification_cache_key(document, user)
    cached = cache.get(key)
    if cached is not None:
        return cached, True
    classification = get_ai_document_classification(document, user)
    cache.set(key, classification, CLASSIFICATION_CACHE_SECONDS)
    return classification, False


def _stream_result(compute) -> StreamingHttpResponse:
    """
    Run a slow model call without letting the connection go quiet.

    A classification is one blocking model call, which on a CPU easily takes
    longer than the 100-120 seconds a proxy such as Cloudflare waits for a first
    byte before answering 524. So the response starts at once, a zero-width
    space is sent every few seconds while the model works, and the JSON result
    follows at the end. The client strips the heartbeats before parsing.

    Errors inside the model call can no longer change the status code once the
    response has started, so they are reported in the JSON as
    {"success": false, "error": ...}.
    """
    ai_config = AIConfig()

    def produce():
        try:
            payload = compute()
        except Exception as exc:
            logger.exception("AI request failed")
            payload = {
                "success": False,
                "error": describe_llm_error(
                    exc,
                    endpoint=ai_config.llm_endpoint or DEFAULT_OLLAMA_ENDPOINT,
                    model=ai_config.llm_model or DEFAULT_OLLAMA_MODEL,
                    timeout=settings.LLM_REQUEST_TIMEOUT,
                ),
            }
        yield json.dumps(payload, ensure_ascii=False)

    response = StreamingHttpResponse(
        stream_from_sync(produce, heartbeat=STREAM_HEARTBEAT),
        content_type="text/plain; charset=utf-8",
    )
    response["Content-Encoding"] = "identity"
    response["X-Accel-Buffering"] = "no"
    return response


@ai_endpoint
def ai_suggest(request):
    """AI-generated suggestions (tags/correspondent/document type/title) for a document."""
    data = _json_body(request)
    if not data.get("document_id"):
        raise _BadRequest("document_id required")

    document = _get_visible_document(request, data["document_id"])
    user = request.user

    def compute():
        suggestions, cached = _classify(document, user)
        return {"success": True, "suggestions": suggestions, "cached": cached}

    return _stream_result(compute)


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

    user = request.user

    def compute():
        classification, cached = _classify(document, user)
        if apply:
            _apply_classification(document, classification, user)
        return {
            "success": True,
            "classification": classification,
            "cached": cached,
            "applied": apply,
        }

    return _stream_result(compute)


def _apply_classification(document, classification: dict, user) -> None:
    """Set what matches existing objects; names that match nothing are skipped."""
    if classification.get("title"):
        document.title = classification["title"]

    tags = match_tags_by_name(classification.get("tags", []), user)
    if tags:
        document.tags.add(*tags)

    correspondents = match_correspondents_by_name(
        classification.get("correspondents", []),
        user,
    )
    if correspondents:
        document.correspondent = correspondents[0]

    document_types = match_document_types_by_name(
        classification.get("document_types", []),
        user,
    )
    if document_types:
        document.document_type = document_types[0]

    document.save()
