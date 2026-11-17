"""
The parser for files nothing else can read: video, audio, CAD drawings,
archives other than ZIP, proprietary formats...

Such a file is still worth keeping in the archive, filed, tagged and
downloadable, so instead of refusing it (see CONSUMER_STORE_UNSUPPORTED) it is
stored with no extracted text and a thumbnail that shows what kind of file it
is. It is found by its title, its metadata and its notes.
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from PIL import Image
from PIL import ImageDraw
from PIL import ImageFont

from documents.parsers import DocumentParser

THUMBNAIL_SIZE = (500, 700)
BACKGROUND = "#f4f6f8"
SHEET = "white"
OUTLINE = "#c3cad3"
ACCENT = "#4a6fa5"
TEXT = "#5b6470"


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(
            font=settings.THUMBNAIL_FONT_NAME,
            size=size,
            layout_engine=ImageFont.Layout.BASIC,
        )
    except OSError:
        return ImageFont.load_default()


def _extension_label(file_name: str | None, document_path: Path) -> str:
    suffix = Path(file_name or document_path.name).suffix.lstrip(".")
    if not suffix or len(suffix) > 8 or not suffix.isalnum():
        return "FILE"
    return suffix.upper()


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"  # pragma: no cover


class GenericDocumentParser(DocumentParser):
    logging_name = "paperless.parsing.generic"

    def get_settings(self):
        return None

    def parse(self, document_path: Path, mime_type, file_name=None):
        # Nothing to read. An empty text, not None: the document is valid and
        # complete, it just has no words in it.
        self.text = ""

    def get_thumbnail(self, document_path: Path, mime_type, file_name=None) -> Path:
        width, height = THUMBNAIL_SIZE
        image = Image.new("RGB", THUMBNAIL_SIZE, color=BACKGROUND)
        draw = ImageDraw.Draw(image)

        # A sheet of paper with a folded corner.
        left, top, right, bottom = 110, 120, width - 110, height - 170
        fold = 70
        draw.polygon(
            [
                (left, top),
                (right - fold, top),
                (right, top + fold),
                (right, bottom),
                (left, bottom),
            ],
            fill=SHEET,
            outline=OUTLINE,
            width=4,
        )
        draw.polygon(
            [(right - fold, top), (right - fold, top + fold), (right, top + fold)],
            fill=OUTLINE,
        )

        label = _extension_label(file_name, document_path)
        label_font = _font(72 if len(label) <= 4 else 48)
        box = draw.textbbox((0, 0), label, font=label_font)
        band_top = (top + bottom) // 2 - 55
        draw.rectangle((left - 30, band_top, right - 20, band_top + 110), fill=ACCENT)
        draw.text(
            (
                (left - 30 + right - 20 - (box[2] - box[0])) / 2,
                band_top + (110 - (box[3] - box[1])) / 2 - box[1],
            ),
            label,
            font=label_font,
            fill="white",
        )

        details = mime_type or ""
        try:
            details = f"{_human_size(document_path.stat().st_size)} · {details}"
        except OSError:
            pass
        detail_font = _font(24)
        box = draw.textbbox((0, 0), details, font=detail_font)
        draw.text(
            ((width - (box[2] - box[0])) / 2, height - 120),
            details,
            font=detail_font,
            fill=TEXT,
        )

        out_path = Path(self.tempdir) / "thumb.webp"
        image.save(out_path, format="WEBP")
        return out_path
