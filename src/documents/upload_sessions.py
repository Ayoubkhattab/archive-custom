"""
Resumable chunked uploads.

A large file is sent as fixed-size chunks that may arrive in any order, in
parallel and more than once. Each session lives in its own directory under
SCRATCH_DIR (shared by the webserver and the consumer):

    upload_sessions/<id>/
        meta.json      who uploads what (filename, size, chunk size)
        data.bin       the file, preallocated; chunks are written in place
        received/<n>   one empty marker per chunk fully written
        completed.json task id, once the file was handed to the consumer

The session id is derived from the user and the file's fingerprint (name,
size, modification time), so selecting the same file again after a dropped
connection, a closed tab or a server restart finds the same session and only
the missing chunks are sent.

Plain files, no database: chunk requests are spread over several webserver
processes, and a rename or a marker file is atomic for all of them.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import time
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from django.conf import settings

if TYPE_CHECKING:
    from typing import BinaryIO

logger = logging.getLogger("paperless.upload")

MIN_CHUNK_SIZE = 1024 * 1024
# How much of the file libmagic looks at (its default bytes_max is 7 MiB).
MAGIC_HEAD_BYTES = 8 * 1024 * 1024
_COPY_BUFFER = 1024 * 1024
_SESSION_ID = re.compile(r"^[0-9a-f]{32}$")


class UploadError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass
class UploadSession:
    id: str
    user_id: int
    filename: str
    size: int
    chunk_size: int
    created: float

    @property
    def total_chunks(self) -> int:
        return max(1, -(-self.size // self.chunk_size))

    def chunk_length(self, index: int) -> int:
        if index == self.total_chunks - 1:
            return self.size - index * self.chunk_size
        return self.chunk_size

    @property
    def dir(self) -> Path:
        return sessions_root() / self.id

    @property
    def data_path(self) -> Path:
        return self.dir / "data.bin"

    def received(self) -> list[int]:
        try:
            return sorted(int(p.name) for p in (self.dir / "received").iterdir())
        except FileNotFoundError:
            return []

    def completed_task_id(self) -> str | None:
        try:
            return json.loads((self.dir / "completed.json").read_text())["task_id"]
        except (FileNotFoundError, ValueError, KeyError):
            return None

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "filename": self.filename,
            "size": self.size,
            "chunk_size": self.chunk_size,
            "total_chunks": self.total_chunks,
            "received": self.received(),
            "task_id": self.completed_task_id(),
        }


def sessions_root() -> Path:
    return settings.SCRATCH_DIR / "upload_sessions"


def _session_id(user_id: int, filename: str, size: int, fingerprint: str) -> str:
    key = f"{user_id}\0{filename}\0{size}\0{fingerprint}".encode()
    return hashlib.sha256(key).hexdigest()[:32]


def get_session(session_id: str, user_id: int) -> UploadSession:
    if not _SESSION_ID.match(session_id):
        raise UploadError("Upload session not found.", 404)
    try:
        meta = json.loads((sessions_root() / session_id / "meta.json").read_text())
    except (FileNotFoundError, ValueError):
        raise UploadError("Upload session not found.", 404)
    session = UploadSession(**meta)
    if session.user_id != user_id:
        raise UploadError("Upload session not found.", 404)
    return session


def remove_expired_sessions() -> None:
    """Abandoned uploads can be gigabytes: drop them after the expiry."""
    root = sessions_root()
    if not root.is_dir():
        return
    cutoff = time.time() - settings.UPLOAD_SESSION_EXPIRY_HOURS * 3600
    for session_dir in root.iterdir():
        try:
            # meta.json is touched on every chunk, so this is "last activity".
            if (session_dir / "meta.json").stat().st_mtime < cutoff:
                shutil.rmtree(session_dir, ignore_errors=True)
                logger.info(f"Removed expired upload session {session_dir.name}")
        except FileNotFoundError:
            shutil.rmtree(session_dir, ignore_errors=True)


def create_or_resume(
    user_id: int,
    filename: str,
    size: int,
    fingerprint: str,
    requested_chunk_size: int | None,
) -> UploadSession:
    if size <= 0:
        raise UploadError("The file is empty.")
    if settings.UPLOAD_MAX_SIZE and size > settings.UPLOAD_MAX_SIZE:
        raise UploadError(
            f"The file is larger than the {settings.UPLOAD_MAX_SIZE / 1024**3:g} GB "
            f"upload limit.",
            413,
        )

    remove_expired_sessions()

    session_id = _session_id(user_id, filename, size, fingerprint)
    session_dir = sessions_root() / session_id

    if (session_dir / "meta.json").is_file():
        session = get_session(session_id, user_id)
        if session.completed_task_id() is None:
            return session  # resume
        # The same file was uploaded before: this is a new upload of it.
        shutil.rmtree(session_dir, ignore_errors=True)

    chunk_size = settings.UPLOAD_CHUNK_SIZE
    if requested_chunk_size:
        chunk_size = max(MIN_CHUNK_SIZE, min(requested_chunk_size, chunk_size))

    sessions_root().mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(sessions_root()).free
    if free < size + settings.UPLOAD_MIN_FREE_SPACE:
        raise UploadError(
            "Not enough free space on the server for this file.",
            507,
        )

    session = UploadSession(
        id=session_id,
        user_id=user_id,
        filename=filename,
        size=size,
        chunk_size=chunk_size,
        created=time.time(),
    )
    (session_dir / "received").mkdir(parents=True, exist_ok=True)
    # Sparse: takes no space until chunks are written into it. O_EXCL so a
    # second tab creating the same session can't truncate chunks already
    # written by the first.
    try:
        fd = os.open(session.data_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        pass
    else:
        try:
            os.ftruncate(fd, size)
        finally:
            os.close(fd)
    tmp = session_dir / f"meta.json.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(asdict(session)))
    tmp.replace(session_dir / "meta.json")
    return session


def write_chunk(
    session: UploadSession,
    index: int,
    stream: BinaryIO | None,
    validate_head=None,
) -> None:
    """
    Writes chunk `index` from `stream` into place. `validate_head(bytes)` is
    called with the start of the file before chunk 0 is written, so an
    unsupported file is refused before gigabytes of it are sent.
    """
    if not 0 <= index < session.total_chunks:
        raise UploadError(f"Chunk {index} is out of range.")
    if session.completed_task_id() is not None:
        raise UploadError("This upload is already complete.", 409)

    expected = session.chunk_length(index)
    read = stream.read if stream is not None else lambda _size: b""
    written = 0
    try:
        with session.data_path.open("r+b") as f:
            f.seek(index * session.chunk_size)
            if index == 0 and validate_head is not None:
                head = b""
                while len(head) < min(MAGIC_HEAD_BYTES, expected):
                    more = read(_COPY_BUFFER)
                    if not more:
                        break
                    head += more
                validate_head(head)
                written = len(head)
                if written <= expected:
                    f.write(head)
            while written <= expected:
                buffer = read(_COPY_BUFFER)
                if not buffer:
                    break
                written += len(buffer)
                # Never write past the chunk: that is the next chunk's data.
                if written <= expected:
                    f.write(buffer)
    except FileNotFoundError:
        raise UploadError("Upload session not found.", 404)

    if written != expected:
        raise UploadError(
            f"Chunk {index} has {written} bytes, expected {expected}.",
        )

    (session.dir / "received" / str(index)).touch()
    os.utime(session.dir / "meta.json")


def missing_chunks(session: UploadSession) -> int:
    return session.total_chunks - len(session.received())


def claim_file(session: UploadSession, destination: Path) -> None:
    """
    Moves the assembled file out of the session. A rename on the same
    volume: instant even for 20 GB, and atomic, so of two concurrent
    "complete" calls only one gets the file.
    """
    try:
        session.data_path.rename(destination)
    except (FileNotFoundError, PermissionError):
        raise UploadError("This upload is already being completed.", 409)


def mark_completed(session: UploadSession, task_id: str) -> None:
    """
    Keeps a small record so a retried "complete" (its first response was
    lost) gets the same task back instead of an error.
    """
    (session.dir / "completed.json").write_text(json.dumps({"task_id": task_id}))
    shutil.rmtree(session.dir / "received", ignore_errors=True)


def delete_session(session: UploadSession) -> None:
    shutil.rmtree(session.dir, ignore_errors=True)
