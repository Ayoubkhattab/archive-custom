"""
GET /api/pdfs/{id}/file/   (?original=true for the original PDF)

* 401 unauthenticated, 404 when the document does not exist, is in the
  trash, is not visible to the caller (no existence leak), or has no PDF
* 200 / 206 / 304 / 416 with Accept-Ranges, Content-Range, ETag,
  Last-Modified, Content-Type: application/pdf, Content-Disposition: inline
"""

from __future__ import annotations

from django.http import Http404
from django.http import HttpResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from documents.models import Document
from paperless.access.drf import PermissionScopedMixin
from paperless.access.exceptions import ApiError
from paperless.pdfs.service import can_view_pdf
from paperless.pdfs.service import content_disposition
from paperless.pdfs.service import get_pdf_file
from paperless.streaming import ranged_file_response


class PdfFileView(PermissionScopedMixin, APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request, pk: int):
        try:
            document = Document.objects.select_related("owner").get(pk=pk)
        except (Document.DoesNotExist, ValueError):
            raise Http404
        if not can_view_pdf(request.user, document):
            raise Http404
        pdf = get_pdf_file(
            document,
            original=request.query_params.get("original") == "true",
        )
        if pdf is None:
            raise ApiError(404, "no_pdf", "This document has no PDF version.")
        disposition = content_disposition("inline", pdf.filename)

        if pdf.encrypted_storage:  # legacy GPG storage: decrypt in memory, no ranges
            from paperless.db import GnuPG

            with pdf.path.open("rb") as fh:
                data = GnuPG.decrypted(fh)
            response = HttpResponse(data, content_type="application/pdf")
            response["Content-Disposition"] = disposition
            response["Cache-Control"] = "private, no-cache"
            return response

        return ranged_file_response(
            request,
            open_file=lambda: pdf.path.open("rb"),
            size=pdf.size,
            content_type="application/pdf",
            etag=pdf.checksum,
            last_modified=document.modified,
            disposition=disposition,
            cache_control="private, no-cache",
        )
