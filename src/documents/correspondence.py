"""
Shared logic for official correspondence turnaround analysis (مدة الاختناق).

A document's turnaround clock starts when it is handed to the receiving entity
(``sent_date``), or when internal processing was closed (``internal_closed_date``)
if that was recorded, and stops when it comes back (``returned_date``). Documents
that have not come back yet are measured against today so overdue items surface
in reports instead of staying invisible.

Days within the grace period (``PAPERLESS_BOTTLENECK_GRACE_DAYS``, 3 by default)
are considered normal; only the excess counts as bottleneck days.

``Document.turnaround_days`` / ``Document.bottleneck_days`` implement the same
rules for a single instance. Every consumer goes through this module so that
filtering, reporting and the API can never disagree on the definition.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db.models import F
from django.db.models import Q
from django.db.models import QuerySet
from django.db.models.functions import Coalesce
from django.utils import timezone

# Name of the annotation holding the turnaround start date. Deliberately not
# `turnaround_start`: that is a read-only property on Document, and Django
# assigns annotations onto the instance, which would fail on the missing setter.
START_ANNOTATION = "_turnaround_start"


def grace_days() -> int:
    """Days a document may spend outside before bottleneck days accrue."""
    return settings.BOTTLENECK_GRACE_DAYS


def annotate_turnaround_start(queryset: QuerySet) -> QuerySet:
    """Annotate the date the turnaround clock starts from."""
    return queryset.annotate(
        **{START_ANNOTATION: Coalesce("internal_closed_date", "sent_date")},
    )


def is_routed() -> Q:
    """Documents that have actually been routed to an entity."""
    return Q(sent_date__isnull=False) | Q(internal_closed_date__isnull=False)


def is_awaiting_return() -> Q:
    """Routed and not returned yet."""
    return is_routed() & Q(returned_date__isnull=True)


def is_bottlenecked(today: datetime.date | None = None) -> Q:
    """
    Turnaround that already exceeded the grace period.

    Requires the queryset to be annotated by :func:`annotate_turnaround_start`.
    Uses documented ``F() + timedelta`` date arithmetic, which is portable
    across the supported database backends.
    """
    today = today or timezone.localdate()
    grace = timedelta(days=grace_days())
    returned_late = Q(
        returned_date__isnull=False,
        returned_date__gt=F(START_ANNOTATION) + grace,
    )
    still_out_too_long = Q(
        returned_date__isnull=True,
        **{f"{START_ANNOTATION}__lt": today - grace},
    )
    return returned_late | still_out_too_long


def compute_turnaround_days(
    start: datetime.date | None,
    returned: datetime.date | None,
    today: datetime.date | None = None,
) -> int | None:
    """Whole calendar days spent outside. ``None`` when never routed."""
    if start is None:
        return None
    end = returned or today or timezone.localdate()
    return max((end - start).days, 0)


def compute_bottleneck_days(
    start: datetime.date | None,
    returned: datetime.date | None,
    today: datetime.date | None = None,
    grace: int | None = None,
) -> int | None:
    """Days beyond the grace period, floored at zero. ``None`` when never routed."""
    turnaround = compute_turnaround_days(start, returned, today)
    if turnaround is None:
        return None
    return max(turnaround - (grace_days() if grace is None else grace), 0)


@dataclass
class TurnaroundStats:
    """Aggregated turnaround figures for a group of documents."""

    routed_count: int = 0
    returned_count: int = 0
    awaiting_return_count: int = 0
    bottlenecked_count: int = 0
    total_bottleneck_days: int = 0
    _turnaround_total: int = 0

    def add(self, turnaround: int | None, bottleneck: int | None, *, returned: bool):
        if turnaround is None:
            return
        self.routed_count += 1
        self._turnaround_total += turnaround
        if returned:
            self.returned_count += 1
        else:
            self.awaiting_return_count += 1
        if bottleneck:
            self.bottlenecked_count += 1
            self.total_bottleneck_days += bottleneck

    @property
    def average_turnaround_days(self) -> float | None:
        if not self.routed_count:
            return None
        return round(self._turnaround_total / self.routed_count, 1)

    @property
    def average_bottleneck_days(self) -> float | None:
        if not self.routed_count:
            return None
        return round(self.total_bottleneck_days / self.routed_count, 1)

    def as_dict(self) -> dict:
        return {
            "routed_count": self.routed_count,
            "returned_count": self.returned_count,
            "awaiting_return_count": self.awaiting_return_count,
            "bottlenecked_count": self.bottlenecked_count,
            "total_bottleneck_days": self.total_bottleneck_days,
            "average_turnaround_days": self.average_turnaround_days,
            "average_bottleneck_days": self.average_bottleneck_days,
        }
