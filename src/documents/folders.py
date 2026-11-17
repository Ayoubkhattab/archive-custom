"""
Who sees which folder, and walking the folder tree.

A folder belongs to its owner, like a tag. Unlike a tag, a folder's contents
can be shared: a user who may see a document in someone else's folder sees
that folder too, and the folders above it, so the document can be reached by
browsing. Folders are few next to documents, so the tree is loaded whole
(id and parent only) and walked in Python.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from django.db.models import Q

from documents.models import Document
from documents.models import Folder
from documents.permissions import get_objects_for_user_owner_aware

if TYPE_CHECKING:
    from collections.abc import Iterable

    from django.contrib.auth.models import User


def _parents() -> dict[int, int | None]:
    return dict(Folder.objects.values_list("pk", "parent_id"))


def ancestor_ids(folder_ids: Iterable[int], parents: dict | None = None) -> set[int]:
    """The folders given and every folder above them."""
    parents = _parents() if parents is None else parents
    result: set[int] = set()
    for folder_id in folder_ids:
        while folder_id is not None and folder_id not in result:
            result.add(folder_id)
            folder_id = parents.get(folder_id)
    return result


def descendant_ids(folder_ids: Iterable[int], parents: dict | None = None) -> set[int]:
    """The folders given and every folder below them."""
    parents = _parents() if parents is None else parents
    children: dict[int | None, list[int]] = defaultdict(list)
    for pk, parent in parents.items():
        children[parent].append(pk)
    result: set[int] = set()
    pending = [pk for pk in folder_ids if pk in parents]
    while pending:
        pk = pending.pop()
        if pk in result:
            continue
        result.add(pk)
        pending.extend(children.get(pk, ()))
    return result


def visible_documents(user: User):
    return get_objects_for_user_owner_aware(user, "view_document", Document)


def visible_folder_ids(user: User) -> set[int]:
    parents = _parents()
    if user.is_superuser:
        return set(parents)
    own = Folder.objects.filter(Q(owner=user) | Q(owner__isnull=True)).values_list(
        "pk",
        flat=True,
    )
    holding = (
        visible_documents(user)
        .filter(folder__isnull=False)
        .values_list("folder_id", flat=True)
        .distinct()
    )
    return ancestor_ids([*own, *holding], parents)


def can_change_folder(user: User, folder: Folder) -> bool:
    if user.is_superuser or folder.owner_id == user.pk:
        return True
    return folder.owner_id is None and user.has_perm("documents.change_document")


def depth_of(folder: Folder | None, parents: dict | None = None) -> int:
    """1 for a top-level folder, 0 for no folder."""
    if folder is None:
        return 0
    parents = _parents() if parents is None else parents
    depth, current, seen = 0, folder.pk, set()
    while current is not None and current not in seen:
        seen.add(current)
        depth += 1
        current = parents.get(current)
    return depth


def height_of(folder: Folder, parents: dict | None = None) -> int:
    """How many levels the folder and its subfolders span (1 when it has none)."""
    parents = _parents() if parents is None else parents
    children: dict[int | None, list[int]] = defaultdict(list)
    for pk, parent in parents.items():
        children[parent].append(pk)

    def height(pk: int, seen: frozenset) -> int:
        if pk in seen:
            return 0
        below = [height(c, seen | {pk}) for c in children.get(pk, ())]
        return 1 + max(below, default=0)

    return height(folder.pk, frozenset())
