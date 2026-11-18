"""
Hierarchical permission tests (policy engine + users API + Django admin).

Hierarchy used throughout:

    grandfather
      └── father
            └── son
                  └── grandson
    stranger (unrelated)
    admin    (system.manage.all, not a Django superuser)
"""

from django.contrib.auth.models import Group
from django.contrib.auth.models import Permission
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from paperless.access import constants as C
from paperless.access.backends import SystemManageAllBackend
from paperless.access.policy import Resource
from paperless.access.policy import can
from paperless.access.policy import decide
from paperless.models import SecurityAuditLog
from paperless.models import UserOwnership

ENDPOINT = "/api/users/"


def grant(user: User, *perms: str) -> User:
    """Grant dotted policy permissions or Django 'app.codename' permissions."""
    for perm in perms:
        if "." in perm and perm.split(".")[0] in {"auth", "documents", "auditlog"}:
            app_label, codename = perm.split(".", 1)
        else:
            app_label, codename = C.APP_LABEL, C.codename(perm)
        user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label=app_label,
                codename=codename,
            ),
        )
    # Drop Django's permission caches.
    return User.objects.get(pk=user.pk)


def child_of(parent: User | None, username: str) -> User:
    user = User.objects.create_user(username=username, password="x" * 12)
    UserOwnership.objects.create(user=user, created_by=parent)
    return user


class HierarchyMixin:
    def setUp(self):
        super().setUp()
        self.grandfather = child_of(None, "grandfather")
        self.father = child_of(self.grandfather, "father")
        self.son = child_of(self.father, "son")
        self.grandson = child_of(self.son, "grandson")
        self.stranger = child_of(None, "stranger")
        self.admin = grant(child_of(None, "admin"), C.SYSTEM_MANAGE_ALL)


