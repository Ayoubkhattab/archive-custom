from rest_framework.throttling import SimpleRateThrottle


class ImageMutationThrottle(SimpleRateThrottle):
    """Per-user limits: POST -> ``image_upload``, DELETE -> ``image_delete``."""

    SCOPES = {"POST": "image_upload", "DELETE": "image_delete"}

    def __init__(self):  # rate depends on the method, see allow_request
        pass

    def allow_request(self, request, view):
        self.scope = self.SCOPES.get(request.method)
        if self.scope is None:
            return True
        self.rate = self.get_rate()
        self.num_requests, self.duration = self.parse_rate(self.rate)
        return super().allow_request(request, view)

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            ident = f"user-{request.user.pk}"
        else:
            ident = self.get_ident(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}
