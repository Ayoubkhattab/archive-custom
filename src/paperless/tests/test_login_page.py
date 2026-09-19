from django.contrib.auth.models import User
from django.test import TestCase


class TestLoginPage(TestCase):
    def setUp(self):
        User.objects.create_user("ahmad", password="correct-horse")

    def test_page_has_no_images(self):
        html = self.client.get("/accounts/login/").content.decode()

        self.assertNotIn("<img", html)
        self.assertNotIn("<svg", html)

    def test_page_is_right_to_left_arabic_whatever_the_browser_language(self):
        response = self.client.get("/accounts/login/", headers={"accept-language": "en"})
        html = response.content.decode()

        self.assertIn('dir="rtl"', html)
        self.assertIn("تسجيل الدخول", html)
        self.assertNotIn(">Username<", html)
        self.assertEqual(response["Content-Language"], "ar-ar")

    def test_other_pages_keep_the_browser_language(self):
        response = self.client.get("/api/", headers={"accept-language": "en"})

        self.assertNotEqual(response.get("Content-Language"), "ar-ar")

    def test_failed_login_keeps_the_username_and_shows_the_error(self):
        response = self.client.post(
            "/accounts/login/",
            {"login": "ahmad", "password": "wrong"},
        )
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('value="ahmad"', html)
        self.assertIn("auth-alert-danger", html)

    def test_username_is_escaped(self):
        response = self.client.post(
            "/accounts/login/",
            {"login": '"><script>alert(1)</script>', "password": "x"},
        )

        self.assertNotIn("<script>alert(1)</script>", response.content.decode())

    def test_login_still_works(self):
        response = self.client.post(
            "/accounts/login/",
            {"login": "ahmad", "password": "correct-horse"},
        )

        self.assertEqual(response.status_code, 302)
