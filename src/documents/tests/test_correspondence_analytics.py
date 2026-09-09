import datetime

from django.contrib.auth.models import User
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from documents.models import Correspondent
from documents.models import Document
from documents.models import DocumentClassification
from documents.models import DocumentType
from documents.tests.utils import DirectoriesMixin


class TestBottleneckCalculation(DirectoriesMixin, APITestCase):
    """The turnaround rules on the Document model itself."""

    def _document(self, **kwargs) -> Document:
        defaults = {
            "title": "doc",
            "checksum": str(Document.objects.count()),
            "mime_type": "application/pdf",
        }
        return Document.objects.create(**{**defaults, **kwargs})

    def test_never_routed_has_no_turnaround(self):
        doc = self._document()
        self.assertIsNone(doc.turnaround_start)
        self.assertIsNone(doc.turnaround_days)
        self.assertIsNone(doc.bottleneck_days)
        self.assertFalse(doc.is_awaiting_return)

    def test_turnaround_measured_from_sent_date(self):
        doc = self._document(
            sent_date=datetime.date(2026, 1, 1),
            returned_date=datetime.date(2026, 1, 11),
        )
        self.assertEqual(doc.turnaround_days, 10)

    def test_internal_closing_date_takes_precedence(self):
        doc = self._document(
            sent_date=datetime.date(2026, 1, 1),
            internal_closed_date=datetime.date(2026, 1, 6),
            returned_date=datetime.date(2026, 1, 11),
        )
        self.assertEqual(doc.turnaround_start, datetime.date(2026, 1, 6))
        self.assertEqual(doc.turnaround_days, 5)

    @override_settings(BOTTLENECK_GRACE_DAYS=3)
    def test_within_grace_period_is_not_a_bottleneck(self):
        doc = self._document(
            sent_date=datetime.date(2026, 1, 1),
            returned_date=datetime.date(2026, 1, 4),
        )
        self.assertEqual(doc.turnaround_days, 3)
        self.assertEqual(doc.bottleneck_days, 0)
        self.assertFalse(doc.is_bottlenecked)

    @override_settings(BOTTLENECK_GRACE_DAYS=3)
    def test_only_days_beyond_the_grace_period_count(self):
        doc = self._document(
            sent_date=datetime.date(2026, 1, 1),
            returned_date=datetime.date(2026, 1, 10),
        )
        self.assertEqual(doc.turnaround_days, 9)
        self.assertEqual(doc.bottleneck_days, 6)
        self.assertTrue(doc.is_bottlenecked)

    @override_settings(BOTTLENECK_GRACE_DAYS=5)
    def test_grace_period_is_configurable(self):
        doc = self._document(
            sent_date=datetime.date(2026, 1, 1),
            returned_date=datetime.date(2026, 1, 10),
        )
        self.assertEqual(doc.bottleneck_days, 4)

    def test_open_document_is_measured_against_today(self):
        doc = self._document(
            sent_date=datetime.date.today() - datetime.timedelta(days=8),
        )
        self.assertTrue(doc.is_awaiting_return)
        self.assertEqual(doc.turnaround_days, 8)

    def test_same_day_return_is_zero_not_negative(self):
        day = datetime.date(2026, 1, 1)
        doc = self._document(sent_date=day, returned_date=day)
        self.assertEqual(doc.turnaround_days, 0)
        self.assertEqual(doc.bottleneck_days, 0)


