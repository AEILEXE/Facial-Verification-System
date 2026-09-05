from django.contrib import admin
from django.contrib.admin.utils import unquote
from django.contrib.auth.admin import UserAdmin
from django.core.exceptions import PermissionDenied
from .models import CustomUser


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    list_display = ['username', 'get_full_name', 'role', 'employee_id', 'is_active']
    list_filter = ['role', 'is_active']
    fieldsets = UserAdmin.fieldsets + (
        ('FANS Info', {'fields': ('role', 'employee_id', 'phone')}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('FANS Info', {'fields': ('role', 'employee_id', 'phone')}),
    )

    # v2.1.16 (Security Hardening #2 — CRITICAL, extended in Round #3 —
    # Blocker 4): the Django admin bypasses every app-level role-hierarchy
    # guard in accounts/views.py entirely — a staff-flagged Admin/IT session
    # could otherwise use /admin/ to change a President account's role,
    # active flag, staff/superuser flags, email/recovery address, or
    # group/permission assignments with no audit trail. Make those fields
    # read-only here whenever the target is a President and the acting user
    # is not themselves President. 'password' is not listed here because
    # UserAdmin never renders it as an editable field on the change form
    # (it's a read-only hash display + a link to a separate password-change
    # view) — that view is blocked outright in user_change_password() below.
    PRESIDENT_PROTECTED_FIELDS = (
        'role', 'is_active', 'is_staff', 'is_superuser',
        'email', 'groups', 'user_permissions',
    )

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if obj is not None and obj.is_president and not getattr(request.user, 'is_president', False):
            readonly.extend(f for f in self.PRESIDENT_PROTECTED_FIELDS if f not in readonly)
        return readonly

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.is_president and not getattr(request.user, 'is_president', False):
            return False
        return super().has_delete_permission(request, obj)

    # v2.1.16 Security Hardening Round #4 (Blocker 2 — complete redesign):
    # PRESIDENT_PROTECTED_FIELDS/get_readonly_fields() above only locks a
    # named SUBSET of fields (role, is_active, is_staff, is_superuser,
    # email, groups, user_permissions). Every other field CustomUserAdmin's
    # fieldsets render for an existing user — username, first_name,
    # last_name, employee_id, phone — was still fully editable by a
    # non-President admin submitting a POST to the change view, since
    # get_readonly_fields() only affects which fields the FORM instance
    # binds as read-only, not whether the change view accepts the POST at
    # all. That is a real attack surface: e.g. changing the President's
    # `phone` (an OTP delivery channel) or `username` (impersonation/lookup
    # confusion) needs no field to be individually enumerated as
    # "security-sensitive" to be dangerous. has_change_permission() denies
    # the entire change view for a President object to a non-President
    # actor — Django then falls back to has_view_permission (still true) so
    # the object renders as fully read-only rather than disappearing, and
    # every field is protected without needing to keep
    # PRESIDENT_PROTECTED_FIELDS in sync with the model's field list by
    # hand. get_readonly_fields()/get_form() stay in place as defense in
    # depth (belt-and-suspenders, not "rely only on hidden UI fields") in
    # case some other code path reaches save_model() directly.
    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.is_president and not getattr(request.user, 'is_president', False):
            return False
        return super().has_change_permission(request, obj)

    # Defense-in-depth object-level guard: even if has_change_permission()
    # were somehow bypassed (a custom action, a future code path that calls
    # save_model() directly), never persist a change to a President's row
    # made by a non-President actor.
    def save_model(self, request, obj, form, change):
        if change and obj.is_president and not getattr(request.user, 'is_president', False):
            raise PermissionDenied('Only the President may modify the President account.')
        super().save_model(request, obj, form, change)

    # Queryset-level guard for bulk actions: has_delete_permission(request, obj)
    # already excludes a President from Django's own delete_selected
    # confirmation flow (per-object permission check), but delete_queryset()
    # is the lower-level hook actually performing the deletion — guarding it
    # directly means a President row can never be deleted through this
    # ModelAdmin via ANY queryset-based path, not only the one built-in action.
    def delete_queryset(self, request, queryset):
        if not getattr(request.user, 'is_president', False):
            queryset = queryset.exclude(role=CustomUser.ROLE_PRESIDENT)
        super().delete_queryset(request, queryset)

    # v2.1.16 Security Hardening Round #3 (Blocker 4): get_readonly_fields()
    # above only protects an EXISTING President row's `role` field from being
    # changed — it does nothing to stop a non-President admin from creating a
    # brand-new user with role=President (add form), or from promoting an
    # existing non-President account to President via the change form (obj
    # is not yet a President at read time, so the readonly-fields check never
    # triggers). Removing 'president' from the role field's choices whenever
    # the acting user is not themselves President closes both paths — Django
    # form validation rejects role=president as "not a valid choice" even
    # against a hand-crafted POST, the same defense-in-depth pattern used
    # elsewhere in this app (e.g. accounts/forms.py role-choice restriction).
    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if not getattr(request.user, 'is_president', False):
            role_field = form.base_fields.get('role')
            if role_field is not None:
                role_field.choices = [
                    choice for choice in role_field.choices
                    if choice[0] != CustomUser.ROLE_PRESIDENT
                ]
        return form

    # v2.1.16 Security Hardening Round #3 (Blocker 4): UserAdmin's dedicated
    # password-change view (/admin/accounts/customuser/<id>/password/) is a
    # separate view from the change form and does not consult
    # get_readonly_fields() at all — a non-President admin could reset a
    # President's password there even with 'password' protected on the main
    # form (which it never actually is, since it's not an editable field to
    # begin with; see PRESIDENT_PROTECTED_FIELDS docstring). Block the route
    # outright for a President target when the acting user is not President.
    def user_change_password(self, request, id, form_url=''):
        user = self.get_object(request, unquote(id))
        if user is not None and user.is_president and not getattr(request.user, 'is_president', False):
            raise PermissionDenied
        return super().user_change_password(request, id, form_url)
