from collections import OrderedDict
from pathlib import Path

from allauth.mfa import signals
from allauth.mfa.adapter import get_adapter as get_mfa_adapter
from allauth.mfa.base.internal.flows import delete_and_cleanup
from allauth.mfa.models import Authenticator
from allauth.mfa.recovery_codes.internal.flows import auto_generate_recovery_codes
from allauth.mfa.totp.internal import auth as totp_auth
from allauth.socialaccount.adapter import get_adapter
from allauth.socialaccount.models import SocialAccount
from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.auth.models import User
from django.contrib.staticfiles.storage import staticfiles_storage
from django.db import transaction
from django.db.models.functions import Lower
from django.http import FileResponse
from django.http import HttpResponseBadRequest
from django.http import HttpResponseForbidden
from django.http import HttpResponseNotFound
from django.views.generic import View
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from drf_spectacular.utils import extend_schema_view
from rest_framework.authtoken.models import Token
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.filters import OrderingFilter
from rest_framework.generics import GenericAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import SAFE_METHODS
from rest_framework.permissions import DjangoModelPermissions
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.viewsets import ModelViewSet

from documents.index import DelayedQuery
from documents.permissions import PaperlessObjectPermissions
from documents.tasks import llmindex_index
from paperless.filters import GroupFilterSet
from paperless.filters import UserFilterSet
from paperless.models import ApplicationConfiguration
from paperless.serialisers import ApplicationConfigurationSerializer
from paperless.serialisers import GroupSerializer
from paperless.serialisers import PaperlessAuthTokenSerializer
from paperless.serialisers import ProfileSerializer
from paperless.serialisers import UserSerializer
from paperless_ai.indexing import vector_store_file_exists
from paperless.models import GroupOwnership
from paperless.models import UserOwnership
from paperless.access import audit
from paperless.access import service as access_service
from paperless.access.constants import Action
from paperless.access.policy import Resource
from paperless.access.policy import is_system_admin


class PaperlessObtainAuthTokenView(ObtainAuthToken):
    serializer_class = PaperlessAuthTokenSerializer
    # Public by nature (it is the login), so guard it against password guessing.
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = "token"


class StandardPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100000

    def get_paginated_response(self, data):
        return Response(
            OrderedDict(
                [
                    ("count", self.page.paginator.count),
                    ("next", self.get_next_link()),
                    ("previous", self.get_previous_link()),
                    ("all", self.get_all_result_ids()),
                    ("results", data),
                ],
            ),
        )

    def get_all_result_ids(self):
        query = self.page.paginator.object_list
        if isinstance(query, DelayedQuery):
            try:
                ids = [
                    query.searcher.ixreader.stored_fields(
                        doc_num,
                    )["id"]
                    for doc_num in query.saved_results.get(0).results.docs()
                ]
            except Exception:
                pass
        else:
            ids = self.page.paginator.object_list.values_list("pk", flat=True)
        return ids

    def get_paginated_response_schema(self, schema):
        response_schema = super().get_paginated_response_schema(schema)
        response_schema["properties"]["all"] = {
            "type": "array",
            "example": "[1, 2, 3]",
            "items": {"type": "integer"},
        }
        return response_schema


class FaviconView(View):
    def get(self, request, *args, **kwargs):
        try:
            path = Path(staticfiles_storage.path("paperless/img/favicon.ico"))
            return FileResponse(path.open("rb"), content_type="image/x-icon")
        except FileNotFoundError:
            return HttpResponseNotFound("favicon.ico not found")


def _audit_denied(request, action: str, resource_type: str, resource_id, exc):
    audit.record(
        actor=request.user,
        action=action,
        target_type=resource_type,
        target_id=resource_id,
        outcome="denied",
        after={"reason": str(getattr(exc, "detail", exc))},
        request=request,
    )


