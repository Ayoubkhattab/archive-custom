from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from rest_framework.exceptions import PermissionDenied

from paperless.access import service as access_service
from paperless.access.constants import Action
from paperless.access.policy import Resource
from paperless.access.policy import can


class PaperlessUserForm(forms.ModelForm):
    """
    User form for the Django admin. Authorization goes through the same
    policy engine as the REST API, so the admin is not a way around the
    hierarchy (a son cannot edit his father here either) or around the
    privilege escalation rules.
    """

    class Meta:
        model = User
        fields = [
            "username",
            "first_name",
            "last_name",
            "email",
            "is_staff",
            "is_active",
            "is_superuser",
            "groups",
            "user_permissions",
        ]

    def clean(self):
        cleaned_data = super().clean()
        actor = self.request.user
        data = {
            key: (set(value) if key in ("groups", "user_permissions") else value)
            for key, value in cleaned_data.items()
            if key in ("is_staff", "is_superuser", "groups", "user_permissions")
        }
        try:
            if self.instance.pk:
                access_service.check_user_update(actor, self.instance, data)
            else:
                access_service.check_user_create(actor, data)
        except PermissionDenied as exc:
            raise forms.ValidationError(str(exc.detail)) from exc
        return cleaned_data


class PaperlessUserAdmin(UserAdmin):
    form = PaperlessUserForm

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        form.request = request
        return form

    def get_queryset(self, request):
        # The seeded admin is not listed here either.
        from paperless.access.protected import exclude_protected

        return exclude_protected(super().get_queryset(request))

    def has_change_permission(self, request, obj=None):
        if obj is None:
            return super().has_change_permission(request, obj)
        return can(
            actor=request.user,
            action=Action.UPDATE,
            resource=Resource.user(obj),
        )

    def has_delete_permission(self, request, obj=None):
        if obj is None:
            return super().has_delete_permission(request, obj)
        return can(
            actor=request.user,
            action=Action.DELETE,
            resource=Resource.user(obj),
        )

    def delete_queryset(self, request, queryset):
        # Bulk "delete selected" must respect the policy for every row.
        for user in queryset:
            if not self.has_delete_permission(request, user):
                raise PermissionDenied(
                    "You do not have permission to delete one of these users",
                )
        super().delete_queryset(request, queryset)


admin.site.unregister(User)
admin.site.register(User, PaperlessUserAdmin)
