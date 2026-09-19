from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient


class TestSecurityHardening(TestCase):
    def setUp(self):
        cache.clear()

    def test_remote_version_requires_login(self):
        """
        Every uncached call makes an outbound request to GitHub, so anonymous
        visitors must not be able to trigger it.
        """
        response = APIClient().get("/api/remote_version/")

        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    def test_token_endpoint_is_rate_limited(self):
        """
        Password guessing against /api/token/ is throttled per client.
        """
        client = APIClient()
        codes = [
            client.post(
                "/api/token/",
                {"username": "nobody", "password": "wrong"},
            ).status_code
            for _ in range(12)
        ]

        self.assertEqual(codes[0], status.HTTP_400_BAD_REQUEST)
        self.assertEqual(codes[-1], status.HTTP_429_TOO_MANY_REQUESTS)

    def test_admin_login_goes_through_allauth(self):
        """
        Django's own admin login form would skip the MFA step.
        """
        response = self.client.get("/admin/login/")

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("/accounts/login/"))

    def test_admin_index_requires_login(self):
        response = self.client.get("/admin/")

        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_hardening_headers_are_sent(self):
        response = self.client.get("/accounts/login/")

        self.assertIn("frame-ancestors 'self'", response["Content-Security-Policy"])
        self.assertIn("camera=()", response["Permissions-Policy"])
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")

    def test_websocket_checks_the_origin(self):
        from channels.security.websocket import OriginValidator

        from paperless.asgi import application

        # AllowedHostsOriginValidator() builds an OriginValidator.
        self.assertIsInstance(
            application.application_mapping["websocket"],
            OriginValidator,
        )

    def test_anonymous_user_cannot_use_analytics(self):
        response = APIClient().get("/api/correspondence_analytics/")

        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    def test_user_without_view_permission_cannot_use_analytics(self):
        client = APIClient()
        client.force_authenticate(User.objects.create_user("nobody"))

        response = client.get("/api/correspondence_analytics/")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class TestApplicationConfigSingleton(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User

        self.client = APIClient()
        self.client.force_authenticate(
            User.objects.create_superuser("admin", "a@example.com", "pw"),
        )

    def test_save_works_when_the_client_lost_the_id(self):
        """
        The settings page once sent PATCH /api/config/null/ and got a 404.
        """
        response = self.client.patch(
            "/api/config/null/",
            {"ai_enabled": True},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["ai_enabled"])

    def test_config_is_recreated_if_the_row_is_missing(self):
        from paperless.models import ApplicationConfiguration

        ApplicationConfiguration.objects.all().delete()

        response = self.client.get("/api/config/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertIsNotNone(response.data[0]["id"])
