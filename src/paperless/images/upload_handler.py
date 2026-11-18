"""
Streaming upload limits enforced while the multipart body is being read:
the request is cut off as soon as the file exceeds the limit or a second
file starts, instead of buffering the whole body first.
"""

from __future__ import annotations

from django.conf import settings
from django.core.files.uploadhandler import StopUpload
from django.core.files.uploadhandler import TemporaryFileUploadHandler


class LimitedTemporaryFileUploadHandler(TemporaryFileUploadHandler):
    def __init__(self, request=None):
        super().__init__(request)
        self.max_bytes = settings.IMAGES_MAX_UPLOAD_BYTES
        self.max_files = settings.IMAGES_MAX_FILES_PER_REQUEST
        self.files_seen = 0
        self.received = 0

    def handle_raw_input(
        self,
        input_data,
        META,
        content_length,
        boundary,
        encoding=None,
    ):
        # Reject obviously oversized bodies before reading anything
        # (64 KiB of slack for multipart framing and form fields).
        # StopUpload must not be raised here: this hook runs outside the
        # parser's error handling, so the first file part stops instead.
        if (
            content_length
            and content_length > self.max_bytes * self.max_files + 64 * 1024
        ):
            self.request.image_upload_error = "too_large"
        return None

    def new_file(self, *args, **kwargs):
        if getattr(self.request, "image_upload_error", None):
            raise StopUpload(connection_reset=False)
        self.files_seen += 1
        if self.files_seen > self.max_files:
            self.request.image_upload_error = "too_many_files"
            raise StopUpload(connection_reset=False)
        self.received = 0
        return super().new_file(*args, **kwargs)

    def receive_data_chunk(self, raw_data, start):
        self.received += len(raw_data)
        if self.received > self.max_bytes:
            self.request.image_upload_error = "too_large"
            self.file.close()
            raise StopUpload(connection_reset=False)
        return super().receive_data_chunk(raw_data, start)
