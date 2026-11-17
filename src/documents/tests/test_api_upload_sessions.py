import os
import time
import uuid
from pathlib import Path

import celery
from django.conf import settings
from django.contrib.auth.models import User
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from documents import upload_sessions
from documents.data_models import DocumentSource
from documents.tests.utils import DirectoriesMixin
from documents.tests.utils import DocumentConsumeDelayMixin

SAMPLE = Path(__file__).parent / "samples" / "simple.pdf"
URL = "/api/documents/upload_sessions/"


@override_settings(UPLOAD_CHUNK_SIZE=1000, UPLOAD_MIN_FREE_SPACE=0)
class TestUploadSessions(DirectoriesMixin, DocumentConsumeDelayMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="uploader")
        self.client.force_authenticate(user=self.user)
        self.data = SAMPLE.read_bytes()
        self.consume_file_mock.return_value = celery.result.AsyncResult(
            id=str(uuid.uuid4()),
        )

    def create(self, **kwargs):
        body = {
            "filename": "simple.pdf",
            "size": len(self.data),
            "fingerprint": "simple.pdf:1234:1700000000",
            **kwargs,
        }
        return self.client.post(URL, body, format="json")

    def put_chunk(self, session, index, data=None):
        size = session["chunk_size"]
        if data is None:
            data = self.data[index * size : (index + 1) * size]
        return self.client.put(
            f"{URL}{session['id']}/chunks/{index}/",
            data=data,
            content_type="application/octet-stream",
        )

    def complete(self, session, **metadata):
        return self.client.post(
            f"{URL}{session['id']}/complete/",
            {"from_webui": True, **metadata},
            format="json",
        )

    def test_chunked_upload(self):
        """
        GIVEN:
            - A file sent as several chunks, out of order
        WHEN:
            - The upload is completed
        THEN:
            - The consumer gets the file, byte for byte, with the metadata
        """
        session = self.create().data
        self.assertGreater(session["total_chunks"], 1)
        self.assertEqual(session["received"], [])

        self.assertEqual(self.put_chunk(session, 0).status_code, status.HTTP_200_OK)
        for index in reversed(range(1, session["total_chunks"])):
            response = self.put_chunk(session, index)
            self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.complete(session, title="Big file")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        input_doc, overrides = self.get_last_consume_delay_call_args()
        self.assertEqual(input_doc.original_file.name, "simple.pdf")
        self.assertIn(Path(settings.SCRATCH_DIR), input_doc.original_file.parents)
        self.assertEqual(input_doc.original_file.read_bytes(), self.data)
        self.assertEqual(input_doc.source, DocumentSource.WebUI)
        self.assertEqual(overrides.title, "Big file")
        self.assertEqual(overrides.owner_id, self.user.id)

    def test_complete_is_idempotent(self):
        """A retried "complete" whose first response was lost gets the same task."""
        session = self.create().data
        for index in range(session["total_chunks"]):
            self.put_chunk(session, index)

        first = self.complete(session)
        second = self.complete(session)

        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(first.data, second.data)
        self.consume_file_mock.assert_called_once()

    def test_resume(self):
        """
        GIVEN:
            - An upload that stopped after some chunks
        WHEN:
            - The same file is selected again
        THEN:
            - The same session comes back with the chunks already received
        """
        session = self.create().data
        self.put_chunk(session, 0)
        self.put_chunk(session, 2)

        resumed = self.create().data

        self.assertEqual(resumed["id"], session["id"])
        self.assertEqual(resumed["received"], [0, 2])

    def test_new_upload_after_completion(self):
        session = self.create().data
        for index in range(session["total_chunks"]):
            self.put_chunk(session, index)
        self.complete(session)

        again = self.create().data

        self.assertEqual(again["received"], [])
        self.assertIsNone(again["task_id"])

    def test_different_file_gets_different_session(self):
        first = self.create().data
        second = self.create(fingerprint="simple.pdf:1234:1800000000").data
        self.assertNotEqual(first["id"], second["id"])

    def test_unsupported_type_refused_on_first_chunk(self):
        self.data = bytes(range(256)) * 16
        session = self.create(filename="blob.bin").data

        response = self.put_chunk(session, 0)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("not supported", response.data["document"][0])
        # The session is gone, so nothing more can be sent to it.
        self.assertEqual(
            self.put_chunk(session, 1).status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_short_chunk_is_not_marked_received(self):
        session = self.create().data

        response = self.put_chunk(session, 1, data=b"too short")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.client.get(f"{URL}{session['id']}/").data["received"], [])

    def test_oversized_chunk_does_not_overwrite_next_chunk(self):
        session = self.create().data
        self.put_chunk(session, 2)

        response = self.put_chunk(session, 1, data=b"x" * 5000)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        data = (
            upload_sessions.get_session(session["id"], self.user.id)
            .data_path.read_bytes()
        )
        self.assertEqual(data[2000:3000], self.data[2000:3000])

    def test_complete_with_missing_chunks(self):
        session = self.create().data
        self.put_chunk(session, 0)

        response = self.complete(session)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.consume_file_mock.assert_not_called()

    def test_chunk_out_of_range(self):
        session = self.create().data
        response = self.put_chunk(session, session["total_chunks"], data=b"x")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_other_users_cannot_touch_a_session(self):
        session = self.create().data
        self.client.force_authenticate(User.objects.create_user(username="other"))

        self.assertEqual(
            self.client.get(f"{URL}{session['id']}/").status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(
            self.put_chunk(session, 0).status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_delete_session(self):
        session = self.create().data

        response = self.client.delete(f"{URL}{session['id']}/")

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse((upload_sessions.sessions_root() / session["id"]).exists())

    @override_settings(UPLOAD_MAX_SIZE=1000)
    def test_file_too_large(self):
        response = self.create()
        self.assertEqual(response.status_code, status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

    @override_settings(UPLOAD_MIN_FREE_SPACE=10**18)
    def test_not_enough_disk_space(self):
        response = self.create()
        self.assertEqual(response.status_code, status.HTTP_507_INSUFFICIENT_STORAGE)

    @override_settings(UPLOAD_SESSION_EXPIRY_HOURS=1)
    def test_expired_sessions_are_removed(self):
        stale = self.create().data
        stale_dir = upload_sessions.sessions_root() / stale["id"]
        two_hours_ago = time.time() - 7200
        os.utime(stale_dir / "meta.json", (two_hours_ago, two_hours_ago))

        self.create(fingerprint="another file")

        self.assertFalse(stale_dir.exists())

    def test_requires_authentication(self):
        self.client.force_authenticate(user=None)
        self.assertIn(
            self.create().status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )
