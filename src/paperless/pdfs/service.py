from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unicodedata import normalize
from urllib.parse import quote

from paperless.access.constants import Action
from paperless.access.policy import Resource
from paperless.access.policy import can


@dataclass(frozen=True)
class PdfFile:
    path: Path
    size: int
    checksum: str | None
    filename: str
    encrypted_storage: bool


def can_view_pdf(actor, document) -> bool:
    """
    Existing document sharing (owner / object permissions / unowned) OR the
    hierarchical policy (pdf.view.self / .child / .any, system.manage.all).
    """
    from documents.permissions import has_perms_owner_aware

    if actor is None or not actor.is_authenticated:
        return False
    if has_perms_owner_aware(actor, "view_document", document):
        return True
    return can(actor=actor, action=Action.VIEW, resource=Resource.pdf(document))


def content_disposition(disposition: str, filename: str) -> str:
    # Firefox needs RFC 5987 for unicode, Chromium chokes on commas.
    ascii_name = (
        normalize("NFKD", filename.replace(",", "_"))
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    ascii_name = ascii_name.replace('"', "").replace("\\", "")
    return (
        f"{disposition}; filename=\"{ascii_name}\"; filename*=utf-8''{quote(filename)}"
    )


def get_pdf_file(document, *, original: bool = False) -> PdfFile | None:
    """The PDF rendition of a document, or None if it has none."""
    from documents.models import Document

    if not original and document.has_archive_version:
        path = Path(document.archive_path)
        checksum = document.archive_checksum
        filename = document.get_public_filename(archive=True)
    elif document.mime_type == "application/pdf":
        path = Path(document.source_path)
        checksum = document.checksum
        filename = document.get_public_filename()
    else:
        return None
    if not path.is_file():
        return None
    return PdfFile(
        path=path,
        size=path.stat().st_size,
        checksum=checksum,
        filename=filename,
        encrypted_storage=document.storage_type == Document.STORAGE_TYPE_GPG,
    )