class TestCorrespondenceAnalyticsAPI(DirectoriesMixin, APITestCase):
    ENDPOINT = "/api/correspondence_analytics/"

    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create_superuser(username="admin")
        self.client.force_authenticate(user=self.user)

        self.ministry = Correspondent.objects.create(
            name="Ministry",
            code="MIN-01",
            diwan_number="D-100",
        )
        self.bank = Correspondent.objects.create(name="Bank", code="BNK-01")
        self.letter = DocumentType.objects.create(name="Letter")
        self.secret = DocumentClassification.objects.create(
            name="Confidential",
            code="CONF",
        )
        self.counter = 0

    def _document(self, **kwargs) -> Document:
        self.counter += 1
        defaults = {
            "title": f"doc{self.counter}",
            "checksum": f"checksum{self.counter}",
            "mime_type": "application/pdf",
            "created": datetime.date(2026, 3, 10),
        }
        doc = Document.objects.create(**{**defaults, **kwargs})
        # `added` has a default of now(); force it into the reporting window.
        Document.objects.filter(pk=doc.pk).update(
            added=datetime.datetime(2026, 3, 10, 12, 0, tzinfo=datetime.timezone.utc),
        )
        doc.refresh_from_db()
        return doc

    def test_requires_authentication(self):
        self.client.force_authenticate(user=None)
        response = self.client.get(self.ENDPOINT)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    @override_settings(BOTTLENECK_GRACE_DAYS=3)
    def test_counts_sent_received_and_uploaded_per_entity(self):
        self._document(
            sender=self.ministry,
            recipient=self.bank,
            correspondent=self.ministry,
            document_type=self.letter,
        )
        self._document(sender=self.bank, recipient=self.ministry)

        response = self.client.get(f"{self.ENDPOINT}?month=2026-03")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        entities = {row["name"]: row for row in response.data["entities"]}
        self.assertEqual(entities["Ministry"]["sent_count"], 1)
        self.assertEqual(entities["Ministry"]["received_count"], 1)
        self.assertEqual(entities["Ministry"]["uploaded_count"], 1)
        self.assertEqual(entities["Ministry"]["code"], "MIN-01")
        self.assertEqual(entities["Ministry"]["diwan_number"], "D-100")
        self.assertEqual(entities["Bank"]["sent_count"], 1)
        self.assertEqual(entities["Bank"]["received_count"], 1)
        self.assertEqual(entities["Bank"]["uploaded_count"], 0)

    @override_settings(BOTTLENECK_GRACE_DAYS=3)
    def test_bottleneck_days_attributed_to_the_receiving_entity(self):
        self._document(
            sender=self.ministry,
            recipient=self.bank,
            sent_date=datetime.date(2026, 3, 1),
            returned_date=datetime.date(2026, 3, 11),
        )

        response = self.client.get(f"{self.ENDPOINT}?month=2026-03")
        entities = {row["name"]: row for row in response.data["entities"]}

        # 10 days out, 3 tolerated -> 7 bottleneck days, charged to the receiver.
        self.assertEqual(entities["Bank"]["total_bottleneck_days"], 7)
        self.assertEqual(entities["Bank"]["bottlenecked_count"], 1)
        self.assertEqual(entities["Bank"]["average_turnaround_days"], 10.0)
        self.assertEqual(entities["Ministry"]["total_bottleneck_days"], 0)
        self.assertEqual(entities["Ministry"]["routed_count"], 0)

        totals = response.data["totals"]
        self.assertEqual(totals["documents"], 1)
        self.assertEqual(totals["routed_count"], 1)
        self.assertEqual(totals["returned_count"], 1)
        self.assertEqual(totals["total_bottleneck_days"], 7)
        self.assertEqual(response.data["grace_days"], 3)

    @override_settings(BOTTLENECK_GRACE_DAYS=3)
    def test_awaiting_return_is_counted(self):
        self._document(
            recipient=self.bank,
            sent_date=datetime.date(2026, 3, 1),
        )
        response = self.client.get(f"{self.ENDPOINT}?month=2026-03")
        self.assertEqual(response.data["totals"]["awaiting_return_count"], 1)
        self.assertEqual(response.data["totals"]["returned_count"], 0)

    def test_month_parameter_scopes_the_report(self):
        self._document(recipient=self.bank)
        response = self.client.get(f"{self.ENDPOINT}?month=2026-01")
        self.assertEqual(response.data["totals"]["documents"], 0)
        self.assertEqual(response.data["period"]["date_from"], "2026-01-01")
        self.assertEqual(response.data["period"]["date_to"], "2026-01-31")

    def test_february_end_of_month_is_correct(self):
        response = self.client.get(f"{self.ENDPOINT}?month=2026-02")
        self.assertEqual(response.data["period"]["date_to"], "2026-02-28")

    def test_december_end_of_month_is_correct(self):
        response = self.client.get(f"{self.ENDPOINT}?month=2026-12")
        self.assertEqual(response.data["period"]["date_to"], "2026-12-31")

    def test_explicit_date_range(self):
        self._document(recipient=self.bank)
        response = self.client.get(
            f"{self.ENDPOINT}?date_from=2026-03-01&date_to=2026-03-31",
        )
        self.assertEqual(response.data["totals"]["documents"], 1)

    def test_date_field_can_target_the_sent_date(self):
        self._document(
            recipient=self.bank,
            sent_date=datetime.date(2026, 5, 4),
        )
        response = self.client.get(
            f"{self.ENDPOINT}?month=2026-05&date_field=sent_date",
        )
        self.assertEqual(response.data["totals"]["documents"], 1)
        # The same document is outside the window when scoped by upload date.
        response = self.client.get(f"{self.ENDPOINT}?month=2026-05")
        self.assertEqual(response.data["totals"]["documents"], 0)

    def test_invalid_month_is_rejected(self):
        response = self.client.get(f"{self.ENDPOINT}?month=nonsense")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_date_field_is_rejected(self):
        response = self.client.get(f"{self.ENDPOINT}?date_field=title")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_inverted_range_is_rejected(self):
        response = self.client.get(
            f"{self.ENDPOINT}?date_from=2026-03-31&date_to=2026-03-01",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_monthly_trend_always_has_twelve_points(self):
        response = self.client.get(f"{self.ENDPOINT}?month=2026-03")
        monthly = response.data["monthly"]
        self.assertEqual(len(monthly), 12)
        self.assertEqual(monthly[-1]["month"], "2026-03")
        self.assertEqual(monthly[0]["month"], "2025-04")

    def test_breakdown_by_type_and_classification(self):
        self._document(
            recipient=self.bank,
            document_type=self.letter,
            classification=self.secret,
        )
        response = self.client.get(f"{self.ENDPOINT}?month=2026-03")
        self.assertEqual(
            response.data["by_document_type"],
            [{"id": self.letter.id, "name": "Letter", "count": 1}],
        )
        self.assertEqual(
            response.data["by_classification"],
            [{"id": self.secret.id, "name": "Confidential", "count": 1}],
        )


class TestCorrespondenceRoutingAPI(DirectoriesMixin, APITestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create_superuser(username="admin")
        self.client.force_authenticate(user=self.user)
        self.entity = Correspondent.objects.create(name="Entity", code="E-1")
        self.other = Correspondent.objects.create(name="Other", code="E-2")
        self.doc = Document.objects.create(
            title="doc",
            checksum="abc",
            mime_type="application/pdf",
        )

    def test_routing_fields_round_trip(self):
        response = self.client.patch(
            f"/api/documents/{self.doc.id}/",
            {
                "sender": self.entity.id,
                "recipient": self.other.id,
                "diwan_number": "2026/144",
                "sent_date": "2026-03-01",
                "returned_date": "2026-03-09",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.sender, self.entity)
        self.assertEqual(self.doc.recipient, self.other)
        self.assertEqual(self.doc.diwan_number, "2026/144")
        self.assertEqual(self.doc.sent_date, datetime.date(2026, 3, 1))
        self.assertEqual(response.data["turnaround_days"], 8)

    def test_returned_before_sent_is_rejected(self):
        response = self.client.patch(
            f"/api/documents/{self.doc.id}/",
            {"sent_date": "2026-03-10", "returned_date": "2026-03-01"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("returned_date", response.data)

    def test_internal_closing_before_sent_is_rejected(self):
        response = self.client.patch(
            f"/api/documents/{self.doc.id}/",
            {"sent_date": "2026-03-10", "internal_closed_date": "2026-03-01"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("internal_closed_date", response.data)

    def test_returned_date_without_a_start_is_rejected(self):
        response = self.client.patch(
            f"/api/documents/{self.doc.id}/",
            {"returned_date": "2026-03-01"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_validation_considers_dates_already_stored(self):
        self.doc.sent_date = datetime.date(2026, 3, 10)
        self.doc.save()
        response = self.client.patch(
            f"/api/documents/{self.doc.id}/",
            {"returned_date": "2026-03-01"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @override_settings(BOTTLENECK_GRACE_DAYS=3)
    def test_filter_documents_by_bottleneck(self):
        Document.objects.create(
            title="late",
            checksum="late",
            mime_type="application/pdf",
            sent_date=datetime.date(2026, 3, 1),
            returned_date=datetime.date(2026, 3, 20),
        )
        Document.objects.create(
            title="ontime",
            checksum="ontime",
            mime_type="application/pdf",
            sent_date=datetime.date(2026, 3, 1),
            returned_date=datetime.date(2026, 3, 3),
        )

        response = self.client.get("/api/documents/?is_bottlenecked=true")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        titles = {row["title"] for row in response.data["results"]}
        self.assertEqual(titles, {"late"})

    def test_filter_documents_awaiting_return(self):
        Document.objects.create(
            title="out",
            checksum="out",
            mime_type="application/pdf",
            sent_date=datetime.date(2026, 3, 1),
        )
        response = self.client.get("/api/documents/?is_awaiting_return=true")
        titles = {row["title"] for row in response.data["results"]}
        self.assertEqual(titles, {"out"})


class TestCorrespondentEntityFields(DirectoriesMixin, APITestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create_superuser(username="admin")
        self.client.force_authenticate(user=self.user)

    def test_blank_codes_do_not_collide(self):
        """The unique code column must tolerate any number of entities without one."""
        Correspondent.objects.create(name="A")
        Correspondent.objects.create(name="B")
        self.assertEqual(Correspondent.objects.filter(code__isnull=True).count(), 2)

    def test_entity_counts_are_not_inflated_by_join_fan_out(self):
        entity = Correspondent.objects.create(name="Entity", code="E-1")
        other = Correspondent.objects.create(name="Other")
        for index in range(3):
            Document.objects.create(
                title=f"doc{index}",
                checksum=f"sum{index}",
                mime_type="application/pdf",
                correspondent=entity,
                sender=entity,
                recipient=other,
            )

        response = self.client.get(f"/api/correspondents/{entity.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["document_count"], 3)
        self.assertEqual(response.data["sent_document_count"], 3)
        self.assertEqual(response.data["received_document_count"], 0)
        self.assertEqual(response.data["code"], "E-1")

    def test_document_classification_endpoint(self):
        response = self.client.post(
            "/api/document_classifications/",
            {"name": "Routine", "code": "RTN"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            DocumentClassification.objects.get(name="Routine").code,
            "RTN",
        )
