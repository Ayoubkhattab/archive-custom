"""
Optional ClamAV scan (feature flag PAPERLESS_IMAGES_CLAMAV_ENABLED).

Speaks clamd's INSTREAM protocol over TCP with explicit timeouts, so no
extra dependency is needed. When the flag is on and clamd is unreachable
the upload fails closed (503) instead of skipping the scan.
"""

from __future__ import annotations

import socket
import struct
from typing import TYPE_CHECKING

from django.conf import settings

from paperless.images.processing.image_processor import ImageRejected

if TYPE_CHECKING:
    from pathlib import Path


def scan_file(path: Path) -> None:
    if not settings.IMAGES_CLAMAV_ENABLED:
        return
    try:
        with (
            socket.create_connection(
                (settings.IMAGES_CLAMAV_HOST, settings.IMAGES_CLAMAV_PORT),
                timeout=settings.IMAGES_CLAMAV_TIMEOUT,
            ) as sock,
            path.open("rb") as fh,
        ):
            sock.sendall(b"zINSTREAM\0")
            while chunk := fh.read(64 * 1024):
                sock.sendall(struct.pack("!L", len(chunk)) + chunk)
            sock.sendall(struct.pack("!L", 0))
            reply = b""
            while not reply.endswith(b"\0"):
                data = sock.recv(4096)
                if not data:
                    break
                reply += data
    except OSError as exc:
        raise ImageRejected(
            503,
            "scan_unavailable",
            "Virus scanning is unavailable, try again later.",
        ) from exc
    text = reply.rstrip(b"\0").decode("utf-8", "replace")
    if text.endswith("OK"):
        return
    if "FOUND" in text:
        raise ImageRejected(
            422,
            "malware_detected",
            "The file was rejected by the virus scanner.",
        )
    raise ImageRejected(503, "scan_failed", "Virus scanning failed, try again later.")