class UserViewSet(ModelViewSet):
    """
    Users API. Every decision goes through the central policy engine
    (paperless.access); model-level Django permissions are only one input to
    it. Order inside each write: validate input -> authorize -> write + audit
    in one transaction.
    """

    model = User

    queryset = User.objects.exclude(
        username__in=["consumer", "AnonymousUser"],
    ).order_by(Lower("username"))

    serializer_class = UserSerializer
    pagination_class = StandardPagination
    permission_classes = (IsAuthenticated,)
    filter_backends = (DjangoFilterBackend, OrderingFilter)
    filterset_class = UserFilterSet
    ordering_fields = ("username",)

    def get_queryset(self):
        # Accounts the actor may not read do not exist for them (404, not 403).
        return access_service.readable_users(
            self.request.user,
            super().get_queryset().select_related("ownership"),
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            access_service.check_user_create(request.user, serializer.validated_data)
        except PermissionDenied as exc:
            _audit_denied(request, "user.create", "user", None, exc)
            raise
        self.perform_create(serializer)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def perform_create(self, serializer):
        parent = serializer.validated_data.get("parent") or self.request.user
        with transaction.atomic():
            if settings.AUDIT_LOG_ENABLED:
                from auditlog.context import set_actor

                with set_actor(self.request.user):
                    user = serializer.save()
            else:
                user = serializer.save()
            UserOwnership.objects.update_or_create(
                user=user,
                defaults={"created_by": parent},
            )
            audit.record(
                actor=self.request.user,
                action="user.create",
                target_type="user",
                target_id=user.pk,
                after={
                    "parent": parent.pk,
                    "is_staff": user.is_staff,
                    "is_superuser": user.is_superuser,
                    "groups": sorted(user.groups.values_list("name", flat=True)),
                    "user_permissions": sorted(
                        user.user_permissions.values_list("codename", flat=True),
                    ),
                },
                request=self.request,
            )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        target: User = self.get_object()
        serializer = self.get_serializer(target, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        try:
            changes = access_service.check_user_update(
                request.user,
                target,
                serializer.validated_data,
            )
        except PermissionDenied as exc:
            _audit_denied(request, "user.update", "user", target.pk, exc)
            raise

        with transaction.atomic():
            new_parent = serializer.validated_data.get("parent", None)
            self.perform_update(serializer)
            if "parent" in changes:
                UserOwnership.objects.update_or_create(
                    user=target,
                    defaults={"created_by": new_parent},
                )
            audit.record(
                actor=request.user,
                action="user.privileges.change" if changes else "user.update",
                target_type="user",
                target_id=target.pk,
                after={
                    "fields": sorted(
                        k for k in serializer.validated_data if k != "password"
                    ),
                    "password_changed": bool(
                        (serializer.validated_data.get("password") or "").replace("*", ""),
                    ),
                    **changes,
                },
                request=request,
            )
        return Response(self.get_serializer(target).data)

    def perform_update(self, serializer):
        if settings.AUDIT_LOG_ENABLED:
            from auditlog.context import set_actor

            with set_actor(self.request.user):
                serializer.save()
        else:
            serializer.save()

    def destroy(self, request, *args, **kwargs):
        target = self.get_object()
        try:
            access_service.check_user_delete(request.user, target)
        except PermissionDenied as exc:
            _audit_denied(request, "user.delete", "user", target.pk, exc)
            raise
        with transaction.atomic():
            audit.record(
                actor=request.user,
                action="user.delete",
                target_type="user",
                target_id=target.pk,
                after={"username": target.username},
                request=request,
            )
            from paperless.images.service import delete_images_of_owner

            delete_images_of_owner(actor=request.user, owner=target, request=request)
            self.perform_destroy(target)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        request=None,
        responses={
            200: OpenApiTypes.BOOL,
            404: OpenApiTypes.STR,
        },
    )
    @action(detail=True, methods=["post"])
    def deactivate_totp(self, request, pk=None):
        target = self.get_object()
        try:
            access_service.require(
                request.user,
                Action.MANAGE_SECURITY,
                Resource.user(target),
            )
        except PermissionDenied as exc:
            _audit_denied(request, "user.totp.deactivate", "user", target.pk, exc)
            raise
        authenticator = Authenticator.objects.filter(
            user=target,
            type=Authenticator.Type.TOTP,
        ).first()
        if authenticator is not None:
            delete_and_cleanup(request, authenticator)
            audit.record(
                actor=request.user,
                action="user.totp.deactivate",
                target_type="user",
                target_id=target.pk,
                request=request,
            )
            return Response(data=True)
        else:
            return HttpResponseNotFound("TOTP not found")

    @action(methods=["get"], detail=True, name="User Audit Trail", filter_backends=[])
    def history(self, request, pk=None):
        if not settings.AUDIT_LOG_ENABLED:
            return HttpResponseBadRequest("Audit log is disabled")
        if not request.user.has_perm("auditlog.view_logentry"):
            return HttpResponseForbidden("Insufficient permissions")
        if not request.user.is_staff:
            return HttpResponseForbidden("Staff access required")

        try:
            user = User.objects.get(pk=pk)
        except User.DoesNotExist:
            return HttpResponseNotFound("User not found")

        from auditlog.models import LogEntry

        entries = [
            {
                "id": entry.id,
                "timestamp": entry.timestamp,
                "action": entry.get_action_display(),
                "changes": entry.changes,
                "actor": (
                    {"id": entry.actor.id, "username": entry.actor.username}
                    if entry.actor
                    else None
                ),
            }
            for entry in LogEntry.objects.get_for_object(user).select_related("actor")
        ]

        return Response(sorted(entries, key=lambda x: x["timestamp"], reverse=True))


class GroupViewSet(ModelViewSet):
    model = Group

    queryset = Group.objects.order_by(Lower("name"))

    serializer_class = GroupSerializer
    pagination_class = StandardPagination
    permission_classes = (IsAuthenticated, PaperlessObjectPermissions)
    filter_backends = (DjangoFilterBackend, OrderingFilter)
    filterset_class = GroupFilterSet
    ordering_fields = ("name",)

    def _check_group_ownership(self, request, group):
        if is_system_admin(request.user):
            pass
        else:
            try:
                owner_id = group.ownership.created_by_id
            except GroupOwnership.DoesNotExist:
                owner_id = None
            if owner_id != request.user.id:
                raise PermissionDenied("You can only modify groups you created")
        # Relationship guard: editing or deleting a group that one of my
        # ancestors belongs to would change my father's permissions.
        if not request.user.is_superuser and access_service.ancestors_in_group(
            request.user,
            group,
        ):
            raise PermissionDenied(
                "You cannot modify a group that an account above you belongs to",
            )

    def _check_added_permissions(self, request, validated_data, existing_group=None):
        if request.user.is_superuser or "permissions" not in validated_data:
            return
        added = set(validated_data["permissions"])
        if existing_group is not None:
            added -= set(existing_group.permissions.all())
        missing = sorted(
            p.codename
            for p in added
            if not request.user.has_perm(f"{p.content_type.app_label}.{p.codename}")
        )
        if missing:
            raise PermissionDenied(
                f"Cannot add permissions you do not have: {', '.join(missing)}",
            )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self._check_added_permissions(request, serializer.validated_data)
        self.perform_create(serializer)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def perform_create(self, serializer):
        with transaction.atomic():
            super().perform_create(serializer)
            GroupOwnership.objects.create(
                group=serializer.instance,
                created_by=self.request.user,
            )
            audit.record(
                actor=self.request.user,
                action="group.create",
                target_type="group",
                target_id=serializer.instance.pk,
                after={
                    "permissions": sorted(
                        serializer.instance.permissions.values_list(
                            "codename",
                            flat=True,
                        ),
                    ),
                },
                request=self.request,
            )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        group = self.get_object()
        try:
            self._check_group_ownership(request, group)
        except PermissionDenied as exc:
            _audit_denied(request, "group.update", "group", group.pk, exc)
            raise
        serializer = self.get_serializer(group, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self._check_added_permissions(
            request,
            serializer.validated_data,
            existing_group=group,
        )
        before = set(group.permissions.values_list("codename", flat=True))
        with transaction.atomic():
            self.perform_update(serializer)
            after = set(group.permissions.values_list("codename", flat=True))
            audit.record(
                actor=request.user,
                action="group.update",
                target_type="group",
                target_id=group.pk,
                after={
                    "added": sorted(after - before),
                    "removed": sorted(before - after),
                },
                request=request,
            )
        return Response(serializer.data)

    def perform_update(self, serializer):
        if settings.AUDIT_LOG_ENABLED:
            from auditlog.context import set_actor

            with set_actor(self.request.user):
                serializer.save()
        else:
            serializer.save()

    def destroy(self, request, *args, **kwargs):
        group = self.get_object()
        try:
            self._check_group_ownership(request, group)
        except PermissionDenied as exc:
            _audit_denied(request, "group.delete", "group", group.pk, exc)
            raise
        with transaction.atomic():
            audit.record(
                actor=request.user,
                action="group.delete",
                target_type="group",
                target_id=group.pk,
                after={"name": group.name},
                request=request,
            )
            self.perform_destroy(group)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(methods=["get"], detail=True, name="Group Audit Trail", filter_backends=[])
    def history(self, request, pk=None):
        if not settings.AUDIT_LOG_ENABLED:
            return HttpResponseBadRequest("Audit log is disabled")
        if not request.user.has_perm("auditlog.view_logentry"):
            return HttpResponseForbidden("Insufficient permissions")
        if not request.user.is_staff:
            return HttpResponseForbidden("Staff access required")

        try:
            group = Group.objects.get(pk=pk)
        except Group.DoesNotExist:
            return HttpResponseNotFound("Group not found")

        from auditlog.models import LogEntry

        entries = [
            {
                "id": entry.id,
                "timestamp": entry.timestamp,
                "action": entry.get_action_display(),
                "changes": entry.changes,
                "actor": (
                    {"id": entry.actor.id, "username": entry.actor.username}
                    if entry.actor
                    else None
                ),
            }
            for entry in LogEntry.objects.get_for_object(group).select_related("actor")
        ]

        return Response(sorted(entries, key=lambda x: x["timestamp"], reverse=True))


class ProfileView(GenericAPIView):
    """
    User profile view, only available when logged in
    """

    permission_classes = [IsAuthenticated]
    serializer_class = ProfileSerializer

    def get(self, request, *args, **kwargs):
        user = self.request.user

        serializer = self.get_serializer(data=request.data)
        return Response(serializer.to_representation(user))

    def patch(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = self.request.user if hasattr(self.request, "user") else None

        password = serializer.validated_data.pop("password", None)
        if password and password.replace("*", ""):
            user.set_password(password)
            user.save()

        for key, value in serializer.validated_data.items():
            setattr(user, key, value)
        user.save()

        return Response(serializer.to_representation(user))


@extend_schema_view(
    get=extend_schema(
        responses={
            (200, "application/json"): OpenApiTypes.OBJECT,
        },
    ),
    post=extend_schema(
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    "secret": {"type": "string"},
                    "code": {"type": "string"},
                },
                "required": ["secret", "code"],
            },
        },
        responses={
            (200, "application/json"): OpenApiTypes.OBJECT,
        },
    ),
    delete=extend_schema(
        responses={
            (200, "application/json"): OpenApiTypes.BOOL,
            404: OpenApiTypes.STR,
        },
    ),
)
class TOTPView(GenericAPIView):
    """
    TOTP views
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        """
        Generates a new TOTP secret and returns the URL and SVG
        """
        user = self.request.user
        mfa_adapter = get_mfa_adapter()
        secret = totp_auth.get_totp_secret(regenerate=True)
        url = mfa_adapter.build_totp_url(user, secret)
        svg = mfa_adapter.build_totp_svg(url)
        return Response(
            {
                "url": url,
                "qr_svg": svg,
                "secret": secret,
            },
        )

    def post(self, request, *args, **kwargs):
        """
        Validates a TOTP code and activates the TOTP authenticator
        """
        valid = totp_auth.validate_totp_code(
            request.data["secret"],
            request.data["code"],
        )
        recovery_codes = None
        if valid:
            auth = totp_auth.TOTP.activate(
                request.user,
                request.data["secret"],
            ).instance
            signals.authenticator_added.send(
                sender=Authenticator,
                request=request,
                user=request.user,
                authenticator=auth,
            )
            rc_auth: Authenticator = auto_generate_recovery_codes(request)
            if rc_auth:
                recovery_codes = rc_auth.wrap().get_unused_codes()
        return Response(
            {
                "success": valid,
                "recovery_codes": recovery_codes,
            },
        )

    def delete(self, request, *args, **kwargs):
        """
        Deactivates the TOTP authenticator
        """
        user = self.request.user
        authenticator = Authenticator.objects.filter(
            user=user,
            type=Authenticator.Type.TOTP,
        ).first()
        if authenticator is not None:
            delete_and_cleanup(request, authenticator)
            return Response(data=True)
        else:
            return HttpResponseNotFound("TOTP not found")


@extend_schema_view(
    post=extend_schema(
        request={
            "application/json": None,
        },
        responses={
            (200, "application/json"): OpenApiTypes.STR,
        },
    ),
)
class GenerateAuthTokenView(GenericAPIView):
    """
    Generates (or re-generates) an auth token, requires a logged in user
    unlike the default DRF endpoint
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        user = self.request.user

        existing_token = Token.objects.filter(user=user).first()
        if existing_token is not None:
            existing_token.delete()
        token = Token.objects.create(user=user)
        return Response(
            token.key,
        )