class TestPolicyEngine(HierarchyMixin, TestCase):
    def test_son_with_any_permissions_cannot_mutate_ancestors(self):
        son = grant(self.son, C.USER_UPDATE_ANY, C.USER_DELETE_ANY, C.USER_READ_ANY)
        for ancestor in (self.father, self.grandfather):
            for action in (
                C.Action.UPDATE,
                C.Action.DELETE,
                C.Action.CHANGE_PRIVILEGES,
            ):
                decision = decide(
                    actor=son,
                    action=action,
                    resource=Resource.user(ancestor),
                )
                self.assertFalse(decision.allowed, (ancestor, action))
                self.assertEqual(decision.rule, "descendant-to-ancestor")
            self.assertFalse(
                can(
                    actor=son,
                    action=C.Action.CHANGE_RELATIONSHIP,
                    resource=Resource.user(ancestor),
                ),
            )
            self.assertFalse(
                can(
                    actor=son,
                    action=C.Action.MANAGE_SECURITY,
                    resource=Resource.user(ancestor),
                ),
            )

    def test_son_with_any_can_still_read_father(self):
        son = grant(self.son, C.USER_READ_ANY)
        self.assertTrue(
            can(actor=son, action=C.Action.READ, resource=Resource.user(self.father)),
        )

    def test_child_scope_covers_direct_children_only(self):
        father = grant(self.father, C.USER_UPDATE_CHILD)
        self.assertTrue(
            can(actor=father, action=C.Action.UPDATE, resource=Resource.user(self.son)),
        )
        self.assertFalse(
            can(
                actor=father,
                action=C.Action.UPDATE,
                resource=Resource.user(self.grandson),
            ),
        )

    def test_unrelated_requires_any(self):
        stranger = self.stranger
        self.assertFalse(
            can(
                actor=stranger,
                action=C.Action.UPDATE,
                resource=Resource.user(self.son),
            ),
        )
        stranger = grant(stranger, C.USER_UPDATE_ANY)
        self.assertTrue(
            can(
                actor=stranger,
                action=C.Action.UPDATE,
                resource=Resource.user(self.son),
            ),
        )

    def test_global_admin_allowed_everywhere_but_not_on_superusers(self):
        root = User.objects.create_superuser(username="root")
        for target in (self.grandfather, self.son, self.stranger):
            self.assertTrue(
                can(
                    actor=self.admin,
                    action=C.Action.UPDATE,
                    resource=Resource.user(target),
                ),
            )
        self.assertFalse(
            can(actor=self.admin, action=C.Action.UPDATE, resource=Resource.user(root)),
        )

    def test_nobody_escalates_themselves(self):
        for actor in (self.son, self.admin):
            self.assertFalse(
                can(
                    actor=actor,
                    action=C.Action.CHANGE_PRIVILEGES,
                    resource=Resource.user(actor),
                ),
            )
            self.assertFalse(
                can(actor=actor, action=C.Action.DELETE, resource=Resource.user(actor)),
            )

    def test_inactive_and_anonymous_are_denied(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertFalse(
            can(
                actor=AnonymousUser(),
                action=C.Action.READ,
                resource=Resource.user(self.son),
            ),
        )
        self.father.is_active = False
        self.father.save()
        father = grant(self.father, C.USER_UPDATE_CHILD)
        self.assertFalse(
            can(actor=father, action=C.Action.UPDATE, resource=Resource.user(self.son)),
        )

    def test_hierarchy_cycles_do_not_hang_or_grant(self):
        # Corrupt data: grandfather's parent is the son.
        UserOwnership.objects.filter(user=self.grandfather).update(created_by=self.son)
        self.assertFalse(
            can(
                actor=self.grandfather,
                action=C.Action.UPDATE,
                resource=Resource.user(self.stranger),
            ),
        )

    def test_global_admin_never_gets_audit_log_write_permissions(self):
        backend = SystemManageAllBackend()
        self.assertTrue(backend.has_perm(self.admin, "documents.change_document"))
        self.assertFalse(backend.has_perm(self.admin, "auditlog.delete_logentry"))
        self.assertFalse(
            backend.has_perm(self.admin, "paperless.change_securityauditlog"),
        )
        self.assertFalse(backend.has_perm(self.son, "documents.change_document"))


class TestUsersApiHierarchy(HierarchyMixin, APITestCase):
    def patch(self, actor, target, data):
        self.client.force_authenticate(actor)
        return self.client.patch(f"{ENDPOINT}{target.pk}/", data, format="json")

    def test_son_edits_father_403(self):
        son = grant(self.son, C.USER_READ_ANY, C.USER_UPDATE_ANY)
        response = self.patch(son, self.father, {"first_name": "hacked"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.father.refresh_from_db()
        self.assertEqual(self.father.first_name, "")

    def test_son_deletes_father_403(self):
        son = grant(self.son, C.USER_READ_ANY, C.USER_DELETE_ANY, "auth.delete_user")
        self.client.force_authenticate(son)
        response = self.client.delete(f"{ENDPOINT}{self.father.pk}/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(User.objects.filter(pk=self.father.pk).exists())

    def test_son_changes_father_parent_403(self):
        son = grant(self.son, C.USER_READ_ANY, C.USER_UPDATE_ANY)
        response = self.patch(son, self.father, {"parent": self.son.pk})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            UserOwnership.objects.get(user=self.father).created_by,
            self.grandfather,
        )

    def test_son_changes_his_own_role_403(self):
        group = Group.objects.create(name="powerful")
        group.permissions.add(Permission.objects.get(codename="delete_document"))
        son = grant(self.son, C.USER_READ_ANY, C.USER_UPDATE_ANY)
        son.groups.add(
            group,
        )  # even a group he belongs to cannot be self-assigned again
        for data in (
            {"is_staff": True},
            {"is_superuser": True},
            {"user_permissions": ["delete_document"]},
            {"groups": []},
            {"parent": None},
        ):
            response = self.patch(son, son, data)
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, data)

    def test_son_can_update_own_profile_fields(self):
        response = self.patch(self.son, self.son, {"first_name": "Me"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_father_edits_direct_child_with_update_child(self):
        father = grant(self.father, C.USER_READ_CHILD, C.USER_UPDATE_CHILD)
        response = self.patch(father, self.son, {"first_name": "Junior"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.son.refresh_from_db()
        self.assertEqual(self.son.first_name, "Junior")

    def test_father_missing_permission_403(self):
        father = grant(self.father, C.USER_READ_CHILD)
        response = self.patch(father, self.son, {"first_name": "Junior"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_father_cannot_grant_what_he_does_not_have(self):
        father = grant(self.father, C.USER_READ_CHILD, C.USER_UPDATE_CHILD)
        response = self.patch(
            father,
            self.son,
            {"user_permissions": ["delete_document"]},
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        father = grant(father, "documents.delete_document")
        response = self.patch(
            father,
            self.son,
            {"user_permissions": ["delete_document"]},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_global_admin_edits_anyone(self):
        for target in (self.grandfather, self.son, self.stranger):
            response = self.patch(self.admin, target, {"last_name": "edited"})
            self.assertEqual(response.status_code, status.HTTP_200_OK, target)

    def test_global_admin_can_reparent_but_not_into_a_cycle(self):
        response = self.patch(self.admin, self.son, {"parent": self.stranger.pk})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            UserOwnership.objects.get(user=self.son).created_by,
            self.stranger,
        )

        response = self.patch(self.admin, self.son, {"parent": self.grandson.pk})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unrelated_user_edit_403(self):
        stranger = grant(self.stranger, C.USER_READ_ANY)
        response = self.patch(stranger, self.son, {"first_name": "x"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unreadable_user_is_404_not_403(self):
        response = self.patch(self.stranger, self.son, {"first_name": "x"})
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_list_is_scoped(self):
        father = grant(self.father, C.USER_READ_CHILD)
        self.client.force_authenticate(father)
        response = self.client.get(ENDPOINT)
        names = {u["username"] for u in response.data["results"]}
        self.assertEqual(names, {"father", "son"})

    def test_create_makes_a_child_and_ignores_client_parent(self):
        father = grant(self.father, C.USER_CREATE_CHILD)
        self.client.force_authenticate(father)
        response = self.client.post(
            ENDPOINT,
            {"username": "newkid", "parent": self.stranger.pk},
            format="json",
        )
        # choosing another parent needs system.manage.all
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        response = self.client.post(ENDPOINT, {"username": "newkid"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        new = User.objects.get(username="newkid")
        self.assertEqual(new.ownership.created_by, self.father)

    def test_sensitive_changes_are_audited_and_immutable(self):
        self.patch(self.admin, self.son, {"is_staff": True})
        entry = SecurityAuditLog.objects.get(action="user.privileges.change")
        self.assertEqual(entry.actor_id, self.admin.pk)
        self.assertEqual(entry.after_json["is_staff"], {"from": False, "to": True})

        son = grant(self.son, C.USER_READ_ANY, C.USER_UPDATE_ANY)
        self.patch(son, self.father, {"first_name": "x"})
        self.assertTrue(
            SecurityAuditLog.objects.filter(
                action="user.update",
                outcome="denied",
            ).exists(),
        )

        entry.action = "tampered"
        with self.assertRaises(PermissionError):
            entry.save()
        with self.assertRaises(PermissionError):
            entry.delete()
        with self.assertRaises(PermissionError):
            SecurityAuditLog.objects.all().delete()
        with self.assertRaises(PermissionError):
            SecurityAuditLog.objects.update(action="x")

    def test_audit_log_api_is_admin_only_and_read_only(self):
        self.client.force_authenticate(self.son)
        self.assertEqual(self.client.get("/api/audit_logs/").status_code, 403)
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get("/api/audit_logs/").status_code, 200)
        self.assertEqual(self.client.post("/api/audit_logs/", {}).status_code, 405)

    def test_son_cannot_disable_fathers_2fa(self):
        son = grant(self.son, C.USER_READ_ANY)
        self.client.force_authenticate(son)
        response = self.client.post(f"{ENDPOINT}{self.father.pk}/deactivate_totp/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_son_cannot_alter_a_group_his_father_is_in(self):
        son = grant(
            self.son,
            "auth.add_group",
            "auth.change_group",
            "auth.view_group",
            "auth.delete_group",
        )
        self.client.force_authenticate(son)
        group = self.client.post(
            "/api/groups/",
            {"name": "g", "permissions": []},
            format="json",
        )
        # created by son, but father joins it
        group_obj = Group.objects.get(pk=group.data["id"])
        self.father.groups.add(group_obj)
        response = self.client.delete(f"/api/groups/{group_obj.pk}/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_ui_settings_mirror_access(self):
        self.client.force_authenticate(self.son)
        user = self.client.get("/api/ui_settings/").data["user"]
        self.assertFalse(user["is_system_admin"])
        self.assertIn(C.IMAGE_UPLOAD_SELF, user["access"])
        self.assertEqual(user["parent"], self.father.pk)
        self.assertEqual(user["ancestors"], [self.father.pk, self.grandfather.pk])


class TestDjangoAdminRespectsHierarchy(HierarchyMixin, TestCase):
    def test_son_cannot_edit_father_in_admin(self):
        son = grant(self.son, "auth.change_user", "auth.view_user", C.USER_UPDATE_ANY)
        son.is_staff = True
        son.save()
        self.client.force_login(son)
        response = self.client.post(
            f"/admin/auth/user/{self.father.pk}/change/",
            {"username": "father", "first_name": "hacked"},
        )
        self.assertIn(response.status_code, (302, 403))
        self.father.refresh_from_db()
        self.assertEqual(self.father.first_name, "")


class TestProtectedSeedAdmin(APITestCase):
    """The admin seeded on first start-up is hidden and immutable."""

    def setUp(self):
        from paperless.access.protected import protect_seed_admin

        self.admin = User.objects.create_superuser(username="admin", password="x" * 12)
        self.assertTrue(protect_seed_admin("admin"))
        self.other_root = User.objects.create_superuser(username="root2")
        self.global_admin = grant(child_of(None, "ga"), C.SYSTEM_MANAGE_ALL)

    def test_hidden_from_users_list_for_everyone(self):
        for actor in (self.other_root, self.global_admin, self.admin):
            self.client.force_authenticate(actor)
            names = {u["username"] for u in self.client.get(ENDPOINT).data["results"]}
            self.assertNotIn("admin", names, actor)
            self.assertEqual(
                self.client.get(f"{ENDPOINT}{self.admin.pk}/").status_code,
                status.HTTP_404_NOT_FOUND,
            )

    def test_nobody_can_modify_or_delete_it(self):
        for actor in (self.other_root, self.global_admin):
            self.client.force_authenticate(actor)
            r = self.client.patch(
                f"{ENDPOINT}{self.admin.pk}/", {"first_name": "x"}, format="json"
            )
            self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)
            r = self.client.delete(f"{ENDPOINT}{self.admin.pk}/")
            self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)
            r = self.client.post(f"{ENDPOINT}{self.admin.pk}/deactivate_totp/")
            self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(User.objects.filter(pk=self.admin.pk, first_name="").exists())

    def test_policy_denies_every_mutation_even_for_superusers(self):
        for actor in (self.other_root, self.global_admin, self.admin):
            for action in (
                C.Action.UPDATE,
                C.Action.DELETE,
                C.Action.CHANGE_PRIVILEGES,
                C.Action.CHANGE_RELATIONSHIP,
            ):
                decision = decide(
                    actor=actor, action=action, resource=Resource.user(self.admin)
                )
                self.assertFalse(decision.allowed, (actor, action))
                self.assertEqual(decision.rule, "protected-account")

    def test_hidden_and_locked_in_django_admin(self):
        self.client.force_login(self.other_root)
        listing = self.client.get("/admin/auth/user/")
        self.assertEqual(listing.status_code, 200)
        self.assertNotContains(listing, f"/admin/auth/user/{self.admin.pk}/change/")
        response = self.client.post(
            f"/admin/auth/user/{self.admin.pk}/change/",
            {"username": "admin", "first_name": "hacked"},
        )
        self.assertNotEqual(response.status_code, 200)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.first_name, "")

    def test_cannot_be_chosen_as_image_owner(self):
        self.client.force_authenticate(self.other_root)
        from django.core.files.uploadedfile import SimpleUploadedFile

        r = self.client.post(
            "/api/images/",
            {"file": SimpleUploadedFile("a.jpg", b"x"), "owner": self.admin.pk},
            format="multipart",
        )
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_the_admin_still_uses_its_own_profile(self):
        self.client.force_authenticate(self.admin)
        r = self.client.patch("/api/profile/", {"first_name": "Root"}, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.client.force_authenticate(self.admin)
        self.assertTrue(
            self.client.get("/api/ui_settings/").data["user"]["is_superuser"]
        )


class TestSeedCommandProtectsAdmin(TestCase):
    def test_manage_superuser_creates_a_protected_admin(self):
        import os
        from io import StringIO
        from unittest import mock

        from django.core.management import call_command

        from paperless.access.protected import is_protected

        env = {
            "PAPERLESS_ADMIN_USER": "seedadmin",
            "PAPERLESS_ADMIN_PASSWORD": "pw" * 8,
        }
        with mock.patch.dict(os.environ, env):
            call_command("manage_superuser", stdout=StringIO())
        user = User.objects.get(username="seedadmin")
        self.assertTrue(user.is_superuser)
        self.assertTrue(is_protected(user))

    def test_rerun_protects_an_admin_seeded_before_this_feature(self):
        import os
        from io import StringIO
        from unittest import mock

        from django.core.management import call_command

        from paperless.access.protected import is_protected

        user = User.objects.create_superuser(username="oldadmin")
        self.assertFalse(is_protected(user))
        with mock.patch.dict(os.environ, {"PAPERLESS_ADMIN_USER": "oldadmin"}):
            call_command("manage_superuser", stdout=StringIO())
        self.assertTrue(is_protected(user))

    def test_a_regular_user_with_that_name_is_not_protected(self):
        from paperless.access.protected import is_protected
        from paperless.access.protected import protect_seed_admin

        user = User.objects.create_user(username="admin")
        self.assertFalse(protect_seed_admin("admin"))
        self.assertFalse(is_protected(user))
