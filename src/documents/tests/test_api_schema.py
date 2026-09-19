from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from rest_framework import status
from rest_framework.test import APITestCase


class TestApiSchema(APITestCase):
    ENDPOINT = "/api/schema/"

    def test_valid_schema(self):
        """
        Test that the schema is valid
        """
        try:
            call_command("spectacular", "--validate", "--fail-on-warn")
        except CommandError as e:
            self.fail(f"Schema validation failed: {e}")

    def test_get_schema_endpoints(self):
        """
        Test that the schema endpoints exist and return a 200 status code
        for a signed-in user
        """
        self.client.force_authenticate(User.objects.create_user("schema-reader"))

        schema_response = self.client.get(self.ENDPOINT)
        self.assertEqual(schema_response.status_code, status.HTTP_200_OK)

        ui_response = self.client.get(self.ENDPOINT + "view/")
        self.assertEqual(ui_response.status_code, status.HTTP_200_OK)

    def test_schema_endpoints_require_login(self):
        """
        The schema lists every endpoint, so anonymous visitors must not get it
        """
        for url in (self.ENDPOINT, self.ENDPOINT + "view/"):
            response = self.client.get(url)
            self.assertIn(
                response.status_code,
                (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
            )
