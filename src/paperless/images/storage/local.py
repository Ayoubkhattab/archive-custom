from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import BinaryIO

from django.conf import settings

from paperless.images.storage.base import InvalidStorageKey
from paperless.images.storage.base import StorageProvider
from paperless.images.storage.base import validate_key


class LocalStorageProvider(StorageProvider):
    """Files below a private directory that no URL maps to."""

    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        validate_key(key)
        path = (self.root / key).resolve()
        # Belt and braces against traversal, including through symlinks.
        if not path.is_relative_to(self.root):
            raise InvalidStorageKey("Invalid storage key")
        return path

    def save(self, key: str, fileobj: BinaryIO, content_type: str) -> int:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        # Write to a temp file in the same directory, then rename: readers
        # never see a half-written file.
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".upload-")
        try:
            with os.fdopen(fd, "wb") as out:
                shutil.copyfileobj(fileobj, out, length=1024 * 1024)
            Path(tmp).chmod(0o640)
            Path(tmp).replace(path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return path.stat().st_size

    def open(self, key: str) -> BinaryIO:
        return self._path(key).open("rb")

    def delete(self, key: str) -> None:
        path = self._path(key)
        path.unlink(missing_ok=True)
        # Remove now-empty per-image directories.
        parent = path.parent
        while parent != self.root and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def size(self, key: str) -> int:
        return self._path(key).stat().st_size

    def x_accel_path(self, key: str) -> str | None:
        prefix = getattr(settings, "IMAGES_X_ACCEL_PREFIX", "")
        if not prefix:
            return None
        return prefix.rstrip("/") + "/" + validate_key(key)
