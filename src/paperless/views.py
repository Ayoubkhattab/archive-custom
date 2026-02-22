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
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter
from rest_framework.generics import GenericAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import SAFE_METHODS
from rest_framework.permissions import DjangoModelPermissions
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
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
from paperless.models import ApplicationConfiguration, UserOwnership, GroupOwnership


class PaperlessObtainAuthTokenView(ObtainAuthToken):
    serializer_class = PaperlessAuthTokenSerializer


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


class UserViewSet(ModelViewSet):
    model = User

    queryset = User.objects.exclude(
        username__in=["consumer", "AnonymousUser"],
    ).order_by(Lower("username"))

    serializer_class = UserSerializer
    pagination_class = StandardPagination
    permission_classes = (IsAuthenticated, PaperlessObjectPermissions)
    filter_backends = (DjangoFilterBackend, OrderingFilter)
    filterset_class = UserFilterSet
    ordering_fields = ("username",)

    def _validate_privilege_escalation(self, request, existing_user=None):
        """
        Validate that a non-superuser is not escalating privileges beyond
        what they themselves have. For updates, only NEWLY ADDED permissions
        and groups are checked (existing ones are preserved).
        Returns HttpResponseForbidden if violation detected, None otherwise.
        """
        if request.user.is_superuser:
            return None

        # Check is_superuser
        if request.data.get("is_superuser") is True:
            return HttpResponseForbidden(
                "Superuser status can only be granted by a superuser",
            )

        # Check is_staff
        if existing_user is None:
            # CREATE: block if trying to set is_staff=True
            if request.data.get("is_staff") is True:
                return HttpResponseForbidden(
                    "Staff status can only be granted by a superuser",
                )
        else:
            # UPDATE: block only if CHANGING is_staff
            if (
                request.data.get("is_staff") is not None
                and request.data.get("is_staff") != existing_user.is_staff
            ):
                return HttpResponseForbidden(
                    "Staff status can only be changed by a superuser",
                )

        # Check user_permissions - only block ADDING permissions the requester doesn't have
        requested_perms = set(request.data.get("user_permissions", []) or [])
        if requested_perms:
            if existing_user is not None:
                current_perms = set(
                    existing_user.user_permissions.values_list("codename", flat=True),
                )
                new_perms = requested_perms - current_perms
            else:
                new_perms = requested_perms

            if new_perms:
                my_codenames = {
                    p.split(".")[-1] for p in request.user.get_all_permissions()
                }
                extra_perms = new_perms - my_codenames
                if extra_perms:
                    return HttpResponseForbidden(
                        f"Cannot grant permissions you do not have: "
                        f"{', '.join(sorted(extra_perms))}",
                    )

        # Check groups - only block ADDING groups the requester doesn't belong to
        raw_groups = request.data.get("groups", []) or []
        requested_groups = {int(g) for g in raw_groups}
        if requested_groups:
            if existing_user is not None:
                current_groups = set(
                    existing_user.groups.values_list("id", flat=True),
                )
                new_groups = requested_groups - current_groups
            else:
                new_groups = requested_groups

            if new_groups:
                my_group_ids = set(
                    request.user.groups.values_list("id", flat=True),
                )
                extra_groups = new_groups - my_group_ids
                if extra_groups:
                    return HttpResponseForbidden(
                        "Cannot assign groups you do not belong to",
                    )

        return None

    def _check_user_ownership(self, request, user_to_modify):
        if request.user.is_superuser:
            return None
        try:
            if user_to_modify.ownership.created_by_id == request.user.id:
                return None
        except UserOwnership.DoesNotExist:
            pass
        return HttpResponseForbidden("You can only modify users you created")

    def create(self, request, *args, **kwargs):
        forbidden = self._validate_privilege_escalation(request, existing_user=None)
        if forbidden:
            return forbidden
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        # أولاً قم بإنشاء الـ user باستخدام الطريقة الافتراضية
        super().perform_create(serializer)
        
        # ثم قم بإنشاء الـ ownership record
        UserOwnership.objects.create(
            user=serializer.instance, 
            created_by=self.request.user
        )
        
        # audit log code موجود مسبقاً في super().perform_create

    def perform_update(self, serializer):
        if settings.AUDIT_LOG_ENABLED:
            from auditlog.context import set_actor

            with set_actor(self.request.user):
                serializer.save()
        else:
            serializer.save()

    def destroy(self, request, *args, **kwargs):
        user_to_delete = self.get_object()
        
        # منع حذف superuser إلا بواسطة superuser آخر
        if user_to_delete.is_superuser and not request.user.is_superuser:
            return HttpResponseForbidden(
                "Superuser accounts can only be deleted by other superusers"
            )
        
        # منع المستخدم من حذف نفسه
        if user_to_delete.id == request.user.id:
            return HttpResponseForbidden("Users cannot delete their own accounts")
        
        # Check user ownership
        forbidden = self._check_user_ownership(request, user_to_delete)
        if forbidden:
            return forbidden
        
        return super().destroy(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        user_to_update: User = self.get_object()

        # Block non-superusers from editing superuser accounts entirely
        if not request.user.is_superuser and user_to_update.is_superuser:
            return HttpResponseForbidden(
                "Superusers can only be modified by other superusers",
            )

        # Validate privilege escalation (is_superuser, is_staff, permissions, groups)
        forbidden = self._validate_privilege_escalation(
            request,
            existing_user=user_to_update,
        )
        if forbidden:
            return forbidden
        
        # Check user ownership
        forbidden = self._check_user_ownership(request, user_to_update)
        if forbidden:
            return forbidden

        return super().update(request, *args, **kwargs)

    @extend_schema(
        request=None,
        responses={
            200: OpenApiTypes.BOOL,
            404: OpenApiTypes.STR,
        },
    )
    @action(detail=True, methods=["post"])
    def deactivate_totp(self, request, pk=None):
        request_user = request.user
        user = User.objects.get(pk=pk)
        if not request_user.is_superuser and request_user != user:
            return HttpResponseForbidden(
                "You do not have permission to deactivate TOTP for this user",
            )
        authenticator = Authenticator.objects.filter(
            user=user,
            type=Authenticator.Type.TOTP,
        ).first()
        if authenticator is not None:
            delete_and_cleanup(request, authenticator)
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

    def _validate_group_permissions(self, request, existing_group=None):
        if request.user.is_superuser:
            return None
        requested_perms = set(request.data.get("permissions", []) or [])
        if not requested_perms:
            return None
        if existing_group is not None:
            current_perms = set(
                existing_group.permissions.values_list("codename", flat=True)
            )
            new_perms = requested_perms - current_perms
        else:
            new_perms = requested_perms
        if new_perms:
            my_codenames = {
                p.split(".")[-1] for p in request.user.get_all_permissions()
            }
            extra_perms = new_perms - my_codenames
            if extra_perms:
                return HttpResponseForbidden(
                    f"Cannot add permissions you do not have: "
                    f"{', '.join(sorted(extra_perms))}"
                )
        return None

    def _check_group_ownership(self, request, group):
        if request.user.is_superuser:
            return None
        try:
            if group.ownership.created_by_id == request.user.id:
                return None
        except GroupOwnership.DoesNotExist:
            pass
        return HttpResponseForbidden("You can only modify groups you created")

    def create(self, request, *args, **kwargs):
        forbidden = self._validate_group_permissions(request)
        if forbidden:
            return forbidden
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        # أولاً قم بإنشاء الـ group باستخدام الطريقة الافتراضية
        super().perform_create(serializer)
        
        # ثم قم بإنشاء الـ ownership record
        GroupOwnership.objects.create(
            group=serializer.instance, 
            created_by=self.request.user
        )
        
        # audit log code موجود مسبقاً في super().perform_create

    def update(self, request, *args, **kwargs):
        group = self.get_object()
        forbidden = self._check_group_ownership(request, group)
        if forbidden:
            return forbidden
        forbidden = self._validate_group_permissions(request, existing_group=group)
        if forbidden:
            return forbidden
        return super().update(request, *args, **kwargs)

    def perform_update(self, serializer):
        if settings.AUDIT_LOG_ENABLED:
            from auditlog.context import set_actor

            with set_actor(self.request.user):
                serializer.save()
        else:
            serializer.save()

    def destroy(self, request, *args, **kwargs):
        group = self.get_object()
        forbidden = self._check_group_ownership(request, group)
        if forbidden:
            return forbidden
        return super().destroy(request, *args, **kwargs)

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
