"""
S3 / MinIO storage. Uses boto3 (the library django-storages wraps) directly
so ranged reads can be done without downloading whole objects. Install
boto3 to enable it; credentials come from the standard AWS environment
variables or instance role, never from settings files.
"""

from __future__ import annotations

import io
from typing import BinaryIO

from django.core.exceptions import ImproperlyConfigured

from paperless.images.storage.base import StorageProvider
from paperless.images.storage.base import validate_key


class _S3ReadFile(io.RawIOBase):
    """Seekable read-only view of an S3 object using ranged GETs."""

    def __init__(self, client, bucket: str, key: str, size: int):
        self._client, self._bucket, self._key, self._size = client, bucket, key, size
        self._pos = 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def seek(self, offset, whence=io.SEEK_SET):
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._pos, io.SEEK_END: self._size}[whence]
        self._pos = max(0, min(base + offset, self._size))
        return self._pos

    def tell(self):
        return self._pos

    def read(self, n=-1):
        if self._pos >= self._size:
            return b""
        end = (
            self._size - 1 if n is None or n < 0 else min(self._pos + n, self._size) - 1
        )
        obj = self._client.get_object(
            Bucket=self._bucket,
            Key=self._key,
            Range=f"bytes={self._pos}-{end}",
        )
        data = obj["Body"].read()
        self._pos += len(data)
        return data


class S3StorageProvider(StorageProvider):
    def __init__(
        self,
        *,
        bucket: str,
        prefix: str = "",
        endpoint_url=None,
        region_name=None,
    ):
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:  # pragma: no cover - depends on deployment
            raise ImproperlyConfigured(
                "PAPERLESS_IMAGES_STORAGE=s3 requires the boto3 package",
            ) from exc
        if not bucket:
            raise ImproperlyConfigured("PAPERLESS_IMAGES_S3_BUCKET is required")
        self.bucket = bucket
        self.prefix = prefix
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region_name,
            # Explicit timeouts and bounded, jittered retries for every call.
            config=Config(
                connect_timeout=5,
                read_timeout=30,
                retries={"max_attempts": 5, "mode": "adaptive"},
            ),
        )

    def _key(self, key: str) -> str:
        return f"{self.prefix}{validate_key(key)}"

    def save(self, key: str, fileobj: BinaryIO, content_type: str) -> int:
        self.client.upload_fileobj(
            fileobj,
            self.bucket,
            self._key(key),
            ExtraArgs={"ContentType": content_type, "ServerSideEncryption": "AES256"},
        )
        return self.size(key)

    def open(self, key: str) -> BinaryIO:
        return io.BufferedReader(
            _S3ReadFile(self.client, self.bucket, self._key(key), self.size(key)),
            buffer_size=256 * 1024,
        )

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self._key(key))

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=self._key(key))
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in (
                "404",
                "NoSuchKey",
                "NotFound",
            ):
                return False
            raise
        return True

    def size(self, key: str) -> int:
        return self.client.head_object(Bucket=self.bucket, Key=self._key(key))[
            "ContentLength"
        ]
