from django.conf import settings
from django.utils import translation

from paperless import version


class ApiVersionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.user.is_authenticated:
            versions = settings.REST_FRAMEWORK["ALLOWED_VERSIONS"]
            response["X-Api-Version"] = versions[len(versions) - 1]
            response["X-Version"] = version.__full_version_str__

        return response


class SecurityHeadersMiddleware:
    """
    Adds browser hardening headers that Django's SecurityMiddleware doesn't.

    The CSP is deliberately limited to directives that cannot break the Angular
    app (document previews use same-origin <object> elements, so object-src has
    to allow 'self'). A script-src policy needs testing against a real build
    and belongs in the reverse proxy.
    """

    HEADERS = {
        "Content-Security-Policy": (
            "object-src 'self'; base-uri 'self'; frame-ancestors 'self'"
        ),
        "Permissions-Policy": (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        ),
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        for name, value in self.HEADERS.items():
            # Never override a header a view chose on purpose (e.g. the CSP
            # used when serving user documents).
            response.headers.setdefault(name, value)
        return response


class ArabicAccountPagesMiddleware:
    """
    The sign-in and account pages are laid out right-to-left (see base.html), so
    render their text in Arabic whatever language the browser asks for.
    Otherwise an English browser gets English words inside a right-to-left page.
    """

    LANGUAGE = "ar-ar"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path_info.startswith("/accounts/"):
            translation.activate(self.LANGUAGE)
            request.LANGUAGE_CODE = self.LANGUAGE
        return self.get_response(request)
