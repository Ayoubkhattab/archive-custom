import shutil
from pathlib import Path

from rest_framework import status
from rest_framework.test import APITestCase

from documents.models import Document
from documents.tests.utils import DirectoriesMixin
from paperless.access import constants as C
from paperless.tests.test_access_policy import child_of
from paperless.tests.test_access_policy import grant

SAMPLE = Path(__file__).parents[2] / "documents" / "tests" / "samples" / "simple.pdf"


class TestPdfStreaming(DirectoriesMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.father = child_of(None, "father")
        self.son = child_of(self.father, "son")
        self.stranger = child_of(None, "stranger")
        self.doc = Document.objects.create(
            title="contract",
            mime_type="application/pdf",
            checksum="abc123",
            filename="0000001.pdf",
            owner=self.son,
        )
        Path(self.doc.source_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(SAMPLE, self.doc.source_path)
        self.size = SAMPLE.stat().st_size
        self.url = f"/api/pdfs/{self.doc.pk}/file/"

    def body(self, response) -> bytes:
        return (
            b"".join(response.streaming_content)
            if response.streaming
            else response.content
        )

    def test_requires_authentication(self):
        self.assertEqual(
            self.client.get(self.url).status_code, status.HTTP_401_UNAUTHORIZED,
        )

    def test_owner_gets_inline_pdf_with_caching_headers(self):
        self.client.force_authenticate(self.son)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response["Content-Disposition"].startswith("inline;"))
        self.assertEqual(response["Accept-Ranges"], "bytes")
        self.assertEqual(response["Content-Length"], str(self.size))
        self.assertEqual(self.body(response), SAMPLE.read_bytes())
        self.assertEqual(
            self.client.get(self.url, HTTP_IF_NONE_MATCH=response["ETag"]).status_code,
            304,
        )

    def test_range_requests(self):
        self.client.force_authenticate(self.son)
        response = self.client.get(self.url, HTTP_RANGE="bytes=0-99")
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response["Content-Range"], f"bytes 0-99/{self.size}")
        self.assertEqual(response["Content-Length"], "100")
        self.assertEqual(self.body(response), SAMPLE.read_bytes()[:100])

        response = self.client.get(self.url, HTTP_RANGE="bytes=-10")
        self.assertEqual(response.status_code, 206)
        self.assertEqual(self.body(response), SAMPLE.read_bytes()[-10:])

        response = self.client.get(self.url, HTTP_RANGE=f"bytes={self.size}-")
        self.assertEqual(response.status_code, 416)
        self.assertEqual(response["Content-Range"], f"bytes */{self.size}")

        # malformed / multi-range requests get the whole file
        response = self.client.get(self.url, HTTP_RANGE="bytes=0-1,5-6")
        self.assertEqual(response.status_code, 200)

        # stale If-Range -> full file
        response = self.client.get(
            self.url, HTTP_RANGE="bytes=0-9", HTTP_IF_RANGE='"other"',
        )
        self.assertEqual(response.status_code, 200)

    def test_unrelated_user_gets_404_not_403(self):
        self.client.force_authenticate(self.stranger)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.get("/api/pdfs/999999/file/").status_code, 404)

    def test_father_needs_pdf_view_child(self):
        self.client.force_authenticate(self.father)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        father = grant(self.father, C.PDF_VIEW_CHILD)
        self.client.force_authenticate(father)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_son_cannot_view_fathers_pdf_without_sharing(self):
        self.doc.owner = self.father
        self.doc.save()
        self.client.force_authenticate(self.son)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_missing_file_is_a_clean_404(self):
        Path(self.doc.source_path).unlink()
        self.client.force_authenticate(self.son)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["code"], "no_pdf")

    def test_existing_preview_endpoint_supports_ranges(self):
        # the legacy documents API also needs the model permission
        self.client.force_authenticate(grant(self.son, "documents.view_document"))
        response = self.client.get(
            f"/api/documents/{self.doc.pk}/preview/", HTTP_RANGE="bytes=0-9",
        )
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response["Content-Range"], f"bytes 0-9/{self.size}")
        full = self.client.get(f"/api/documents/{self.doc.pk}/preview/")
        self.assertEqual(full.status_code, 200)
        self.assertEqual(full["Accept-Ranges"], "bytes")
        self.assertEqual(full.content, SAMPLE.read_bytes())
