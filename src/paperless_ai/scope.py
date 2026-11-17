"""
Narrowing the archive-wide AI chat to the documents a user picked.

The AI page sends the same filter parameters the document list puts in its
URL (document type, correspondent, tags, storage path, dates, custom fields,
owner, text...), so one filter editor and one filter set serve both. They are
applied to the documents the user may already see, so a filter can only ever
narrow that set, never widen it.

Two kinds of parameter are not database filters: `query` (the full-text search,
which is also how the relative dates such as "within the last month" are sent)
and `more_like_id`. Those are answered by the Whoosh index, restricted to the
documents left after the other filters.
"""

import logging
from collections.abc import Mapping

from django.db.models import QuerySet
from rest_framework.exceptions import ValidationError

from documents.filters import DocumentFilterSet

logger = logging.getLogger("paperless_ai.scope")

# Parameters of the document list that are about paging or display, not about
# which documents are included.
IGNORED_PARAMS = frozenset(
    {
        "page",
        "page_size",
        "ordering",
        "fields",
        "truncate_content",
        "format",
    },
)

INDEX_PARAMS = ("query", "more_like_id")

# A "more like this" search scores every document sharing any key term; only
# the closest ones are meant.
MORE_LIKE_LIMIT = 50

MAX_PARAMS = 50
MAX_VALUE_CHARS = 2000


class ScopeError(ValueError):
    """The filters could not be understood; the message is safe to show."""


def _clean(filters) -> dict[str, str]:
    if filters is None:
        return {}
    if not isinstance(filters, Mapping):
        raise ScopeError("filters must be an object")
    if len(filters) > MAX_PARAMS:
        raise ScopeError("too many filters")

    cleaned: dict[str, str] = {}
    for key, value in filters.items():
        if not isinstance(key, str) or key in IGNORED_PARAMS:
            continue
        if value is None or value == "":
            continue
        if isinstance(value, bool):
            value = "1" if value else "0"
        elif isinstance(value, int | float):
            value = str(value)
        elif isinstance(value, list | tuple):
            if not all(isinstance(v, str | int) for v in value):
                raise ScopeError(f"invalid value for {key}")
            value = ",".join(str(v) for v in value)
        elif not isinstance(value, str):
            raise ScopeError(f"invalid value for {key}")
        if len(value) > MAX_VALUE_CHARS:
            raise ScopeError(f"value for {key} is too long")
        cleaned[key] = value
    return cleaned


def _index_ids(params: dict[str, str], queryset: QuerySet) -> list[int]:
    """Ids of the documents of `queryset` that the full-text index matches."""
    from documents import index

    query_class = (
        index.DelayedFullTextQuery
        if "query" in params
        else index.DelayedMoreLikeThisQuery
    )
    limit = None if "query" in params else MORE_LIKE_LIMIT
    with index.open_index_searcher() as searcher:
        delayed = query_class(searcher, params, 1, filter_queryset=queryset)
        q, mask, _ = delayed._get_query()
        results = searcher.search(
            q,
            mask=mask,
            filter=index.MappedDocIdSet(queryset, searcher.ixreader),
            limit=limit,
        )
        return [hit["id"] for hit in results]


def apply_filters(queryset: QuerySet, filters) -> QuerySet:
    """
    `queryset` narrowed by the document-list style `filters`.

    Raises ScopeError when a filter is malformed, so the user is told instead of
    silently getting an answer about the whole archive.
    """
    params = _clean(filters)
    if not params:
        return queryset
    visible = queryset

    db_params = {k: v for k, v in params.items() if k not in INDEX_PARAMS}
    if db_params:
        filterset = DocumentFilterSet(data=db_params, queryset=queryset)
        if not filterset.is_valid():
            fields = ", ".join(sorted(filterset.errors.keys()))
            raise ScopeError(f"invalid filters: {fields}")
        try:
            queryset = filterset.qs
        except ValidationError as exc:
            raise ScopeError(f"invalid filters: {exc.detail}") from None

    index_params = {k: v for k, v in params.items() if k in INDEX_PARAMS}
    if index_params:
        if "query" in index_params:
            # Both together is not something the document list sends.
            index_params.pop("more_like_id", None)
        else:
            try:
                reference = int(index_params["more_like_id"])
            except ValueError:
                raise ScopeError("more_like_id must be a number") from None
            # The reference document's text drives the search, so it must be
            # one the user may see.
            if not visible.filter(pk=reference).exists():
                raise ScopeError("more_like_id is not a document you can see")
        try:
            ids = _index_ids(index_params, queryset)
        except Exception as exc:
            logger.warning("Full-text filter for the AI chat failed", exc_info=True)
            raise ScopeError("the full-text search could not be run") from exc
        queryset = queryset.filter(pk__in=ids)

    # Filters on many-to-many fields (tags, custom fields) can repeat a row.
    return queryset.distinct()