@extend_schema_view(
    list=extend_schema(
        description="Get the application configuration",
        external_docs={
            "description": "Application Configuration",
            "url": "",
        },
    ),
)
class ApplicationConfigurationViewSet(ModelViewSet):
    model = ApplicationConfiguration

    queryset = ApplicationConfiguration.objects

    serializer_class = ApplicationConfigurationSerializer
    permission_classes = (IsAuthenticated,)

    def get_permissions(self):
        if self.request.method in SAFE_METHODS:
            return [IsAuthenticated()]
        return [IsAuthenticated(), DjangoModelPermissions()]

    def get_queryset(self):
        # There is exactly one configuration row. The initial migration creates
        # it, but recreate it rather than leave the settings page unusable if it
        # was ever removed.
        if not ApplicationConfiguration.objects.exists():
            ApplicationConfiguration.objects.create()
        return ApplicationConfiguration.objects.all()

    def get_object(self):
        # A singleton has no meaningful id in the URL. Ignoring it means a
        # client that lost the id (e.g. PATCH /api/config/null/) still saves.
        instance = self.get_queryset().first()
        self.check_object_permissions(self.request, instance)
        return instance

    @extend_schema(exclude=True)
    def create(self, request, *args, **kwargs):
        return Response(status=405)  # Not Allowed

    def perform_update(self, serializer):
        old_instance = ApplicationConfiguration.objects.all().first()
        old_ai_index_enabled = (
            old_instance.ai_enabled and old_instance.llm_embedding_backend
        )

        new_instance: ApplicationConfiguration = serializer.save()
        new_ai_index_enabled = (
            new_instance.ai_enabled and new_instance.llm_embedding_backend
        )

        if (
            not old_ai_index_enabled
            and new_ai_index_enabled
            and not vector_store_file_exists()
        ):
            # AI index was just enabled and vector store file does not exist
            llmindex_index.delay(
                progress_bar_disable=True,
                rebuild=True,
                scheduled=False,
                auto=True,
            )


