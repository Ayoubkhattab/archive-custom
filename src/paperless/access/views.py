"""Read-only audit log API, for global admins only."""

from __future__ import annotations

from rest_framework import serializers
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ReadOnlyModelViewSet

from paperless.access.drf import IsGlobalAdmin
from paperless.access.drf import PermissionScopedMixin
from paperless.models import SecurityAuditLog


class SecurityAuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = SecurityAuditLog
        fields = (
            "id",
            "created_at",
            "actor_id",
            "actor_username",
            "action",
            "target_type",
            "target_id",
            "outcome",
            "before_json",
            "after_json",
            "ip",
            "user_agent",
            "request_id",
        )
        read_only_fields = fields


class AuditPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 500


class SecurityAuditLogViewSet(PermissionScopedMixin, ReadOnlyModelViewSet):
    """GET /api/audit_logs/ (filters: action, target_type, target_id, actor_id)."""

    serializer_class = SecurityAuditLogSerializer
    permission_classes = (IsAuthenticated, IsGlobalAdmin)
    pagination_class = AuditPagination

    def get_queryset(self):
        qs = SecurityAuditLog.objects.all().order_by("-created_at", "-id")
        params = self.request.query_params
        for field in ("action", "target_type", "target_id", "outcome"):
            if params.get(field):
                qs = qs.filter(**{field: params[field]})
        if params.get("actor_id", "").isdigit():
            qs = qs.filter(actor_id=int(params["actor_id"]))
        return qs
