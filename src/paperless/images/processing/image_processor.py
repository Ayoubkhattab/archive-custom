"""
Validation and re-encoding with Pillow.

Upload time (synchronous, cheap):  sniff magic bytes -> allowlist ->
    Pillow header parse + verify() -> format must agree with the magic bytes
    -> pixel count bound (decompression bombs).
Background (Celery): full decode -> EXIF orientation -> copy pixels into a
    brand-new image (drops EXIF, GPS, XMP, ICC, comments, any embedded
    payload) -> resize -> encode WebP/AVIF.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.conf import settings
from PIL import Image as PILImage
from PIL import ImageOps
from PIL import UnidentifiedImageError

from paperless.images.processing.sniff import SVG
from paperless.images.processing.sniff import sniff_mime

if TYPE_CHECKING:
    from pathlib import Path

_PIL_FORMATS = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/gif": "GIF",
    "image/webp": "WEBP",
    "image/avif": "AVIF",
}
_OUTPUT = {"webp": ("WEBP", "image/webp"), "avif": ("AVIF", "image/avif")}


class ImageRejected(Exception):
    """``status`` is the HTTP status the API answers with."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@dataclass(frozen=True)
class ImageInfo:
    mime_type: str
    width: int
    height: int


def inspect_image(path: Path) -> ImageInfo:
    with path.open("rb") as fh:
        head = fh.read(512)
    mime = sniff_mime(head)
    if mime == SVG:
        # SVG can carry scripts; there is no sanitizer in this deployment,
        # so it is refused outright rather than "allowed if sanitized".
        raise ImageRejected(415, "svg_not_allowed", "SVG images are not accepted.")
    if mime is None or mime not in settings.IMAGES_ALLOWED_TYPES:
        raise ImageRejected(415, "unsupported_type", "This file type is not accepted.")

    try:
        with PILImage.open(path) as img:
            if img.format != _PIL_FORMATS[mime]:
                # e.g. a polyglot whose magic bytes and real structure differ
                raise ImageRejected(
                    415,
                    "type_mismatch",
                    "The file content does not match its type.",
                )
            width, height = img.size
            if width <= 0 or height <= 0:
                raise ImageRejected(422, "invalid_image", "The image is empty.")
            if width * height > settings.IMAGES_MAX_PIXELS:
                raise ImageRejected(
                    422,
                    "too_many_pixels",
                    "The image dimensions are too large.",
                )
            img.verify()
    except ImageRejected:
        raise
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
        PILImage.DecompressionBombError,
    ) as exc:
        raise ImageRejected(
            422,
            "invalid_image",
            "The image is corrupted or unreadable.",
        ) from exc
    return ImageInfo(mime, width, height)


@dataclass(frozen=True)
class EncodedImage:
    data: bytes
    width: int
    height: int
    format: str  # "webp" / "avif"
    mime_type: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


def output_format() -> tuple[str, str, str]:
    fmt = (
        settings.IMAGES_OUTPUT_FORMAT
        if settings.IMAGES_OUTPUT_FORMAT in _OUTPUT
        else "webp"
    )
    pil_format, mime = _OUTPUT[fmt]
    return fmt, pil_format, mime


def load_clean(path: Path) -> PILImage.Image:
    """Decode, apply EXIF orientation, and return a metadata-free copy."""
    previous = PILImage.MAX_IMAGE_PIXELS
    PILImage.MAX_IMAGE_PIXELS = settings.IMAGES_MAX_PIXELS
    try:
        with PILImage.open(path) as img:
            img.seek(0)  # animated GIF/WebP: first frame only
            img.load()
            oriented = ImageOps.exif_transpose(img)
            has_alpha = oriented.mode in ("RGBA", "LA", "PA") or (
                oriented.mode == "P" and "transparency" in oriented.info
            )
            mode = "RGBA" if has_alpha else "RGB"
            converted = oriented.convert(mode)
    finally:
        PILImage.MAX_IMAGE_PIXELS = previous
    clean = PILImage.new(mode, converted.size)
    clean.paste(converted)
    return clean  # .info is empty: no EXIF, GPS, XMP, ICC or comments


def encode(img: PILImage.Image, max_width: int | None) -> EncodedImage:
    fmt, pil_format, mime = output_format()
    work = img
    if max_width and img.width > max_width:
        height = max(1, round(img.height * max_width / img.width))
        work = img.resize((max_width, height), PILImage.Resampling.LANCZOS)
    buf = io.BytesIO()
    options = {"quality": settings.IMAGES_OUTPUT_QUALITY}
    if pil_format == "WEBP":
        options["method"] = 4  # good size/speed trade-off
    work.save(buf, format=pil_format, **options)
    return EncodedImage(buf.getvalue(), work.width, work.height, fmt, mime)
