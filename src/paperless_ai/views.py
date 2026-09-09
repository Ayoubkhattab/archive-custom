import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from documents.models import Document
from documents.permissions import get_objects_for_user_owner_aware
from paperless.config import AIConfig
from paperless_ai.ai_classifier import get_ai_document_classification
from paperless_ai.chat import stream_chat_with_documents
from paperless_ai.matching import match_correspondents_by_name
from paperless_ai.matching import match_document_types_by_name
from paperless_ai.matching import match_tags_by_name


def _get_visible_document(request, document_id):
    return get_objects_for_user_owner_aware(
        request.user,
        "documents.view_document",
        Document,
    ).get(id=document_id)


@csrf_exempt
@require_http_methods(["POST"])
@login_required
def ai_suggest(request):
    """AI-generated suggestions (tags/correspondent/document type/title) for a document."""
    ai_config = AIConfig()
    if not ai_config.ai_enabled:
        return JsonResponse({"error": "AI is not enabled"}, status=400)

    try:
        data = json.loads(request.body)
        document_id = data.get("document_id")

        if not document_id:
            return JsonResponse({"error": "document_id required"}, status=400)

        document = _get_visible_document(request, document_id)

        suggestions = get_ai_document_classification(document, request.user)

        return JsonResponse({"success": True, "suggestions": suggestions})

    except Document.DoesNotExist:
        return JsonResponse({"error": "Document not found"}, status=404)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
@login_required
def ai_chat(request):
    """Ask the configured LLM a question about a document, using RAG over its content."""
    ai_config = AIConfig()
    if not ai_config.ai_enabled:
        return JsonResponse({"error": "AI is not enabled"}, status=400)

    try:
        data = json.loads(request.body)
        document_id = data.get("document_id")
        message = data.get("message")

        if not document_id or not message:
            return JsonResponse(
                {"error": "document_id and message required"},
                status=400,
            )

        document = _get_visible_document(request, document_id)

        response_text = "".join(
            stream_chat_with_documents(message, [document]),
        )

        return JsonResponse({"success": True, "response": response_text})

    except Document.DoesNotExist:
        return JsonResponse({"error": "Document not found"}, status=404)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
@login_required
def ai_classify(request):
    """Classify a document with AI, optionally applying the suggested tags/correspondent/type."""
    ai_config = AIConfig()
    if not ai_config.ai_enabled:
        return JsonResponse({"error": "AI is not enabled"}, status=400)

    try:
        data = json.loads(request.body)
        document_id = data.get("document_id")

        if not document_id:
            return JsonResponse({"error": "document_id required"}, status=400)

        document = _get_visible_document(request, document_id)

        classification = get_ai_document_classification(document, request.user)

        if data.get("apply", False):
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

    except Document.DoesNotExist:
        return JsonResponse({"error": "Document not found"}, status=404)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