@extend_schema_view(
    post=extend_schema(
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                },
                "required": ["id"],
            },
        },
        responses={
            (200, "application/json"): OpenApiTypes.INT,
            400: OpenApiTypes.STR,
        },
    ),
)
class DisconnectSocialAccountView(GenericAPIView):
    """
    Disconnects a social account provider from the user account
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        user = self.request.user

        try:
            account = user.socialaccount_set.get(pk=request.data["id"])
            account_id = account.id
            account.delete()
            return Response(account_id)
        except SocialAccount.DoesNotExist:
            return HttpResponseBadRequest("Social account not found")


@extend_schema_view(
    get=extend_schema(
        responses={
            (200, "application/json"): OpenApiTypes.OBJECT,
        },
    ),
)
class SocialAccountProvidersView(GenericAPIView):
    """
    List of social account providers
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        adapter = get_adapter()
        providers = adapter.list_providers(request)
        resp = [
            {"name": p.name, "login_url": p.get_login_url(request, process="connect")}
            for p in providers
            if p.id != "openid"
        ]

        for openid_provider in filter(lambda p: p.id == "openid", providers):
            resp += [
                {
                    "name": b["name"],
                    "login_url": openid_provider.get_login_url(
                        request,
                        process="connect",
                        openid=b["openid_url"],
                    ),
                }
                for b in openid_provider.get_brands()
            ]

        return Response(sorted(resp, key=lambda p: p["name"]))
