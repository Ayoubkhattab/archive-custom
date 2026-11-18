"""
Authenticated file streaming with HTTP caching and byte ranges.

Used by the PDF and image endpoints. Authorization happens *before* this is
called; this module only produces the response:

* ``ETag`` / ``Last-Modified`` with ``If-None-Match`` / ``If-Modified-Since``
  -> 304
* ``Range: bytes=...`` (single range) -> 206 with ``Content-Range``,
  unsatisfiable -> 416 with ``Content-Range: bytes */size``
* ``If-Range`` that does not match -> full 200
* ``Accept-Ranges: bytes`` always, ``X-Content-Type-Options: nosniff``
* optional ``X-Accel-Redirect`` hand-off to Nginx (which then does ranges)
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from typing import BinaryIO

from django.http import HttpRequest
from django.http import HttpResponse
from django.http import StreamingHttpResponse
from django.utils.http import http_date
from django.utils.http import parse_http_date_safe

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Iterator
    from datetime import datetime

CHUNK_SIZE = 64 * 1024
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def parse_range(header: str | None, size: int) -> tuple[int, int] | None | str:
    """
    Returns (start, end) inclusive, None to serve the whole file, or
    "unsatisfiable". Multiple ranges are answered with the full file, which
    RFC 9110 allows.
    """
    if not header:
        return None
    match = _RANGE_RE.match(header.strip())
    if not match:
        return None  # malformed or multi-range: ignore
    first, last = match.groups()
    if first == "" and last == "":
        return None
    if first == "":
        # suffix range: last N bytes
        length = int(last)
        if length == 0:
            return "unsatisfiable"
        start = max(size - length, 0)
        end = size - 1
    else:
        start = int(first)
        if size == 0 or start >= size:
            return "unsatisfiable"
        end = int(last) if last else size - 1
        if end < start:
            return None  # syntactically invalid range: ignore it
        end = min(end, size - 1)
    if size == 0:
        return "unsatisfiable"
    return start, end


def _etag_matches(header: str, etag: str) -> bool:
    if header.strip() == "*":
        return True
    candidates = [c.strip().removeprefix("W/") for c in header.split(",")]
    return etag in candidates


def _iter_file(
    open_file: Callable[[], BinaryIO],
    start: int,
    length: int,
) -> Iterator[bytes]:
    handle = open_file()
    try:
        if start:
            handle.seek(start)
        remaining = length
        while remaining > 0:
            chunk = handle.read(min(CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        handle.close()


def ranged_file_response(
    request: HttpRequest,
    *,
    open_file: Callable[[], BinaryIO],
    size: int,
    content_type: str,
    etag: str | None = None,
    last_modified: datetime | None = None,
    disposition: str | None = None,
    cache_control: str = "private, no-cache",
    x_accel_path: str | None = None,
) -> HttpResponse:
    quoted_etag = f'"{etag}"' if etag and not etag.startswith('"') else etag

    def common(response: HttpResponse) -> HttpResponse:
        response["Accept-Ranges"] = "bytes"
        response["Cache-Control"] = cache_control
        response["X-Content-Type-Options"] = "nosniff"
        if quoted_etag:
            response["ETag"] = quoted_etag
        if last_modified:
            response["Last-Modified"] = http_date(last_modified.timestamp())
        if disposition:
            response["Content-Disposition"] = disposition
        return response

    # Conditional GET
    inm = request.headers.get("If-None-Match")
    if inm and quoted_etag and _etag_matches(inm, quoted_etag):
        return common(HttpResponse(status=304))
    ims = request.headers.get("If-Modified-Since")
    if not inm and ims and last_modified:
        since = parse_http_date_safe(ims)
        if since is not None and int(last_modified.timestamp()) <= since:
            return common(HttpResponse(status=304))

    if x_accel_path:
        response = HttpResponse(content_type=content_type)
        response["X-Accel-Redirect"] = x_accel_path
        return common(response)

    byte_range = parse_range(request.headers.get("Range"), size)
    if_range = request.headers.get("If-Range")
    if (
        byte_range not in (None, "unsatisfiable")
        and if_range
        and not (quoted_etag and if_range.strip() == quoted_etag)
    ):
        # If-Range with a date: still valid only if not modified since.
        if_range_date = parse_http_date_safe(if_range)
        if not (
            last_modified
            and if_range_date is not None
            and int(last_modified.timestamp()) <= if_range_date
        ):
            byte_range = None

    if byte_range == "unsatisfiable":
        response = HttpResponse(status=416)
        response["Content-Range"] = f"bytes */{size}"
        return common(response)

    if byte_range is None:
        response = StreamingHttpResponse(
            _iter_file(open_file, 0, size),
            content_type=content_type,
        )
        response["Content-Length"] = str(size)
        return common(response)

    start, end = byte_range
    length = end - start + 1
    response = StreamingHttpResponse(
        _iter_file(open_file, start, length),
        status=206,
        content_type=content_type,
    )
    response["Content-Length"] = str(length)
    response["Content-Range"] = f"bytes {start}-{end}/{size}"
    return common(response)
