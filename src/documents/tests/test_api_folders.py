import uuid
from unittest import mock

import celery
from django.contrib.auth.models import Permission
from django.contrib.auth.models import User
from django.test import TestCase
from django.test import override_settings
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase

from documents.generic_parser import GenericDocumentParser
from documents.models import Document
from documents.models import Folder
from documents.tests.utils import DirectoriesMixin
from documents.tests.utils import DocumentConsumeDelayMixin

URL = "/api/folders/"


def make_document(title, **kwargs):
    return Document.objects.create(
        title=title,
        checksum=uuid.uuid4().hex,
        mime_type="application/pdf",
        **kwargs,
    )


class TestFolderApi(DirectoriesMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user("alice")
        self.user.user_permissions.add(
            *Permission.objects.filter(
                codename__in=["view_document", "add_document", "change_document"],
            ),
        )
        self.other = User.objects.create_user("bob")
        self.client.force_authenticate(self.user)

        self.root = Folder.objects.create(name="Project", owner=self.user)
        self.plans = Folder.objects.create(
            name="plans",
            parent=self.root,
            owner=self.user,
        )
        self.old = Folder.objects.create(name="old", parent=self.plans, owner=self.user)
        self.secret = Folder.objects.create(name="Secret", owner=self.other)

    def test_lists_visible_folders_with_counts(self):
        make_document("a", folder=self.plans, owner=self.user)
        make_document("b", folder=self.plans, owner=self.user)
        make_document("hidden", folder=self.plans, owner=self.other)

        response = self.client.get(URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        by_name = {f["name"]: f for f in response.data}
        self.assertEqual(set(by_name), {"Project", "plans", "old"})
        self.assertEqual(by_name["plans"]["parent"], self.root.pk)
        # Only documents the user may see are counted.
        self.assertEqual(by_name["plans"]["document_count"], 2)
        self.assertTrue(by_name["plans"]["user_can_change"])

    def test_someone_elses_folder_shows_when_it_holds_a_visible_document(self):
        inner = Folder.objects.create(name="inner", parent=self.secret, owner=self.other)
        make_document("shared", folder=inner, owner=None)

        names = {f["name"]: f for f in self.client.get(URL).data}
        # The folder and those above it, so the document can be browsed to.
        self.assertIn("inner", names)
        self.assertIn("Secret", names)
        self.assertFalse(names["Secret"]["user_can_change"])

        response = self.client.patch(
            f"{URL}{self.secret.pk}/",
            {"name": "Mine now"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_create_rename_move(self):
        response = self.client.post(
            URL,
            {"name": "  New   folder ", "parent": self.root.pk},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created = Folder.objects.get(pk=response.data["id"])
        self.assertEqual(created.name, "New folder")
        self.assertEqual(created.owner, self.user)

        response = self.client.post(
            URL,
            {"name": "new FOLDER", "parent": self.root.pk},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        response = self.client.post(URL, {"name": "a/b"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        response = self.client.patch(
            f"{URL}{self.plans.pk}/",
            {"parent": None},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.plans.refresh_from_db()
        self.assertIsNone(self.plans.parent)

    def test_cannot_move_into_itself(self):
        response = self.client.patch(
            f"{URL}{self.root.pk}/",
            {"parent": self.old.pk},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_parent_must_be_visible(self):
        response = self.client.post(
            URL,
            {"name": "x", "parent": self.secret.pk},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_depth_limit(self):
        with mock.patch.object(Folder, "MAX_NESTING_DEPTH", 3):
            response = self.client.post(
                URL,
                {"name": "too deep", "parent": self.old.pk},
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_delete_keeps_documents(self):
        document = make_document("kept", folder=self.old, owner=self.user)
        response = self.client.delete(f"{URL}{self.root.pk}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Folder.objects.filter(name__in=["plans", "old"]).exists())
        document.refresh_from_db()
        self.assertIsNone(document.folder)

    def test_filter_documents_by_folder_tree(self):
        top = make_document("top", folder=self.root, owner=self.user)
        deep = make_document("deep", folder=self.old, owner=self.user)
        make_document("elsewhere", owner=self.user)

        response = self.client.get(
            f"/api/documents/?folder__id__tree={self.root.pk}&fields=id",
        )
        self.assertEqual(
            {d["id"] for d in response.data["results"]},
            {top.pk, deep.pk},
        )
        response = self.client.get(
            f"/api/documents/?folder__id={self.root.pk}&fields=id",
        )
        self.assertEqual([d["id"] for d in response.data["results"]], [top.pk])
        response = self.client.get("/api/documents/?folder__id__tree=x")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_document_folder_field_and_bulk_move(self):
        document = make_document("doc", owner=self.user)
        response = self.client.patch(
            f"/api/documents/{document.pk}/",
            {"folder": self.plans.pk},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["folder"], self.plans.pk)

        response = self.client.patch(
            f"/api/documents/{document.pk}/",
            {"folder": self.secret.pk},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        response = self.client.post(
            "/api/documents/bulk_edit/",
            {
                "documents": [document.pk],
                "method": "set_folder",
                "parameters": {"folder": self.old.pk},
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        document.refresh_from_db()
        self.assertEqual(document.folder, self.old)

        response = self.client.post(
            "/api/documents/bulk_edit/",
            {
                "documents": [document.pk],
                "method": "set_folder",
                "parameters": {"folder": None},
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        document.refresh_from_db()
        self.assertIsNone(document.folder)


class TestUploadAnyType(DirectoriesMixin, DocumentConsumeDelayMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_superuser("admin")
        self.client.force_authenticate(self.user)
        self.consume_file_mock.return_value = celery.result.AsyncResult(
            id=str(uuid.uuid4()),
        )

    def upload(self, name: str, data: bytes, **extra):
        path = self.dirs.scratch_dir / name
        path.write_bytes(data)
        with path.open("rb") as f:
            return self.client.post(
                "/api/documents/post_document/",
                {"document": f, **extra},
            )

    def test_unknown_type_is_accepted(self):
        response = self.upload("drawing.dwg", b"AC1032\x00\x01\x02binary")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    @override_settings(CONSUMER_STORE_UNSUPPORTED=False)
    def test_unknown_type_refused_when_disabled(self):
        response = self.upload("drawing.dwg", b"AC1032\x00\x01\x02binary")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_upload_into_a_folder(self):
        folder = Folder.objects.create(name="Inbox", owner=self.user)
        response = self.upload("note.txt", b"hello", folder=folder.pk)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        _, overrides = self.get_last_consume_delay_call_args()
        self.assertEqual(overrides.folder_id, folder.pk)


class TestGenericParser(DirectoriesMixin, TestCase):
    def test_thumbnail_and_no_text(self):
        source = self.dirs.scratch_dir / "drawing.dwg"
        source.write_bytes(b"AC1032" + b"\0" * 2048)
        parser = GenericDocumentParser(None)
        try:
            parser.parse(source, "application/octet-stream", "drawing.dwg")
            self.assertEqual(parser.get_text(), "")
            thumb = parser.get_thumbnail(source, "application/octet-stream", "drawing.dwg")
            with Image.open(thumb) as image:
                self.assertEqual(image.size, (500, 700))
        finally:
            parser.cleanup()

    def test_file_type_keeps_the_original_extension(self):
        document = Document(
            title="drawing",
            mime_type="application/octet-stream",
            original_filename="Site Plan.DWG",
        )
        self.assertEqual(document.file_type, ".dwg")
        pdf = Document(mime_type="application/pdf", original_filename="x.weird")
        self.assertEqual(pdf.file_type, ".pdf")
