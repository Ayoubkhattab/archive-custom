from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
import json
from documents.models import Document


@csrf_exempt
@require_http_methods(["POST"])
@login_required
def ai_suggest(request):
    """AI suggestions for document classification"""
    try:
        data = json.loads(request.body)
        document_id = data.get("document_id")
        
        if not document_id:
            return JsonResponse({"error": "document_id required"}, status=400)
            
        document = Document.objects.get(id=document_id)
        
        # Simple AI simulation for now
        suggestions = {
            "title": f"AI Suggested: {document.title}",
            "correspondent": "AI Detected Correspondent",
            "document_type": "AI Detected Type",
            "tags": ["AI", "Suggested", "Tag"],
            "content_analysis": f"Document contains {len(document.content)} characters"
        }
        
        return JsonResponse({"success": True, "suggestions": suggestions})
        
    except Document.DoesNotExist:
        return JsonResponse({"error": "Document not found"}, status=404)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
@login_required
def ai_chat(request):
    """Chat with AI about a document"""
    try:
        data = json.loads(request.body)
        document_id = data.get("document_id")
        message = data.get("message")
        
        if not document_id or not message:
            return JsonResponse({"error": "document_id and message required"}, status=400)
            
        document = Document.objects.get(id=document_id)
        
        # Simple AI simulation for now
        context = f"Document: {document.title}\n\nContent:\n{document.content[:500]}"
        
        # Simulate AI response
        response = f"Based on the document '{document.title}', I can see it contains information about: {message}. The document has {len(document.content)} characters and was created on {document.created}."
        
        return JsonResponse({"success": True, "response": response})
        
    except Document.DoesNotExist:
        return JsonResponse({"error": "Document not found"}, status=404)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
@login_required
def ai_classify(request):
    """Classify document with AI"""
    try:
        data = json.loads(request.body)
        document_id = data.get("document_id")
        
        if not document_id:
            return JsonResponse({"error": "document_id required"}, status=400)
            
        document = Document.objects.get(id=document_id)
        
        # Simple AI classification simulation
        classification = {
            "title": f"AI Classified: {document.title}",
            "correspondent": "AI Classified Correspondent",
            "document_type": "AI Classified Type",
            "tags": ["AI", "Classified", "Auto"],
            "confidence": 0.85
        }
        
        # Apply classification if requested
        if data.get("apply", False):
            if classification.get("title"):
                document.title = classification["title"]
            document.save()
        
        return JsonResponse({"success": True, "classification": classification})
        
    except Document.DoesNotExist:
        return JsonResponse({"error": "Document not found"}, status=404)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
