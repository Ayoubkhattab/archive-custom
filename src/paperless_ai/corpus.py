"""
An in-memory copy of the documents' text for the archive-wide AI chat.

Every question used to load every visible document from the database, OCR
text and all, and the matcher then normalised all of that text again. On a
real archive that alone is seconds before the model even starts. Here the
text is kept per worker process and reused:

- each question asks the database only for (id, modified) of the documents
  the user may see, which is cheap;
- only documents that are new, or changed since they were cached, are read in
  full;
- the same string objects are handed to the matcher every time, so its
  normalisation caches (keyed by the text) hit at once instead of hashing and
  re-normalising megabytes of text.

A deleted document simply stops being returned, because only ids the database
still lists for the user are looked up.
"""

import threading
from dataclasses import dataclass
from datetime import date
from datetime import datetime
from typing import Any

from django.db.models import QuerySet

from documents.models import Document

# Read changed documents in batches, so a first question on a large archive
# doesn't pull everything in one enormous query.
FETCH_BATCH = 500


@dataclass(frozen=True, slots=True)
class CorpusDocument:
    """What the chat needs of a document; duck-types as one for the matcher."""

    pk: int
    title: str
    content: str
    created: date | datetime | None
    filename: str | None
    modified: datetime | None

    @property
    def id(self) -> int:
        return self.pk


_lock = threading.Lock()
_documents: dict[int, CorpusDocument] = {}


def corpus_for(queryset: QuerySet) -> list[CorpusDocument]:
    """
    The documents of `queryset` (already filtered to what the user may see),
    from the cache where still current.
    """
    if not isinstance(queryset, QuerySet):
        # Already materialised documents (tests, callers with a list).
        return list(queryset)

    current: list[tuple[int, Any]] = list(
        queryset.order_by().values_list("pk", "modified"),
    )

    with _lock:
        stale = [
            pk
            for pk, modified in current
            if (cached := _documents.get(pk)) is None or cached.modified != modified
        ]

    for start in range(0, len(stale), FETCH_BATCH):
        batch = stale[start : start + FETCH_BATCH]
        rows = Document.objects.filter(pk__in=batch).values(
            "pk",
            "title",
            "content",
            "created",
            "filename",
            "modified",
        )
        fresh = {
            row["pk"]: CorpusDocument(
                pk=row["pk"],
                title=row["title"] or "",
                content=row["content"] or "",
                created=row["created"],
                filename=row["filename"],
                modified=row["modified"],
            )
            for row in rows
        }
        with _lock:
            _documents.update(fresh)

    with _lock:
        result = [_documents[pk] for pk, _ in current if pk in _documents]
        # Drop documents nobody asked about lately (deleted ones, mostly) once
        # the cache is clearly larger than what is in use.
        if len(_documents) > 2 * len(current) + 1000:
            keep = {pk for pk, _ in current}
            for pk in [pk for pk in _documents if pk not in keep]:
                del _documents[pk]
    return result


def clear() -> None:
    with _lock:
        _documents.clear()
