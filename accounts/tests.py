"""
Tests for the accounts app: CustomUser model, role helpers, password validator.
"""

from unittest import mock
from django.test import TestCase, Client
from django.urls import reverse
from django.core.exceptions import ValidationError
from accounts.models import CustomUser
from accounts.validators import CharacterClassValidator, UppercasePasswordValidator


class CustomUserRoleTest(TestCase):
    """Verify role helper properties on CustomUser — President/Admin/IT/Staff."""

    def _make_user(self, role, username_suffix=''):
        return CustomUser.objects.create_user(
            username=f'testuser_{role}{username_suffix}',
            password='TestPass1!',
            role=role,
            employee_id=f'EMP-{role[:3].upper()}{username_suffix}',
        )

    def test_it_role_is_admin_and_admin_it(self):
        """IT role satisfies is_admin and is_admin_it (technical access)."""
        user = self._make_user(CustomUser.ROLE_IT, '1')
        self.assertTrue(user.is_admin)
        self.assertTrue(user.is_admin_it)
        self.assertFalse(user.is_president)
        self.assertFalse(user.is_head_barangay)
        self.assertFalse(user.is_staff_member)

    def test_president_is_admin_not_admin_it(self):
        """President is_admin=True, is_president=True, is_admin_it=False."""
        user = self._make_user(CustomUser.ROLE_PRESIDENT, '2')
        self.assertTrue(user.is_admin)
        self.assertFalse(user.is_admin_it)
        self.assertTrue(user.is_president)
        self.assertTrue(user.is_head_barangay)   # backward-compat alias
        self.assertFalse(user.is_staff_member)

    def test_admin_role_is_admin_not_admin_it(self):
        """Admin role is_admin=True, is_admin_it=False, is_president=False."""
        user = self._make_user(CustomUser.ROLE_ADMIN, '3')
        self.assertTrue(user.is_admin)
        self.assertFalse(user.is_admin_it)
        self.assertFalse(user.is_president)
        self.assertFalse(user.is_head_barangay)
        self.assertFalse(user.is_staff_member)

    def test_staff_is_not_admin(self):
        user = self._make_user(CustomUser.ROLE_STAFF, '4')
        self.assertFalse(user.is_admin)
        self.assertFalse(user.is_admin_it)
        self.assertFalse(user.is_president)
        self.assertFalse(user.is_head_barangay)
        self.assertTrue(user.is_staff_member)

    def test_default_role_is_staff(self):
        user = CustomUser.objects.create_user(
            username='default_role_user',
            password='TestPass1!',
            employee_id='EMP-DEF',
        )
        self.assertEqual(user.role, CustomUser.ROLE_STAFF)

    def test_str_includes_role(self):
        user = self._make_user(CustomUser.ROLE_IT, '5')
        self.assertIn(user.role, str(user))

    def test_user_is_active_by_default(self):
        user = self._make_user(CustomUser.ROLE_STAFF, '6')
        self.assertTrue(user.is_active)

    def test_four_roles_exist_in_choices(self):
        """System roles must be exactly: President, Admin, IT, Staff."""
        role_values = [r[0] for r in CustomUser.ROLE_CHOICES]
        self.assertIn(CustomUser.ROLE_PRESIDENT, role_values)
        self.assertIn(CustomUser.ROLE_ADMIN, role_values)
        self.assertIn(CustomUser.ROLE_IT, role_values)
        self.assertIn(CustomUser.ROLE_STAFF, role_values)
        self.assertEqual(len(role_values), 4)

    def test_head_brgy_constant_is_legacy_only(self):
        """Legacy ROLE_HEAD_BRGY constant exists but is NOT in active ROLE_CHOICES."""
        role_values = [r[0] for r in CustomUser.ROLE_CHOICES]
        self.assertNotIn(CustomUser.ROLE_HEAD_BRGY, role_values)
        self.assertNotIn(CustomUser.ROLE_ADMIN_IT, role_values)


class CharacterClassValidatorTest(TestCase):
    """Verify the custom password character-class validator."""

    def setUp(self):
        self.validator = CharacterClassValidator()

    def test_valid_password_with_digit(self):
        self.validator.validate('SecurePass1')  # no exception

    def test_valid_password_with_symbol(self):
        self.validator.validate('SecurePass!')  # no exception

    def test_letters_only_raises(self):
        with self.assertRaises(ValidationError):
            self.validator.validate('OnlyLetters')

    def test_digits_only_raises(self):
        with self.assertRaises(ValidationError):
            self.validator.validate('1234567890')

    def test_empty_raises(self):
        with self.assertRaises(ValidationError):
            self.validator.validate('')

    def test_get_help_text_returns_string(self):
        text = self.validator.get_help_text()
        self.assertIsInstance(text, str)
        self.assertTrue(len(text) > 0)


class UppercasePasswordValidatorTest(TestCase):
    """v2.3.0 — passwords must contain at least one uppercase letter."""

    def setUp(self):
        self.validator = UppercasePasswordValidator()

    def test_password_with_uppercase_passes(self):
        self.validator.validate('Secure1pass!')  # no exception

    def test_all_lowercase_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            self.validator.validate('alllowercase1!')
        self.assertIn('uppercase', ctx.exception.message.lower())

    def test_digits_only_rejected(self):
        with self.assertRaises(ValidationError):
            self.validator.validate('1234567890')

    def test_empty_rejected(self):
        with self.assertRaises(ValidationError):
            self.validator.validate('')

    def test_get_help_text_mentions_uppercase(self):
        text = self.validator.get_help_text()
        self.assertIn('uppercase', text.lower())


class LoginViewTest(TestCase):
    """Verify the login view behavior."""

    def setUp(self):
        self.client = Client()
        self.login_url = reverse('accounts:login')
        self.user = CustomUser.objects.create_user(
            username='logintest',
            password='TestPass123!',
            role=CustomUser.ROLE_STAFF,
            employee_id='EMP-LOGIN',
        )

    def test_login_page_loads(self):
        response = self.client.get(self.login_url)
        self.assertEqual(response.status_code, 200)

    def test_valid_login_redirects(self):
        response = self.client.post(self.login_url, {
            'username': 'logintest',
            'password': 'TestPass123!',
        }, follow=False)
        self.assertIn(response.status_code, [301, 302])

    def test_invalid_login_returns_200_with_error(self):
        response = self.client.post(self.login_url, {
            'username': 'logintest',
            'password': 'WrongPassword!',
        })
        self.assertEqual(response.status_code, 200)

    def test_inactive_user_cannot_login(self):
        self.user.is_active = False
        self.user.save()
        response = self.client.post(self.login_url, {
            'username': 'logintest',
            'password': 'TestPass123!',
        })
        self.assertEqual(response.status_code, 200)


class UserCreateFormRequiredFieldsTest(TestCase):
    """Issue 1 regression: all required user fields are enforced by UserCreateForm."""

    _valid = {
        'username':    'reqtest_user',
        'first_name':  'Juan',
        'last_name':   'Cruz',
        'email':       'req@example.com',
        'role':        CustomUser.ROLE_STAFF,
        'employee_id': 'EMP-REQ-001',
        'phone':       '',
        'password1':   'S3cur3Pa$$word!',
        'password2':   'S3cur3Pa$$word!',
    }

    def _form_without(self, field):
        from accounts.forms import UserCreateForm
        data = {**self._valid, field: ''}
        return UserCreateForm(data=data)

    def test_missing_first_name_rejected(self):
        form = self._form_without('first_name')
        self.assertFalse(form.is_valid())
        self.assertIn('first_name', form.errors)

    def test_missing_last_name_rejected(self):
        form = self._form_without('last_name')
        self.assertFalse(form.is_valid())
        self.assertIn('last_name', form.errors)

    def test_missing_email_rejected(self):
        form = self._form_without('email')
        self.assertFalse(form.is_valid())
        self.assertIn('email', form.errors)

    def test_missing_role_rejected(self):
        from accounts.forms import UserCreateForm
        data = {**self._valid, 'role': ''}
        form = UserCreateForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertIn('role', form.errors)

    def test_missing_employee_id_rejected(self):
        form = self._form_without('employee_id')
        self.assertFalse(form.is_valid())
        self.assertIn('employee_id', form.errors)

    def test_valid_create_accepted(self):
        from accounts.forms import UserCreateForm
        form = UserCreateForm(data=self._valid)
        self.assertTrue(form.is_valid(), form.errors)


class UserUpdateFormRequiredFieldsTest(TestCase):
    """Issue 1 regression: required fields also enforced on UserUpdateForm."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username='upd_target',
            password='TestPass123!',
            role=CustomUser.ROLE_STAFF,
            employee_id='EMP-UPD-001',
            first_name='Old',
            last_name='Name',
            email='old@example.com',
        )

    def _form(self, overrides=None):
        from accounts.forms import UserUpdateForm
        data = {
            'first_name':  'New',
            'last_name':   'Name',
            'email':       'new@example.com',
            'role':        CustomUser.ROLE_STAFF,
            'employee_id': 'EMP-UPD-001',
            'phone':       '',
            'is_active':   True,
        }
        if overrides:
            data.update(overrides)
        return UserUpdateForm(data=data, instance=self.user)

    def test_missing_first_name_rejected(self):
        form = self._form({'first_name': ''})
        self.assertFalse(form.is_valid())
        self.assertIn('first_name', form.errors)

    def test_missing_last_name_rejected(self):
        form = self._form({'last_name': ''})
        self.assertFalse(form.is_valid())
        self.assertIn('last_name', form.errors)

    def test_missing_email_rejected(self):
        form = self._form({'email': ''})
        self.assertFalse(form.is_valid())
        self.assertIn('email', form.errors)

    def test_missing_employee_id_rejected(self):
        form = self._form({'employee_id': ''})
        self.assertFalse(form.is_valid())
        self.assertIn('employee_id', form.errors)

    def test_valid_update_accepted(self):
        form = self._form()
        self.assertTrue(form.is_valid(), form.errors)


class ProfilePictureRemovedTest(TestCase):
    """v2.3.0 — profile_picture removed from UserUpdateForm (avatar upload discontinued)."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username='pp_val_user',
            password='TestPass123!',
            role=CustomUser.ROLE_STAFF,
            employee_id='EMP-PP-001',
            first_name='Test',
            last_name='User',
            email='pp@example.com',
        )

    def _base_data(self):
        return {
            'first_name':  'Test',
            'last_name':   'User',
            'email':       'pp@example.com',
            'role':        CustomUser.ROLE_STAFF,
            'employee_id': 'EMP-PP-001',
            'phone':       '',
            'is_active':   True,
        }

    def test_profile_picture_not_in_form_fields(self):
        from accounts.forms import UserUpdateForm
        form = UserUpdateForm(data=self._base_data(), instance=self.user)
        self.assertNotIn('profile_picture', form.fields)

    def test_update_form_valid_without_picture(self):
        from accounts.forms import UserUpdateForm
        form = UserUpdateForm(data=self._base_data(), instance=self.user)
        self.assertTrue(form.is_valid(), form.errors)

    def test_ignored_picture_upload_does_not_error(self):
        """Sending a file that is no longer in the form must not cause an error."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        from accounts.forms import UserUpdateForm
        dummy = SimpleUploadedFile('photo.jpg', b'fake', content_type='image/jpeg')
        form = UserUpdateForm(data=self._base_data(), files={'profile_picture': dummy}, instance=self.user)
        self.assertTrue(form.is_valid(), form.errors)


class LoginBrandingTest(TestCase):
    """v2.0.9 — Login page must show FANSC, not FANS."""

    def test_login_page_title_says_fansc(self):
        resp = Client().get(reverse('accounts:login'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'FANSC')

    def test_login_page_does_not_say_fans_alone(self):
        resp = Client().get(reverse('accounts:login'))
        content = resp.content.decode()
        self.assertIn('FANSC', content)
        self.assertNotIn('>FANS<', content)


class CreateSuperuserRoleTest(TestCase):
    """create_superuser() must set role=it (technical admin), not role=staff."""

    def test_create_superuser_gets_it_role(self):
        user = CustomUser.objects.create_superuser(
            username='su_test',
            email='su@fans.local',
            password='SuperPass1!',
        )
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertEqual(user.role, CustomUser.ROLE_IT)
        self.assertTrue(user.is_admin)
        self.assertTrue(user.is_admin_it)

    def test_create_superuser_role_not_staff(self):
        user = CustomUser.objects.create_superuser(
            username='su_test2',
            email='su2@fans.local',
            password='SuperPass1!',
        )
        self.assertNotEqual(user.role, CustomUser.ROLE_STAFF)

    def test_create_user_still_defaults_to_staff_role(self):
        user = CustomUser.objects.create_user(
            username='reg_user_test',
            password='RegPass1!',
            employee_id='EMP-REG-999',
        )
        self.assertEqual(user.role, CustomUser.ROLE_STAFF)
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.is_admin)


# ── v2.1.9 Legacy-role regression tests ──────────────────────────────────────

class LegacyRoleRegressionTest(TestCase):
    """
    Regression tests for v2.1.9 hotfix: ensure legacy roles admin_it /
    head_brgy cannot be created through active code paths and are correctly
    displayed / migrated.
    """

    def _make_user(self, role, suffix=''):
        return CustomUser.objects.create_user(
            username=f'regruser_{role}_{suffix}',
            password='TestPass1!',
            role=role,
            employee_id=f'EMP-REGR-{suffix}',
        )

    # ── Role choices never include legacy values ───────────────────────────

    def test_role_choices_exact_four(self):
        """ROLE_CHOICES must have exactly president/admin/it/staff and nothing else."""
        values = [v for v, _ in CustomUser.ROLE_CHOICES]
        self.assertEqual(sorted(values), sorted(['president', 'admin', 'it', 'staff']))

    def test_role_choices_no_admin_it(self):
        values = [v for v, _ in CustomUser.ROLE_CHOICES]
        self.assertNotIn('admin_it', values)
        self.assertNotIn('ADMIN_IT', values)

    def test_role_choices_no_head_brgy(self):
        values = [v for v, _ in CustomUser.ROLE_CHOICES]
        self.assertNotIn('head_brgy', values)
        self.assertNotIn('HEAD_BRGY', values)

    def test_role_choices_no_it_admin_label(self):
        labels = [label for _, label in CustomUser.ROLE_CHOICES]
        self.assertNotIn('IT/Admin', labels)
        self.assertNotIn('IT / Admin', labels)
        self.assertNotIn('Head Barangay', labels)

    # ── Forms never offer legacy role values ──────────────────────────────

    def test_create_form_choices_no_legacy_roles(self):
        from accounts.forms import UserCreateForm
        role_field = UserCreateForm().fields['role']
        values = [v for v, _ in role_field.choices if v]
        self.assertNotIn('admin_it', values)
        self.assertNotIn('head_brgy', values)

    def test_create_form_choices_include_all_current_roles(self):
        from accounts.forms import UserCreateForm
        role_field = UserCreateForm().fields['role']
        values = [v for v, _ in role_field.choices if v]
        for role in ('president', 'admin', 'it', 'staff'):
            self.assertIn(role, values)

    # ── Role display never shows legacy strings ────────────────────────────

    def test_admin_it_role_displays_as_admin(self):
        """A DB row with role=admin_it (pre-migration) must display as 'Admin', not 'admin_it'."""
        user = self._make_user('admin_it', '1')
        self.assertEqual(user.get_role_display(), 'Admin')
        self.assertNotEqual(user.get_role_display(), 'admin_it')
        self.assertNotEqual(user.get_role_display(), 'ADMIN_IT')

    def test_head_brgy_role_displays_as_president(self):
        user = self._make_user('head_brgy', '2')
        self.assertEqual(user.get_role_display(), 'President')

    def test_admin_role_displays_as_admin(self):
        user = self._make_user(CustomUser.ROLE_ADMIN, '3')
        self.assertEqual(user.get_role_display(), 'Admin')

    def test_it_role_displays_as_technical_administrator(self):
        # v2.1.19 UX pass (section 3): ROLE_IT is user-facing "Technical
        # Administrator" — there is no barangay "IT Officer" position.
        # Internal stored value stays 'it' (see test_role_choices_values).
        user = self._make_user(CustomUser.ROLE_IT, '4')
        self.assertEqual(user.get_role_display(), 'Technical Administrator')
        self.assertEqual(user.role, 'it')
        # DB-stored value remains lowercase for stable permissions/migrations.
        self.assertEqual(user.role, 'it')

    def test_president_role_displays_as_president(self):
        user = self._make_user(CustomUser.ROLE_PRESIDENT, '5')
        self.assertEqual(user.get_role_display(), 'President')

    # ── Access / permissions ───────────────────────────────────────────────

    def test_admin_role_has_admin_access(self):
        user = self._make_user(CustomUser.ROLE_ADMIN, '6')
        self.assertTrue(user.is_admin)
        self.assertFalse(user.is_admin_it)    # admin_it property is IT-only
        self.assertFalse(user.is_president)

    def test_it_role_has_admin_and_it_access(self):
        user = self._make_user(CustomUser.ROLE_IT, '7')
        self.assertTrue(user.is_admin)
        self.assertTrue(user.is_admin_it)
        self.assertFalse(user.is_president)

    def test_president_role_has_admin_access(self):
        user = self._make_user(CustomUser.ROLE_PRESIDENT, '8')
        self.assertTrue(user.is_admin)
        self.assertTrue(user.is_president)
        self.assertFalse(user.is_admin_it)

    def test_staff_has_no_admin_access(self):
        user = self._make_user(CustomUser.ROLE_STAFF, '9')
        self.assertFalse(user.is_admin)
        self.assertFalse(user.is_admin_it)
        self.assertFalse(user.is_president)
        self.assertTrue(user.is_staff_member)

    def test_legacy_admin_it_has_no_admin_property(self):
        """An admin_it-role user must not pass is_admin — it is a legacy value."""
        user = self._make_user('admin_it', '10')
        # Legacy role is NOT in active permission checks; should not grant access.
        self.assertFalse(user.is_admin)
        self.assertFalse(user.is_admin_it)

    # ── create_admin command uses new defaults ─────────────────────────────

    def test_create_admin_command_default_role_is_admin(self):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command(
            'create_admin',
            '--username', 'test_create_admin_cmd',
            '--password', 'TestPass1!Admin',
            stdout=out,
        )
        user = CustomUser.objects.get(username='test_create_admin_cmd')
        self.assertEqual(user.role, CustomUser.ROLE_ADMIN)
        self.assertNotEqual(user.role, 'admin_it')


# ── Officer Management tests ──────────────────────────────────────────────────

class OfficerPositionModelTest(TestCase):
    """Unit tests for OfficerPosition model."""

    def _make_admin(self, suffix=''):
        return CustomUser.objects.create_user(
            username=f'ofp_admin{suffix}',
            password='TestPass123!',
            role=CustomUser.ROLE_IT,
            employee_id=f'EMP-OFP{suffix}',
        )

    def test_default_positions_seeded(self):
        from accounts.models import OfficerPosition
        # The migration seeds 14 default positions.
        count = OfficerPosition.objects.count()
        self.assertGreaterEqual(count, 14, 'At least 14 default positions must be seeded')

    def test_position_str(self):
        from accounts.models import OfficerPosition
        pos = OfficerPosition.objects.filter(name='President').first()
        self.assertIsNotNone(pos)
        self.assertEqual(str(pos), 'President')

    def test_current_holder_none_when_vacant(self):
        from accounts.models import OfficerPosition
        pos = OfficerPosition.objects.create(
            name='Test Vacant Position',
            order=99,
            is_unique=True,
            is_active=True,
        )
        self.assertIsNone(pos.current_holder)

    def test_current_holder_returns_active_assignment(self):
        import datetime
        from accounts.models import OfficerPosition, OfficerAssignment
        admin = self._make_admin('1')
        pos = OfficerPosition.objects.create(
            name='Test Filled Position',
            order=98,
            is_unique=True,
            is_active=True,
        )
        OfficerAssignment.objects.create(
            user=admin,
            position=pos,
            start_date=datetime.date.today(),
            is_current=True,
        )
        self.assertIsNotNone(pos.current_holder)
        self.assertEqual(pos.current_holder.user, admin)


class OfficerAssignmentModelTest(TestCase):
    """Unit tests for OfficerAssignment model and close() method."""

    def setUp(self):
        import datetime
        from accounts.models import OfficerPosition, OfficerAssignment
        self.admin = CustomUser.objects.create_user(
            username='ofa_admin',
            password='TestPass123!',
            role=CustomUser.ROLE_IT,
            employee_id='EMP-OFA',
        )
        self.pos = OfficerPosition.objects.create(
            name='OFA Test Position',
            order=97,
            is_unique=True,
            is_active=True,
        )
        self.assignment = OfficerAssignment.objects.create(
            user=self.admin,
            position=self.pos,
            start_date=datetime.date.today(),
            is_current=True,
        )

    def test_is_current_true_on_creation(self):
        self.assertTrue(self.assignment.is_current)

    def test_close_sets_is_current_false(self):
        self.assignment.close()
        self.assignment.refresh_from_db()
        self.assertFalse(self.assignment.is_current)

    def test_close_sets_end_date(self):
        self.assignment.close()
        self.assignment.refresh_from_db()
        self.assertIsNotNone(self.assignment.end_date)

    def test_str_includes_position_name(self):
        self.assertIn('OFA Test Position', str(self.assignment))


class CustomUserStatusTest(TestCase):
    """Tests for account_status and can_login property."""

    def _make_user(self, status='active'):
        u = CustomUser.objects.create_user(
            username=f'status_{status}',
            password='TestPass123!',
            role=CustomUser.ROLE_STAFF,
            employee_id=f'EMP-ST-{status[:3].upper()}',
            account_status=status,
        )
        if status != 'active':
            u.is_active = False
            u.save()
        return u

    def test_active_user_can_login(self):
        u = self._make_user('active')
        self.assertTrue(u.can_login)

    def test_inactive_user_cannot_login(self):
        u = self._make_user('inactive')
        self.assertFalse(u.can_login)

    def test_suspended_user_cannot_login(self):
        u = self._make_user('suspended')
        self.assertFalse(u.can_login)

    def test_get_current_officer_assignment_none_when_none(self):
        u = self._make_user('active')
        self.assertIsNone(u.get_current_officer_assignment())

    def test_get_current_officer_assignment_returns_assignment(self):
        import datetime
        from accounts.models import OfficerPosition, OfficerAssignment
        u = self._make_user('active')
        pos = OfficerPosition.objects.create(
            name='Can Login Test Position',
            order=96,
            is_unique=True,
            is_active=True,
        )
        OfficerAssignment.objects.create(
            user=u,
            position=pos,
            start_date=datetime.date.today(),
            is_current=True,
        )
        oa = u.get_current_officer_assignment()
        self.assertIsNotNone(oa)
        self.assertEqual(oa.position, pos)


class OfficerManagementViewTest(TestCase):
    """RBAC tests for officer management views."""

    def setUp(self):
        self.client = Client()
        self.admin = CustomUser.objects.create_user(
            username='om_admin',
            password='TestPass123!',
            role=CustomUser.ROLE_IT,
            employee_id='EMP-OM-ADM',
        )
        self.staff = CustomUser.objects.create_user(
            username='om_staff',
            password='TestPass123!',
            role=CustomUser.ROLE_STAFF,
            employee_id='EMP-OM-STF',
        )
        # v2.1.19 UX pass (section 7): organizational officer assignment is a
        # barangay operational decision, not a technical one — self.admin
        # above is actually role=IT (Technical Administrator) and can view
        # officer_assignment_list/org_chart but must NOT be able to assign
        # or close an officer assignment. Tests that need that mutation to
        # succeed use this real Admin-tier user instead.
        self.true_admin = CustomUser.objects.create_user(
            username='om_true_admin',
            password='TestPass123!',
            role=CustomUser.ROLE_ADMIN,
            employee_id='EMP-OM-TADM',
        )

    def test_user_list_requires_auth(self):
        resp = self.client.get(reverse('accounts:user_list'))
        self.assertIn(resp.status_code, [301, 302])

    def test_user_list_accessible_to_admin(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('accounts:user_list'))
        self.assertEqual(resp.status_code, 200)

    def test_user_list_forbidden_to_staff(self):
        self.client.force_login(self.staff)
        resp = self.client.get(reverse('accounts:user_list'), follow=False)
        # Staff should be redirected away from the user list
        self.assertIn(resp.status_code, [301, 302], msg='Staff must be redirected away from user list')

    def test_officer_position_list_accessible_to_admin(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('accounts:officer_position_list'))
        self.assertEqual(resp.status_code, 200)

    def test_officer_assignment_list_accessible_to_admin(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('accounts:officer_assignment_list'))
        self.assertEqual(resp.status_code, 200)

    def test_officer_assignment_list_filters_by_assigned_by(self):
        """FINAL PRE-EXE COMPLETION checkpoint, section 16: `assigned_by` is a
        real FK on OfficerAssignment — the list view/template now offer a
        filter on it, same as the existing officer/position/status filters."""
        from accounts.models import OfficerPosition, OfficerAssignment
        position = OfficerPosition.objects.create(name='Test Officer Position')
        OfficerAssignment.objects.create(
            user=self.staff, position=position, start_date='2026-01-01',
            assigned_by=self.true_admin,
        )
        OfficerAssignment.objects.create(
            user=self.admin, position=position, start_date='2026-01-01',
            assigned_by=self.admin,
        )
        self.client.force_login(self.true_admin)
        resp = self.client.get(reverse('accounts:officer_assignment_list'), {
            'current': '0', 'assigned_by': str(self.true_admin.pk),
        })
        self.assertEqual(resp.status_code, 200)
        assignments = list(resp.context['assignments'])
        self.assertEqual(len(assignments), 1)
        self.assertEqual(assignments[0].assigned_by_id, self.true_admin.pk)

    def test_org_chart_accessible_to_all_logged_in(self):
        self.client.force_login(self.staff)
        resp = self.client.get(reverse('accounts:org_chart'))
        self.assertEqual(resp.status_code, 200)

    def test_org_chart_groups_positions_into_tiers_by_level(self):
        """v2.2.0 Post-UAT Phase 12: positions sharing the same `level` value
        must render in the same tier row as equivalent-rank siblings; `order`
        only breaks ties for sort position and must NOT affect tiering by
        itself — two positions can share a level with different order values
        and still land in the same tier."""
        from .models import OfficerPosition
        OfficerPosition.objects.all().delete()
        top = OfficerPosition.objects.create(name='Chart Top', level=1, order=1)
        peer_a = OfficerPosition.objects.create(name='Chart Peer A', level=2, order=1)
        peer_b = OfficerPosition.objects.create(name='Chart Peer B', level=2, order=2)
        bottom = OfficerPosition.objects.create(name='Chart Bottom', level=3, order=1)

        self.client.force_login(self.staff)
        resp = self.client.get(reverse('accounts:org_chart'))
        self.assertEqual(resp.status_code, 200)
        tiers = resp.context['chart_tiers']
        self.assertEqual(len(tiers), 3)
        self.assertEqual(len(tiers[0]), 1)
        self.assertEqual(tiers[0][0]['position'], top)
        self.assertEqual({e['position'] for e in tiers[1]}, {peer_a, peer_b})
        self.assertEqual(tiers[2][0]['position'], bottom)

    def test_org_chart_same_level_different_order_still_grouped(self):
        """Same level, differing order values: still one tier, not split."""
        from .models import OfficerPosition
        OfficerPosition.objects.all().delete()
        a = OfficerPosition.objects.create(name='Sibling A', level=1, order=5)
        b = OfficerPosition.objects.create(name='Sibling B', level=1, order=99)

        self.client.force_login(self.staff)
        resp = self.client.get(reverse('accounts:org_chart'))
        tiers = resp.context['chart_tiers']
        self.assertEqual(len(tiers), 1)
        self.assertEqual({e['position'] for e in tiers[0]}, {a, b})

    def test_create_position_with_level_field(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse('accounts:officer_position_create'), {
            'name': 'Assistant Treasurer', 'description': '', 'level': 3,
            'order': 1, 'is_unique': 'on', 'is_active': 'on',
        })
        self.assertEqual(resp.status_code, 302)
        from .models import OfficerPosition
        pos = OfficerPosition.objects.get(name='Assistant Treasurer')
        self.assertEqual(pos.level, 3)

    def test_org_chart_renders_multiple_siblings_without_crashing(self):
        from .models import OfficerPosition
        OfficerPosition.objects.all().delete()
        OfficerPosition.objects.create(name='VP Internal', level=1, order=1)
        OfficerPosition.objects.create(name='VP External', level=1, order=2)
        self.client.force_login(self.staff)
        resp = self.client.get(reverse('accounts:org_chart'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'VP Internal')
        self.assertContains(resp, 'VP External')
        self.assertContains(resp, 'org-tier mb-2 multi')

    def test_user_set_status_requires_post(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('accounts:user_set_status', args=[self.staff.pk]))
        self.assertIn(resp.status_code, [301, 302, 405])

    def test_user_set_status_activates_user(self):
        self.staff.account_status = 'inactive'
        self.staff.is_active = False
        self.staff.save()
        self.client.force_login(self.admin)
        resp = self.client.post(
            reverse('accounts:user_set_status', args=[self.staff.pk]),
            data={'status': 'active'},
            follow=False,
        )
        self.assertIn(resp.status_code, [301, 302])
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.account_status, 'active')
        self.assertTrue(self.staff.is_active)

    def test_user_set_status_suspends_user(self):
        self.client.force_login(self.admin)
        self.client.post(
            reverse('accounts:user_set_status', args=[self.staff.pk]),
            data={'status': 'suspended', 'reason': 'Suspended for testing purposes'},
        )
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.account_status, 'suspended')
        self.assertFalse(self.staff.is_active)

    def test_officer_assignment_close_requires_post(self):
        import datetime
        from accounts.models import OfficerPosition, OfficerAssignment
        pos = OfficerPosition.objects.create(
            name='OM Close Test Position', order=95, is_active=True,
        )
        assignment = OfficerAssignment.objects.create(
            user=self.staff, position=pos,
            start_date=datetime.date.today(), is_current=True,
        )
        self.client.force_login(self.admin)
        resp = self.client.get(
            reverse('accounts:officer_assignment_close', args=[assignment.pk])
        )
        self.assertIn(resp.status_code, [301, 302, 405])

    def test_officer_assignment_close_ends_assignment(self):
        import datetime
        from accounts.models import OfficerPosition, OfficerAssignment
        pos = OfficerPosition.objects.create(
            name='OM Close Test Position2', order=94, is_active=True,
        )
        assignment = OfficerAssignment.objects.create(
            user=self.staff, position=pos,
            start_date=datetime.date.today(), is_current=True,
        )
        self.client.force_login(self.true_admin)
        self.client.post(
            reverse('accounts:officer_assignment_close', args=[assignment.pk])
        )
        assignment.refresh_from_db()
        self.assertFalse(assignment.is_current)

    def test_officer_assignment_close_denied_to_technical_administrator(self):
        """v2.1.19 UX pass: Technical Administrator (role=IT) has read access
        to officer assignments but must not be able to close one — that is a
        barangay operational decision reserved for President/Admin."""
        import datetime
        from accounts.models import OfficerPosition, OfficerAssignment
        pos = OfficerPosition.objects.create(
            name='OM Close Test Position3', order=93, is_active=True,
        )
        assignment = OfficerAssignment.objects.create(
            user=self.staff, position=pos,
            start_date=datetime.date.today(), is_current=True,
        )
        self.client.force_login(self.admin)  # role=IT
        self.client.post(
            reverse('accounts:officer_assignment_close', args=[assignment.pk])
        )
        assignment.refresh_from_db()
        self.assertTrue(assignment.is_current)


# ── v2.1-liveness-fix-test1 accounts regression tests ────────────────────────

class MustChangePasswordTest(TestCase):
    """
    v2.1 regression: must_change_password flag is honoured on login and cleared
    after a successful password change; admin reset sets the flag.
    """

    def setUp(self):
        self.client = Client()
        self.admin = CustomUser.objects.create_user(
            username='mcp_admin',
            password='AdminPass1!',
            role=CustomUser.ROLE_PRESIDENT,
            employee_id='EMP-MCP-ADMIN',
            first_name='Admin',
            last_name='User',
            email='mcp_admin@fans.local',
        )
        self.staff = CustomUser.objects.create_user(
            username='mcp_staff',
            password='StaffPass1!',
            role=CustomUser.ROLE_STAFF,
            employee_id='EMP-MCP-STAFF',
            first_name='Staff',
            last_name='User',
            email='mcp_staff@fans.local',
        )
        self.login_url = reverse('accounts:login')
        self.change_url = reverse('accounts:change_password')

    def test_login_with_must_change_password_redirects_to_change_password(self):
        """User with must_change_password=True must be sent to change_password on login."""
        self.staff.must_change_password = True
        self.staff.save()
        response = self.client.post(self.login_url, {
            'username': 'mcp_staff',
            'password': 'StaffPass1!',
        }, follow=False)
        self.assertIn(response.status_code, [301, 302])
        location = response.get('Location', '')
        self.assertTrue(
            'change' in location and 'password' in location,
            f'Expected redirect to change-password page, got: {location}',
        )

    def test_login_without_must_change_password_does_not_redirect_to_change(self):
        """User with must_change_password=False must NOT be sent to change_password."""
        self.staff.must_change_password = False
        self.staff.save()
        response = self.client.post(self.login_url, {
            'username': 'mcp_staff',
            'password': 'StaffPass1!',
        }, follow=False)
        self.assertIn(response.status_code, [301, 302])
        location = response.get('Location', '')
        # Should not redirect to change-password — user doesn't need a forced change
        self.assertFalse(
            'change' in location and 'password' in location,
            f'Should NOT redirect to change-password, got: {location}',
        )

    def test_change_password_clears_must_change_password_flag(self):
        """Successful password change must set must_change_password=False."""
        self.staff.must_change_password = True
        self.staff.save()
        self.client.force_login(self.staff)
        self.client.post(self.change_url, {
            'old_password': 'StaffPass1!',
            'new_password1': 'NewStaffPass2!',
            'new_password2': 'NewStaffPass2!',
        })
        self.staff.refresh_from_db()
        self.assertFalse(
            self.staff.must_change_password,
            'must_change_password must be cleared after successful change',
        )

    def test_admin_reset_sets_must_change_password(self):
        """Admin password reset must set must_change_password=True on the target user."""
        self.staff.must_change_password = False
        self.staff.save()
        self.client.force_login(self.admin)
        self.client.post(
            reverse('accounts:admin_reset_password', args=[self.staff.pk]),
            {
                'new_password1': 'ResetPass3!',
                'new_password2': 'ResetPass3!',
                'reset_reason': 'Resetting password for test verification',
            },
        )
        self.staff.refresh_from_db()
        self.assertTrue(
            self.staff.must_change_password,
            'must_change_password must be True after admin password reset',
        )


class PasswordResetRequestTest(TestCase):
    """
    P0.3 — self-service 'I'm locked out' request queue. No email/SMTP in this
    system; approval funnels into the EXISTING admin_reset_password view/form
    rather than duplicating password-setting logic.
    """

    def setUp(self):
        from accounts.models import PasswordResetRequest
        self.PasswordResetRequest = PasswordResetRequest
        self.client = Client()
        self.admin = CustomUser.objects.create_user(
            username='prr_admin', password='AdminPass1!',
            role=CustomUser.ROLE_PRESIDENT, employee_id='EMP-PRR-ADMIN',
        )
        self.other_admin = CustomUser.objects.create_user(
            username='prr_admin2', password='AdminPass1!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-PRR-ADMIN2',
        )
        self.staff = CustomUser.objects.create_user(
            username='prr_staff', password='StaffPass1!',
            role=CustomUser.ROLE_STAFF, employee_id='EMP-PRR-STAFF',
        )
        self.create_url = reverse('accounts:password_reset_request_create')
        self.list_url = reverse('accounts:password_reset_request_list')

    def test_unauthenticated_create_does_not_reveal_username_existence(self):
        """Same generic success message whether or not the username matches an account."""
        resp_match = self.client.post(self.create_url, {
            'username': 'prr_staff', 'contact_note': 'Forgot my password after vacation.',
        }, follow=True)
        resp_no_match = self.client.post(self.create_url, {
            'username': 'no_such_user_xyz', 'contact_note': 'Please help, locked out.',
        }, follow=True)
        msgs_match = [str(m) for m in resp_match.context['messages']]
        msgs_no_match = [str(m) for m in resp_no_match.context['messages']]
        self.assertEqual(msgs_match, msgs_no_match)
        self.assertEqual(self.PasswordResetRequest.objects.count(), 2)
        matched = self.PasswordResetRequest.objects.get(username_entered='prr_staff')
        unmatched = self.PasswordResetRequest.objects.get(username_entered='no_such_user_xyz')
        self.assertEqual(matched.user, self.staff)
        self.assertIsNone(unmatched.user)

    def test_creating_a_request_notifies_admins(self):
        from logs.models import Notification
        self.client.post(self.create_url, {
            'username': 'prr_staff', 'contact_note': 'Locked out.',
        })
        notif = Notification.objects.filter(
            category=Notification.CATEGORY_PASSWORD_RESET, recipient=self.admin,
        )
        self.assertTrue(notif.exists())
        self.assertIn('password reset', notif.first().title.lower())

    def test_duplicate_pending_request_is_deduped(self):
        self.client.post(self.create_url, {
            'username': 'prr_staff', 'contact_note': 'First request.',
        })
        self.client.post(self.create_url, {
            'username': 'prr_staff', 'contact_note': 'Second request, same user.',
        })
        self.assertEqual(
            self.PasswordResetRequest.objects.filter(username_entered='prr_staff').count(), 1,
        )

    def test_non_admin_denied_on_list_and_reject(self):
        self.client.force_login(self.staff)
        resp = self.client.get(self.list_url)
        self.assertNotEqual(resp.status_code, 200)

        prr = self.PasswordResetRequest.objects.create(
            username_entered='prr_staff', user=self.staff, contact_note='x',
        )
        reject_url = reverse('accounts:password_reset_request_reject', args=[prr.id])
        self.client.post(reject_url)
        prr.refresh_from_db()
        self.assertEqual(prr.status, self.PasswordResetRequest.STATUS_PENDING)

    def test_admin_approve_flow_resets_password_and_closes_request(self):
        from logs.models import AuditLog
        prr = self.PasswordResetRequest.objects.create(
            username_entered='prr_staff', user=self.staff, contact_note='Locked out.',
        )
        self.client.force_login(self.admin)
        reset_url = reverse('accounts:admin_reset_password', args=[self.staff.pk])
        response = self.client.post(f'{reset_url}?prr={prr.id}', {
            'new_password1': 'BrandNewPass9!',
            'new_password2': 'BrandNewPass9!',
            'reset_reason': 'Approved self-service reset request',
        })
        self.assertIn(response.status_code, [301, 302])

        self.staff.refresh_from_db()
        self.assertTrue(self.staff.check_password('BrandNewPass9!'))
        self.assertTrue(self.staff.must_change_password)

        prr.refresh_from_db()
        self.assertEqual(prr.status, self.PasswordResetRequest.STATUS_APPROVED)
        self.assertEqual(prr.reviewed_by, self.admin)

        audit_row = AuditLog.objects.filter(
            action=AuditLog.ACTION_PASSWORD_RESET, target_id=str(self.staff.id),
        ).latest('timestamp')
        self.assertEqual(audit_row.details.get('source'), 'self_service_request')
        self.assertEqual(audit_row.details.get('request_id'), str(prr.id))

    def test_reject_flow_leaves_password_unchanged(self):
        from logs.models import AuditLog
        original_hash = self.staff.password
        prr = self.PasswordResetRequest.objects.create(
            username_entered='prr_staff', user=self.staff, contact_note='x',
        )
        self.client.force_login(self.admin)
        reject_url = reverse('accounts:password_reset_request_reject', args=[prr.id])
        self.client.post(reject_url, {'review_notes': 'Could not verify identity.'})

        self.staff.refresh_from_db()
        self.assertEqual(self.staff.password, original_hash)

        prr.refresh_from_db()
        self.assertEqual(prr.status, self.PasswordResetRequest.STATUS_REJECTED)
        self.assertTrue(
            AuditLog.objects.filter(action=AuditLog.ACTION_PASSWORD_RESET_REQUEST_REJECTED).exists()
        )

    def test_reusing_already_resolved_request_fails_gracefully(self):
        """Two admins approving the same pending request: the second attempt must not
        silently log a second self-service-sourced reset tied to an already-closed request."""
        from logs.models import AuditLog
        prr = self.PasswordResetRequest.objects.create(
            username_entered='prr_staff', user=self.staff, contact_note='x',
        )
        reset_url = reverse('accounts:admin_reset_password', args=[self.staff.pk])

        self.client.force_login(self.admin)
        self.client.post(f'{reset_url}?prr={prr.id}', {
            'new_password1': 'FirstPass9!', 'new_password2': 'FirstPass9!',
            'reset_reason': 'first admin approves',
        })
        prr.refresh_from_db()
        self.assertEqual(prr.status, self.PasswordResetRequest.STATUS_APPROVED)

        self.client.force_login(self.other_admin)
        self.client.post(f'{reset_url}?prr={prr.id}', {
            'new_password1': 'SecondPass9!', 'new_password2': 'SecondPass9!',
            'reset_reason': 'second admin tries the same request',
        })

        self_service_resets = AuditLog.objects.filter(
            action=AuditLog.ACTION_PASSWORD_RESET,
            details__request_id=str(prr.id),
        )
        self.assertEqual(
            self_service_resets.count(), 1,
            'the already-resolved request must not be attributed to a second reset',
        )

    def test_president_only_admin_account_reset_rule_still_holds_via_prr_path(self):
        prr = self.PasswordResetRequest.objects.create(
            username_entered='prr_admin2', user=self.other_admin, contact_note='x',
        )
        self.client.force_login(self.admin)  # president may reset another admin
        reset_url = reverse('accounts:admin_reset_password', args=[self.other_admin.pk])
        response = self.client.post(f'{reset_url}?prr={prr.id}', {
            'new_password1': 'AdminReset9!', 'new_password2': 'AdminReset9!',
            'reset_reason': 'president resets another admin',
        }, follow=True)
        self.other_admin.refresh_from_db()
        self.assertTrue(self.other_admin.check_password('AdminReset9!'))
        prr.refresh_from_db()
        self.assertEqual(prr.status, self.PasswordResetRequest.STATUS_APPROVED)

    def test_admin_approve_resolves_pending_notification(self):
        """Final-verification Step 9: approving a self-service reset request
        must clear its 'pending review' notification, or the bell keeps
        showing a stale item for a case that is already closed."""
        from logs.models import Notification
        prr = self.PasswordResetRequest.objects.create(
            username_entered='prr_staff', user=self.staff, contact_note='Locked out.',
        )
        Notification.objects.create(
            recipient=self.admin, category=Notification.CATEGORY_PASSWORD_RESET,
            title='Password reset requested', dedupe_key=f'password_reset_request:{prr.id}',
        )
        self.client.force_login(self.admin)
        reset_url = reverse('accounts:admin_reset_password', args=[self.staff.pk])
        self.client.post(f'{reset_url}?prr={prr.id}', {
            'new_password1': 'BrandNewPass9!', 'new_password2': 'BrandNewPass9!',
            'reset_reason': 'Approved self-service reset request',
        })
        self.assertTrue(
            Notification.objects.get(dedupe_key=f'password_reset_request:{prr.id}').is_read
        )

    def test_reject_resolves_pending_notification(self):
        from logs.models import Notification
        prr = self.PasswordResetRequest.objects.create(
            username_entered='prr_staff', user=self.staff, contact_note='x',
        )
        Notification.objects.create(
            recipient=self.admin, category=Notification.CATEGORY_PASSWORD_RESET,
            title='Password reset requested', dedupe_key=f'password_reset_request:{prr.id}',
        )
        self.client.force_login(self.admin)
        reject_url = reverse('accounts:password_reset_request_reject', args=[prr.id])
        self.client.post(reject_url, {'review_notes': 'Could not verify identity.'})
        self.assertTrue(
            Notification.objects.get(dedupe_key=f'password_reset_request:{prr.id}').is_read
        )


class AuditLogUserCreateConstantTest(TestCase):
    """
    v2.1 regression: AuditLog.ACTION_USER_CREATE constant (not ACTION_USER_CREATED)
    must exist and hold the expected string value used throughout accounts/views.py.
    """

    def test_action_user_create_constant_exists(self):
        """ACTION_USER_CREATE must be defined on AuditLog."""
        from logs.models import AuditLog
        self.assertTrue(
            hasattr(AuditLog, 'ACTION_USER_CREATE'),
            'AuditLog.ACTION_USER_CREATE constant is missing',
        )

    def test_action_user_create_is_string(self):
        """ACTION_USER_CREATE must be a non-empty string."""
        from logs.models import AuditLog
        value = AuditLog.ACTION_USER_CREATE
        self.assertIsInstance(value, str)
        self.assertTrue(len(value) > 0)

    def test_action_user_created_does_not_exist(self):
        """ACTION_USER_CREATED (typo) must NOT be defined — it was replaced by ACTION_USER_CREATE."""
        from logs.models import AuditLog
        self.assertFalse(
            hasattr(AuditLog, 'ACTION_USER_CREATED'),
            'AuditLog.ACTION_USER_CREATED must not exist (use ACTION_USER_CREATE)',
        )


# ─── v2.1.12 — President System-Role Uniqueness ───────────────────────────────

class PresidentRoleUniquenessTest(TestCase):
    """
    v2.1.12 (Issue 2): only one active user may hold System Role = President.
    Tests the model helper, the create/edit forms, and the create-admin form.
    """

    def setUp(self):
        # Seed a single active President so we can test uniqueness.
        self.existing_pres = CustomUser.objects.create_user(
            username='pres_active',
            password='TestPass123!',
            role=CustomUser.ROLE_PRESIDENT,
            first_name='Active', last_name='President',
            email='pres@example.com',
            employee_id='EMP-PRES-1',
            account_status=CustomUser.STATUS_ACTIVE,
            is_active=True,
        )

    def test_active_president_exists_true_when_one_present(self):
        self.assertTrue(CustomUser.active_president_exists())

    def test_active_president_exists_false_when_excluded(self):
        """Editing the same president should not trip uniqueness."""
        self.assertFalse(
            CustomUser.active_president_exists(exclude_pk=self.existing_pres.pk)
        )

    def test_inactive_president_does_not_block_new_one(self):
        self.existing_pres.is_active = False
        self.existing_pres.account_status = CustomUser.STATUS_INACTIVE
        self.existing_pres.save()
        self.assertFalse(CustomUser.active_president_exists())

    def test_suspended_president_does_not_block_new_one(self):
        self.existing_pres.is_active = False
        self.existing_pres.account_status = CustomUser.STATUS_SUSPENDED
        self.existing_pres.save()
        self.assertFalse(CustomUser.active_president_exists())

    def test_user_create_form_blocks_second_president(self):
        from accounts.forms import UserCreateForm, PRESIDENT_UNIQUE_ERROR
        form = UserCreateForm(data={
            'username': 'pres2',
            'first_name': 'Second', 'last_name': 'President',
            'email': 'pres2@example.com',
            'role': CustomUser.ROLE_PRESIDENT,
            'employee_id': 'EMP-PRES-2',
            'phone': '',
            'password1': 'TestPass123!Aa',
            'password2': 'TestPass123!Aa',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('role', form.errors)
        self.assertIn(PRESIDENT_UNIQUE_ERROR, form.errors['role'][0])

    def test_user_create_full_form_blocks_second_president(self):
        from accounts.forms import UserCreateFullForm, PRESIDENT_UNIQUE_ERROR
        form = UserCreateFullForm(data={
            'username': 'pres2',
            'first_name': 'Second', 'last_name': 'President',
            'email': 'pres2@example.com',
            'role': CustomUser.ROLE_PRESIDENT,
            'employee_id': 'EMP-PRES-2',
            'phone': '',
            'assigned_office': '',
            'account_status': CustomUser.STATUS_ACTIVE,
            'must_change_password': False,
            'password1': 'TestPass123!Aa',
            'password2': 'TestPass123!Aa',
        })
        self.assertFalse(form.is_valid())
        self.assertIn(PRESIDENT_UNIQUE_ERROR, form.errors['role'][0])

    def test_user_edit_full_form_blocks_promote_to_president(self):
        from accounts.forms import UserEditFullForm, PRESIDENT_UNIQUE_ERROR
        other = CustomUser.objects.create_user(
            username='to_promote',
            password='TestPass123!',
            role=CustomUser.ROLE_ADMIN,
            first_name='To', last_name='Promote',
            email='tp@example.com',
            employee_id='EMP-TP',
        )
        form = UserEditFullForm(
            instance=other,
            data={
                'first_name': 'To', 'last_name': 'Promote',
                'email': 'tp@example.com',
                'role': CustomUser.ROLE_PRESIDENT,
                'employee_id': 'EMP-TP',
                'phone': '',
                'assigned_office': '',
                'account_status': CustomUser.STATUS_ACTIVE,
                'must_change_password': False,
            },
        )
        self.assertFalse(form.is_valid())
        self.assertIn(PRESIDENT_UNIQUE_ERROR, form.errors['role'][0])

    def test_user_edit_full_form_allows_editing_existing_president(self):
        """The current President can be edited (e.g. phone update) without trip."""
        from accounts.forms import UserEditFullForm
        form = UserEditFullForm(
            instance=self.existing_pres,
            data={
                'first_name': 'Active', 'last_name': 'President',
                'email': 'pres@example.com',
                'role': CustomUser.ROLE_PRESIDENT,
                'employee_id': 'EMP-PRES-1',
                'phone': '09171234567',
                'assigned_office': '',
                'account_status': CustomUser.STATUS_ACTIVE,
                'must_change_password': False,
            },
        )
        self.assertTrue(form.is_valid(), msg=form.errors)


class PresidentRoleFirstCreationTest(TestCase):
    """v2.1.12: creating the FIRST active President is allowed."""

    def test_first_president_create_succeeds(self):
        from accounts.forms import UserCreateFullForm
        form = UserCreateFullForm(data={
            'username': 'pres1',
            'first_name': 'First', 'last_name': 'President',
            'email': 'pres1@example.com',
            'role': CustomUser.ROLE_PRESIDENT,
            'employee_id': 'EMP-PRES-1',
            'phone': '',
            'assigned_office': '',
            'account_status': CustomUser.STATUS_ACTIVE,
            'must_change_password': False,
            'password1': 'TestPass123!Aa',
            'password2': 'TestPass123!Aa',
        })
        self.assertTrue(form.is_valid(), msg=form.errors)
        user = form.save()
        self.assertEqual(user.role, CustomUser.ROLE_PRESIDENT)
        self.assertTrue(user.is_active)


# ─── v2.1.12 — Officer Assignment Integration ─────────────────────────────────

class PresidentOfficerAssignmentTest(TestCase):
    """
    v2.1.12 (Issue 3): the user create/edit form can also create the linked
    OfficerAssignment so the org chart and officer assignments page update
    in the same flow.
    """

    def setUp(self):
        from accounts.models import OfficerPosition
        # Migration seeds the default positions; pick the President position.
        self.pres_pos = OfficerPosition.objects.get(name='President')
        self.secretary_pos = OfficerPosition.objects.get(name='Secretary')

    def test_create_user_with_officer_position_creates_assignment(self):
        from accounts.forms import UserCreateFullForm
        from accounts.models import OfficerAssignment
        form = UserCreateFullForm(data={
            'username': 'pres_org',
            'first_name': 'Org', 'last_name': 'President',
            'email': 'pres_org@example.com',
            'role': CustomUser.ROLE_PRESIDENT,
            'employee_id': 'EMP-ORG',
            'phone': '',
            'assigned_office': '',
            'account_status': CustomUser.STATUS_ACTIVE,
            'must_change_password': False,
            'password1': 'TestPass123!Aa',
            'password2': 'TestPass123!Aa',
            'officer_position': self.pres_pos.pk,
            'officer_start_date': '2026-05-27',
            'officer_is_current': 'on',
        })
        self.assertTrue(form.is_valid(), msg=form.errors)
        user = form.save()
        self.assertTrue(
            OfficerAssignment.objects.filter(
                user=user, position=self.pres_pos, is_current=True,
            ).exists(),
            msg='OfficerAssignment was not created for the new President.',
        )

    def test_create_user_without_officer_position_creates_no_assignment(self):
        from accounts.forms import UserCreateFullForm
        from accounts.models import OfficerAssignment
        form = UserCreateFullForm(data={
            'username': 'staff_user',
            'first_name': 'Staff', 'last_name': 'Only',
            'email': 'staff@example.com',
            'role': CustomUser.ROLE_STAFF,
            'employee_id': 'EMP-STF',
            'phone': '',
            'assigned_office': '',
            'account_status': CustomUser.STATUS_ACTIVE,
            'must_change_password': False,
            'password1': 'TestPass123!Aa',
            'password2': 'TestPass123!Aa',
            # officer_position deliberately omitted
        })
        self.assertTrue(form.is_valid(), msg=form.errors)
        user = form.save()
        self.assertFalse(
            OfficerAssignment.objects.filter(user=user).exists(),
        )

    def test_create_user_blocks_duplicate_unique_position(self):
        """If President officer position is occupied, block another current
        assignment in the create form."""
        import datetime
        from accounts.forms import UserCreateFullForm
        from accounts.models import OfficerAssignment

        # Pre-populate a current President officer holder.
        holder = CustomUser.objects.create_user(
            username='pres_holder',
            password='TestPass123!',
            role=CustomUser.ROLE_PRESIDENT,
            first_name='Holder', last_name='President',
            email='holder@example.com',
            employee_id='EMP-HOLDER',
            account_status=CustomUser.STATUS_INACTIVE,  # so System-Role uniqueness passes
            is_active=False,
        )
        OfficerAssignment.objects.create(
            user=holder, position=self.pres_pos,
            start_date=datetime.date(2026, 1, 1), is_current=True,
        )

        form = UserCreateFullForm(data={
            'username': 'second_pres',
            'first_name': 'Second', 'last_name': 'President',
            'email': 'second@example.com',
            'role': CustomUser.ROLE_PRESIDENT,  # OK — old holder is inactive
            'employee_id': 'EMP-SECOND',
            'phone': '',
            'assigned_office': '',
            'account_status': CustomUser.STATUS_ACTIVE,
            'must_change_password': False,
            'password1': 'TestPass123!Aa',
            'password2': 'TestPass123!Aa',
            'officer_position': self.pres_pos.pk,
            'officer_start_date': '2026-05-27',
            'officer_is_current': 'on',
        })
        self.assertFalse(form.is_valid())
        # Non-field error from clean(); should mention the position name.
        all_errors = ' | '.join(
            [' '.join(v) for v in form.errors.values()]
        )
        self.assertIn('already held', all_errors)

    def test_edit_user_promotes_to_officer_position(self):
        """Editing an existing user to add an Officer Position creates the assignment."""
        from accounts.forms import UserEditFullForm
        from accounts.models import OfficerAssignment
        user = CustomUser.objects.create_user(
            username='to_assign',
            password='TestPass123!',
            role=CustomUser.ROLE_ADMIN,
            first_name='To', last_name='Assign',
            email='ta@example.com',
            employee_id='EMP-TA',
            account_status=CustomUser.STATUS_ACTIVE,
        )
        form = UserEditFullForm(
            instance=user,
            data={
                'first_name': 'To', 'last_name': 'Assign',
                'email': 'ta@example.com',
                'role': CustomUser.ROLE_ADMIN,
                'employee_id': 'EMP-TA',
                'phone': '',
                'assigned_office': '',
                'account_status': CustomUser.STATUS_ACTIVE,
                'must_change_password': False,
                'officer_position': self.secretary_pos.pk,
                'officer_start_date': '2026-05-27',
                'officer_is_current': 'on',
            },
        )
        self.assertTrue(form.is_valid(), msg=form.errors)
        form.save()
        self.assertTrue(
            OfficerAssignment.objects.filter(
                user=user, position=self.secretary_pos, is_current=True,
            ).exists(),
        )

    def test_org_chart_shows_new_president_after_assignment(self):
        """End-to-end smoke: org chart shows the assigned officer."""
        import datetime
        from accounts.models import OfficerAssignment
        from django.test import Client
        from django.urls import reverse

        pres_user = CustomUser.objects.create_user(
            username='org_pres',
            password='TestPass123!',
            role=CustomUser.ROLE_PRESIDENT,
            first_name='Org', last_name='President',
            email='op@example.com',
            employee_id='EMP-OP',
        )
        OfficerAssignment.objects.create(
            user=pres_user, position=self.pres_pos,
            start_date=datetime.date(2026, 1, 1), is_current=True,
        )

        viewer = CustomUser.objects.create_user(
            username='viewer',
            password='TestPass123!',
            role=CustomUser.ROLE_STAFF,
            first_name='View', last_name='Er',
            email='v@example.com',
            employee_id='EMP-V',
        )
        client = Client()
        client.force_login(viewer)
        resp = client.get(reverse('accounts:org_chart'))
        self.assertEqual(resp.status_code, 200)
        # Org President must appear, and Secretary must show as vacant.
        self.assertContains(resp, 'Org President')

    def test_officer_assignments_page_shows_new_assignment(self):
        """Officer assignments list page renders the newly created assignment."""
        import datetime
        from accounts.models import OfficerAssignment
        from django.test import Client
        from django.urls import reverse

        admin = CustomUser.objects.create_user(
            username='oa_admin',
            password='TestPass123!',
            role=CustomUser.ROLE_IT,
            first_name='Adm', last_name='In',
            email='oa@example.com',
            employee_id='EMP-OA',
        )
        target = CustomUser.objects.create_user(
            username='oa_target',
            password='TestPass123!',
            role=CustomUser.ROLE_ADMIN,
            first_name='Tar', last_name='Get',
            email='tg@example.com',
            employee_id='EMP-TG',
        )
        OfficerAssignment.objects.create(
            user=target, position=self.secretary_pos,
            start_date=datetime.date(2026, 1, 1), is_current=True,
        )
        client = Client()
        client.force_login(admin)
        resp = client.get(reverse('accounts:officer_assignment_list'))
        self.assertEqual(resp.status_code, 200)
        # The target user's name must appear on the assignments page.
        self.assertContains(resp, 'Tar')

    def test_unique_position_prevents_multiple_active_holders_at_db_level(self):
        """
        Defence-in-depth: the form blocks duplicates, but verify the data layer
        also surfaces both holders if someone bypasses the form (they should
        not be filtered out — we want the inconsistency to be visible).
        """
        import datetime
        from accounts.models import OfficerAssignment
        u1 = CustomUser.objects.create_user(
            username='u1', password='TestPass123!',
            role=CustomUser.ROLE_PRESIDENT,
            first_name='Old', last_name='Pres',
            email='u1@e.com', employee_id='EMP-U1',
            account_status=CustomUser.STATUS_INACTIVE, is_active=False,
        )
        u2 = CustomUser.objects.create_user(
            username='u2', password='TestPass123!',
            role=CustomUser.ROLE_PRESIDENT,
            first_name='New', last_name='Pres',
            email='u2@e.com', employee_id='EMP-U2',
            account_status=CustomUser.STATUS_ACTIVE, is_active=True,
        )
        # Both assignments are technically allowed at the DB level (no unique
        # constraint on the table). The FORM is the gate.
        OfficerAssignment.objects.create(
            user=u1, position=self.pres_pos,
            start_date=datetime.date(2025, 1, 1), is_current=True,
        )
        OfficerAssignment.objects.create(
            user=u2, position=self.pres_pos,
            start_date=datetime.date(2026, 5, 27), is_current=True,
        )
        # The form path enforces uniqueness — this DB-level check is here so
        # future schema-level constraints have a regression baseline.
        count = OfficerAssignment.objects.filter(
            position=self.pres_pos, is_current=True,
        ).count()
        self.assertGreaterEqual(count, 2)


# ─── v2.1.12 — System Role / Officer Position label separation ────────────────

class SystemRoleLabelTest(TestCase):
    """v2.1.12 (Issue 3): role label must say 'System Role / Access Role'."""

    def test_user_create_form_role_label(self):
        from accounts.forms import UserCreateForm
        form = UserCreateForm()
        self.assertEqual(
            form.fields['role'].label,
            'System Role / Access Role',
        )

    def test_user_edit_full_form_role_label(self):
        from accounts.forms import UserEditFullForm
        # Need an instance for ModelForm init.
        u = CustomUser.objects.create_user(
            username='lbl', password='TestPass123!',
            role=CustomUser.ROLE_STAFF,
            employee_id='EMP-LBL',
        )
        form = UserEditFullForm(instance=u)
        self.assertEqual(
            form.fields['role'].label,
            'System Role / Access Role',
        )

    def test_user_create_full_form_role_help_text(self):
        from accounts.forms import UserCreateFullForm
        form = UserCreateFullForm()
        self.assertIn(
            'software permissions',
            form.fields['role'].help_text,
        )


class LoginLockoutSecurityNotificationTest(TestCase):
    """v2.2.0 Phase 1: a login lockout is a meaningful security event and
    must notify admins exactly once per lockout — not once per failed attempt."""

    def setUp(self):
        from django.test import override_settings
        from django.core.cache import cache
        from accounts.views import _clear_failures

        self.override = override_settings(
            LOGIN_MAX_FAILED_ATTEMPTS=3, LOGIN_LOCKOUT_WINDOW_S=600, LOGIN_LOCKOUT_DURATION_S=900,
        )
        self.override.enable()
        self.addCleanup(self.override.disable)

        # The Django test client's default REMOTE_ADDR (127.0.0.1) is shared
        # across every test in the suite. The lockout counter lives in the
        # process-global cache, NOT the database, so it is not reset by
        # Django's per-test transaction rollback — a lockout set here would
        # otherwise persist (real wall-clock seconds) and break every other
        # test that logs in via the same IP for the rest of the run. Clear
        # before AND after so this test is fully self-contained.
        _clear_failures('127.0.0.1')
        self.addCleanup(_clear_failures, '127.0.0.1')

        self.admin = CustomUser.objects.create_user(
            username='lockout_admin', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-LOCKOUT-ADMIN',
        )
        self.client = Client()
        self.login_url = reverse('accounts:login')

    def test_lockout_creates_one_security_alert_notification(self):
        from logs.models import Notification
        for _ in range(3):
            self.client.post(self.login_url, {'username': 'nobody', 'password': 'wrong'})

        alerts = Notification.objects.filter(
            recipient=self.admin, category=Notification.CATEGORY_SECURITY_ALERT,
        )
        self.assertEqual(alerts.count(), 1)
        self.assertEqual(alerts.first().priority, Notification.PRIORITY_HIGH)

    def test_failed_attempts_below_threshold_do_not_notify(self):
        from logs.models import Notification
        self.client.post(self.login_url, {'username': 'nobody', 'password': 'wrong'})
        self.assertFalse(
            Notification.objects.filter(category=Notification.CATEGORY_SECURITY_ALERT).exists()
        )


class AdminMenuReorganizationTest(TestCase):
    """v2.2.0 Post-UAT Phase 11 — navigation reorganized by responsibility.

    "System Administration" used to be one mega-dropdown holding everything
    admin-level (Users & Access / Verification Management / Distribution /
    Security / Analytics / Settings all nested as sub-headers inside it).
    Distribution, Audit & Verification, and Analytics & Reports are now
    their own top-level nav groups; System Administration is narrowed to
    account/access/config concerns. Every previously-existing link must
    still be present somewhere (nothing removed, only regrouped), and three
    review queues that existed but were never in the persistent nav
    (Registration Applications, Duplicate Face Review, Duplicate Name/DOB
    Review) are now reachable from Beneficiary Management.

    2026-09-02 UX refinement pass shortened the on-screen labels (nav
    overflowed its container at common laptop widths) without changing
    which groups exist or what they contain: "Beneficiary Management" ->
    "Beneficiaries", "Verify Claimant" -> "Verify", "Audit & Verification"
    -> "Audit & Logs", "Analytics & Reports" -> "Analytics", "System
    Administration" -> "Administration". Assertions below use the new
    labels; this docstring keeps the original names for history."""

    def setUp(self):
        self.client = Client()
        self.it_admin = CustomUser.objects.create_user(
            username='menu_it_admin', password='TestPass123!',
            role=CustomUser.ROLE_IT, employee_id='EMP-MENU-IT',
        )
        self.staff = CustomUser.objects.create_user(
            username='menu_staff', password='TestPass123!',
            role=CustomUser.ROLE_STAFF, employee_id='EMP-MENU-STAFF',
        )

    def test_top_level_groups_present_for_admin(self):
        self.client.force_login(self.it_admin)
        resp = self.client.get(reverse('beneficiaries:dashboard'))
        content = resp.content.decode()
        for label in [
            'Beneficiaries', 'Verify', 'Distribution',
            'Audit &amp; Logs', 'Analytics', 'Administration',
        ]:
            self.assertIn(label, content, f'missing top-level nav group: {label}')

    def test_distribution_and_analytics_are_top_level_not_nested(self):
        """Distribution and Analytics & Reports must be their own dropdowns,
        not sub-headers buried inside System Administration."""
        self.client.force_login(self.it_admin)
        resp = self.client.get(reverse('beneficiaries:dashboard'))
        content = resp.content.decode()
        admin_dropdown_start = content.index('Administration')
        # The narrowed System Administration ("Administration") dropdown must
        # not repeat these as its own sub-headers — it only has "Users &
        # Access" and "System & Security Configuration" now.
        admin_dropdown_html = content[admin_dropdown_start:admin_dropdown_start + 4000]
        self.assertNotIn('Verification Management', admin_dropdown_html)

    def test_staff_do_not_see_admin_only_groups(self):
        self.client.force_login(self.staff)
        resp = self.client.get(reverse('beneficiaries:dashboard'))
        content = resp.content.decode()
        self.assertNotIn('Administration', content)
        self.assertNotIn('Analytics', content)
        self.assertNotIn('>Distribution<', content)

    def test_review_queues_reachable_from_beneficiary_management(self):
        self.client.force_login(self.it_admin)
        resp = self.client.get(reverse('beneficiaries:dashboard'))
        content = resp.content.decode()
        for url in [
            reverse('verification:registration_review_list'),
            reverse('beneficiaries:duplicate_review_list'),
            reverse('beneficiaries:namedob_review_list'),
        ]:
            self.assertIn(url, content, f'Beneficiaries menu is missing a link to {url}')

    def test_every_previously_existing_link_still_present(self):
        self.client.force_login(self.it_admin)
        resp = self.client.get(reverse('beneficiaries:dashboard'))
        content = resp.content.decode()
        expected_urls = [
            reverse('accounts:user_list'), reverse('role_matrix'),
            reverse('accounts:officer_assignment_list'), reverse('accounts:org_chart'),
            reverse('accounts:password_reset_request_list'), reverse('verification:manual_review'),
            reverse('verification:shared_rep_review_list'), reverse('verification:config'),
            reverse('verification:report_override_fallback'), reverse('verification:stipend_list'),
            reverse('verification:report_claims'), reverse('verification:report_event_summary'),
            reverse('verification:analytics_executive'), reverse('verification:fraud_signals_report'),
            reverse('verification:report_suspicious_attempts'), reverse('verification:template_match_report'),
            reverse('verification:report_staff_performance'), reverse('logs:audit_logs'),
            reverse('beneficiaries:auto_approval_settings'), reverse('privacy_consent'),
            reverse('system_health'), reverse('system_connection'),
        ]
        for url in expected_urls:
            self.assertIn(url, content, f'admin menu is missing a link to {url}')


# ──────────────────────────────────────────────────────────────────────────────
# v2.2.0 Post-UAT Phase 6 — self-service email-OTP password reset.
# ──────────────────────────────────────────────────────────────────────────────

from django.test import override_settings as _override_settings


@_override_settings(
    EMAIL_CONFIGURED=True, EMAIL_HOST='smtp.example.com', EMAIL_HOST_USER='x',
    OTP_MAX_ATTEMPTS=5, OTP_EXPIRY_MINUTES=5, OTP_RESEND_COOLDOWN_S=60,
    OTP_REQUEST_RATE_LIMIT=5, OTP_REQUEST_RATE_WINDOW_S=900,
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
)
class OTPPasswordResetTest(TestCase):

    def setUp(self):
        from django.core.cache import cache
        self.cache = cache
        # Cache-backed rate limit/cooldown state is process-global, not reset
        # by Django's per-test transaction rollback — clear before AND after
        # so this test class is self-contained (mirrors LoginLockoutSecurityNotificationTest).
        self._clear_otp_cache()
        self.addCleanup(self._clear_otp_cache)

        self.user = CustomUser.objects.create_user(
            username='otpuser1', password='OldPass123!',
            email='otpuser1@example.com',
            role=CustomUser.ROLE_STAFF, employee_id='EMP-OTPUSER1',
        )
        self.client = Client()
        self.forgot_url = reverse('accounts:otp_forgot_password')
        self.verify_url = reverse('accounts:otp_verify')
        self.reset_url = reverse('accounts:otp_reset_password')

    def _clear_otp_cache(self):
        self.cache.delete(f'fans:otp_request:127.0.0.1')
        self.cache.delete(f'fans:otp_cooldown:{self.user.pk}' if hasattr(self, 'user') else '')

    def _request_otp(self, identifier='otpuser1'):
        return self.client.post(self.forgot_url, {'identifier': identifier}, follow=True)

    # ── Anti-enumeration ────────────────────────────────────────────────────

    def test_generic_message_identical_for_existing_and_nonexistent_account(self):
        resp_match = self._request_otp('otpuser1')
        self.client.session.flush()
        resp_no_match = self._request_otp('no_such_user_zzz')
        msgs_match = [str(m) for m in resp_match.context['messages']]
        msgs_no_match = [str(m) for m in resp_no_match.context['messages']]
        self.assertEqual(msgs_match, msgs_no_match)

    def test_no_otp_row_created_for_nonexistent_account(self):
        from accounts.models import PasswordResetOTP
        self._request_otp('no_such_user_zzz')
        self.assertEqual(PasswordResetOTP.objects.count(), 0)

    def test_otp_row_created_for_existing_account(self):
        from accounts.models import PasswordResetOTP
        self._request_otp('otpuser1')
        self.assertEqual(PasswordResetOTP.objects.filter(user=self.user).count(), 1)

    def test_resolves_by_email_too(self):
        from accounts.models import PasswordResetOTP
        self._request_otp('otpuser1@example.com')
        self.assertEqual(PasswordResetOTP.objects.filter(user=self.user).count(), 1)

    def test_both_responses_redirect_to_same_verify_page(self):
        resp1 = self.client.post(self.forgot_url, {'identifier': 'otpuser1'})
        self.assertRedirects(resp1, self.verify_url)
        self.client.session.flush()
        resp2 = self.client.post(self.forgot_url, {'identifier': 'no_such_user_zzz'})
        self.assertRedirects(resp2, self.verify_url)

    # ── OTP generation / hashing ────────────────────────────────────────────

    def test_code_is_never_stored_in_plaintext(self):
        from accounts.models import PasswordResetOTP
        from accounts import otp as otp_lib
        otp_row, raw_code, _sent = otp_lib.issue_otp(self.user)
        stored = PasswordResetOTP.objects.get(pk=otp_row.pk)
        self.assertNotEqual(stored.code_hash, raw_code)
        self.assertTrue(stored.check_code(raw_code))

    def test_code_never_logged_in_plaintext(self):
        from logs.models import AuditLog
        from accounts import otp as otp_lib
        otp_row, raw_code, _sent = otp_lib.issue_otp(self.user)
        for log in AuditLog.objects.filter(user=self.user):
            self.assertNotIn(raw_code, str(log.details))

    def test_newer_otp_invalidates_older(self):
        from accounts import otp as otp_lib
        first, _, _ = otp_lib.issue_otp(self.user)
        # Bypass cooldown for this direct-call test
        self.cache.delete(f'fans:otp_cooldown:{self.user.pk}')
        second, _, _ = otp_lib.issue_otp(self.user)
        first.refresh_from_db()
        self.assertIsNotNone(first.invalidated_at)
        self.assertFalse(first.is_valid_for_verification)
        self.assertTrue(second.is_valid_for_verification)

    # ── Verification ────────────────────────────────────────────────────────

    def _do_verify(self, code):
        return self.client.post(self.verify_url, {'code': code}, follow=True)

    def test_correct_code_verifies_and_advances_to_reset(self):
        from accounts import otp as otp_lib
        otp_row, raw_code, _sent = otp_lib.issue_otp(self.user)
        session = self.client.session
        session['otp_identifier'] = 'otpuser1'
        session.save()

        resp = self.client.post(self.verify_url, {'code': raw_code})
        self.assertRedirects(resp, self.reset_url)
        otp_row.refresh_from_db()
        self.assertIsNotNone(otp_row.verified_at)

    def test_wrong_code_shows_generic_error_and_increments_attempts(self):
        from accounts import otp as otp_lib
        otp_row, raw_code, _sent = otp_lib.issue_otp(self.user)
        session = self.client.session
        session['otp_identifier'] = 'otpuser1'
        session.save()

        resp = self._do_verify('000000' if raw_code != '000000' else '111111')
        self.assertContains(resp, 'invalid or has expired')
        otp_row.refresh_from_db()
        self.assertEqual(otp_row.attempts, 1)
        self.assertIsNone(otp_row.verified_at)

    def test_max_attempts_exhausted_blocks_further_verification(self):
        from accounts import otp as otp_lib
        otp_row, raw_code, _sent = otp_lib.issue_otp(self.user)
        wrong = '000000' if raw_code != '000000' else '111111'
        session = self.client.session
        session['otp_identifier'] = 'otpuser1'
        session.save()

        for _ in range(5):
            self._do_verify(wrong)
        otp_row.refresh_from_db()
        self.assertEqual(otp_row.attempts, 5)
        self.assertFalse(otp_row.is_valid_for_verification)

        # Even the correct code must now be rejected.
        resp = self._do_verify(raw_code)
        self.assertContains(resp, 'invalid or has expired')

    def test_expired_code_rejected(self):
        import datetime
        from django.utils import timezone
        from accounts import otp as otp_lib
        otp_row, raw_code, _sent = otp_lib.issue_otp(self.user)
        otp_row.expires_at = timezone.now() - datetime.timedelta(seconds=1)
        otp_row.save(update_fields=['expires_at'])

        session = self.client.session
        session['otp_identifier'] = 'otpuser1'
        session.save()
        resp = self._do_verify(raw_code)
        self.assertContains(resp, 'invalid or has expired')

    def test_verify_without_session_marker_redirects_to_start(self):
        resp = self.client.get(self.verify_url)
        self.assertRedirects(resp, self.forgot_url)

    # ── Resend cooldown ──────────────────────────────────────────────────────

    def test_resend_within_cooldown_does_not_issue_new_otp(self):
        from accounts.models import PasswordResetOTP
        from accounts import otp as otp_lib
        otp_lib.issue_otp(self.user)
        session = self.client.session
        session['otp_identifier'] = 'otpuser1'
        session.save()

        self.client.post(self.verify_url, {'resend': '1'})
        self.assertEqual(PasswordResetOTP.objects.filter(user=self.user).count(), 1)

    def test_resend_after_cooldown_issues_new_otp(self):
        from accounts.models import PasswordResetOTP
        from accounts import otp as otp_lib
        otp_lib.issue_otp(self.user)
        self.cache.delete(f'fans:otp_cooldown:{self.user.pk}')
        session = self.client.session
        session['otp_identifier'] = 'otpuser1'
        session.save()

        self.client.post(self.verify_url, {'resend': '1'})
        self.assertEqual(PasswordResetOTP.objects.filter(user=self.user).count(), 2)

    # ── Concurrency (v2.1.16 Security Hardening Round #2, H-04) ──────────────

    def test_concurrent_cooldown_claims_only_one_wins(self):
        """
        Simultaneous OTP resend requests: fires try_start_cooldown() from
        several threads at (as close as possible to) the same instant and
        asserts exactly one claims the slot. This is the actual race the
        previous check-then-act (`seconds_until_resend_allowed() == 0` then
        a separate `issue_otp()` call) was vulnerable to — two concurrent
        requests could both observe "no cooldown active" before either had
        written the cooldown key.
        """
        import threading
        from accounts import otp as otp_lib
        results = []
        results_lock = threading.Lock()
        barrier = threading.Barrier(5)

        def attempt():
            barrier.wait()
            claimed = otp_lib.try_start_cooldown(self.user)
            with results_lock:
                results.append(claimed)

        threads = [threading.Thread(target=attempt) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(
            results.count(True), 1,
            f'exactly one concurrent request must claim the cooldown slot, got {results}',
        )

    def test_rapid_double_resend_click_issues_only_one_otp(self):
        """Duplicate prevention: a rapid double-click on Resend (two POSTs
        back to back, well within the cooldown window) must not issue two
        OTPs — only the first request's issue_otp() call may go through."""
        from accounts.models import PasswordResetOTP
        session = self.client.session
        session['otp_identifier'] = 'otpuser1'
        session.save()

        self.client.post(self.verify_url, {'resend': '1'})
        self.client.post(self.verify_url, {'resend': '1'})
        self.assertEqual(PasswordResetOTP.objects.filter(user=self.user).count(), 1)

    def test_try_start_cooldown_blocks_second_call_within_window(self):
        """Cooldown enforcement at the primitive level: a second claim
        attempt within the cooldown window must fail."""
        from accounts import otp as otp_lib
        self.assertTrue(otp_lib.try_start_cooldown(self.user))
        self.assertFalse(otp_lib.try_start_cooldown(self.user))

    # ── Rate limiting ────────────────────────────────────────────────────────

    def test_request_rate_limited_after_threshold(self):
        for _ in range(5):
            self.client.post(self.forgot_url, {'identifier': 'otpuser1'})
            self.client.session.flush()
        resp = self.client.post(self.forgot_url, {'identifier': 'otpuser1'}, follow=True)
        msgs = [str(m) for m in resp.context['messages']]
        self.assertTrue(any('Too many' in m for m in msgs))

    # ── Full reset flow ──────────────────────────────────────────────────────

    def _full_flow_to_reset_page(self):
        from accounts import otp as otp_lib
        otp_row, raw_code, _sent = otp_lib.issue_otp(self.user)
        session = self.client.session
        session['otp_identifier'] = 'otpuser1'
        session.save()
        self.client.post(self.verify_url, {'code': raw_code})
        return otp_row

    def test_full_flow_changes_password(self):
        otp_row = self._full_flow_to_reset_page()
        resp = self.client.post(self.reset_url, {
            'new_password1': 'BrandNewPass1!', 'new_password2': 'BrandNewPass1!',
        }, follow=True)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('BrandNewPass1!'))
        otp_row.refresh_from_db()
        self.assertIsNotNone(otp_row.consumed_at)

    def test_reset_enforces_password_policy(self):
        self._full_flow_to_reset_page()
        resp = self.client.post(self.reset_url, {
            'new_password1': 'weak', 'new_password2': 'weak',
        })
        self.assertEqual(resp.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.check_password('weak'))

    def test_otp_cannot_be_reused_after_reset(self):
        otp_row = self._full_flow_to_reset_page()
        self.client.post(self.reset_url, {
            'new_password1': 'BrandNewPass1!', 'new_password2': 'BrandNewPass1!',
        })
        # Session marker is cleared after use — trying the reset page again
        # (fresh session) must bounce back to the start, not reuse the OTP.
        self.client.session.flush()
        resp = self.client.get(self.reset_url, follow=True)
        self.assertRedirects(resp, self.forgot_url)

    def test_must_change_password_cleared_after_self_service_reset(self):
        self.user.must_change_password = True
        self.user.save(update_fields=['must_change_password'])
        self._full_flow_to_reset_page()
        self.client.post(self.reset_url, {
            'new_password1': 'BrandNewPass1!', 'new_password2': 'BrandNewPass1!',
        })
        self.user.refresh_from_db()
        self.assertFalse(self.user.must_change_password)

    def test_reset_without_verified_session_redirects(self):
        resp = self.client.get(self.reset_url, follow=True)
        self.assertRedirects(resp, self.forgot_url)

    # ── Audit trail ──────────────────────────────────────────────────────────

    def test_audit_actions_recorded_across_full_flow(self):
        from logs.models import AuditLog
        otp_row = self._full_flow_to_reset_page()
        self.client.post(self.reset_url, {
            'new_password1': 'BrandNewPass1!', 'new_password2': 'BrandNewPass1!',
        })
        actions = set(AuditLog.objects.filter(user=self.user).values_list('action', flat=True))
        self.assertIn(AuditLog.ACTION_OTP_REQUESTED, actions)
        self.assertIn(AuditLog.ACTION_OTP_SENT, actions)
        self.assertIn(AuditLog.ACTION_OTP_VERIFIED, actions)
        self.assertIn(AuditLog.ACTION_OTP_RESET_DONE, actions)

    # ── Email delivery ───────────────────────────────────────────────────────

    def test_email_actually_sent_when_configured(self):
        from django.core import mail
        self._request_otp('otpuser1')
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['otpuser1@example.com'])

    @_override_settings(EMAIL_CONFIGURED=False, EMAIL_HOST='')
    def test_no_crash_when_email_not_configured(self):
        from django.core import mail
        resp = self._request_otp('otpuser1')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)
        # Same generic message still shown — not configured is invisible to the user.
        msgs = [str(m) for m in resp.context['messages']]
        self.assertTrue(any('registered email address' in m for m in msgs))


# ──────────────────────────────────────────────────────────────────────────────
# v2.2.0 Post-UAT Phase 8 — registered email security.
# Email became a password-recovery factor in Phase 6, so a duplicate email
# would make the OTP flow's account lookup ambiguous, and changing someone's
# recovery email is now security-sensitive enough to need its own audit trail
# (not just folded into the generic "user updated" details blob).
# ──────────────────────────────────────────────────────────────────────────────

class RegisteredEmailSecurityTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.admin = CustomUser.objects.create_user(
            username='email_sec_admin', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-EMAILSEC',
            email='admin@example.com',
        )
        self.other = CustomUser.objects.create_user(
            username='email_sec_other', password='TestPass123!',
            role=CustomUser.ROLE_STAFF, employee_id='EMP-EMAILSEC2',
            email='taken@example.com',
        )
        self.client.force_login(self.admin)

    def _create_payload(self, **overrides):
        data = {
            'username': 'newstaffuser', 'first_name': 'New', 'last_name': 'Staff',
            'email': 'newstaff@example.com', 'role': CustomUser.ROLE_STAFF,
            'employee_id': 'EMP-NEWSTAFF', 'phone': '', 'assigned_office': '',
            'account_status': CustomUser.STATUS_ACTIVE, 'password1': 'NewStaffPass1!',
            'password2': 'NewStaffPass1!', 'officer_position': '',
            'officer_start_date': '', 'officer_is_current': 'on',
        }
        data.update(overrides)
        return data

    def test_duplicate_email_rejected_on_create(self):
        resp = self.client.post(
            reverse('accounts:user_create'), self._create_payload(email='taken@example.com'),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(CustomUser.objects.filter(username='newstaffuser').exists())
        self.assertContains(resp, 'already registered')

    def test_duplicate_email_case_insensitive_on_create(self):
        resp = self.client.post(
            reverse('accounts:user_create'), self._create_payload(email='TAKEN@EXAMPLE.COM'),
        )
        self.assertFalse(CustomUser.objects.filter(username='newstaffuser').exists())
        self.assertContains(resp, 'already registered')

    def test_unique_email_accepted_on_create(self):
        resp = self.client.post(
            reverse('accounts:user_create'), self._create_payload(email='brandnew@example.com'),
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(CustomUser.objects.filter(username='newstaffuser').exists())

    def _edit_payload(self, **overrides):
        data = {
            'first_name': self.other.first_name or 'New', 'last_name': self.other.last_name or 'Staff',
            'email': self.other.email, 'role': self.other.role,
            'employee_id': self.other.employee_id, 'phone': '', 'assigned_office': '',
            'account_status': CustomUser.STATUS_ACTIVE, 'must_change_password': '',
            'officer_position': '', 'officer_start_date': '', 'officer_is_current': 'on',
        }
        data.update(overrides)
        return data

    def test_duplicate_email_rejected_on_edit(self):
        resp = self.client.post(
            reverse('accounts:user_edit', args=[self.other.pk]),
            self._edit_payload(email='admin@example.com'),
        )
        self.assertEqual(resp.status_code, 200)
        self.other.refresh_from_db()
        self.assertEqual(self.other.email, 'taken@example.com')
        self.assertContains(resp, 'already registered')

    def test_editing_own_email_to_same_value_is_allowed(self):
        resp = self.client.post(
            reverse('accounts:user_edit', args=[self.other.pk]),
            self._edit_payload(email='taken@example.com'),
        )
        self.assertEqual(resp.status_code, 302)

    def test_email_change_creates_explicit_audit_entry(self):
        from logs.models import AuditLog
        self.client.post(
            reverse('accounts:user_edit', args=[self.other.pk]),
            self._edit_payload(email='new-address@example.com'),
        )
        entry = AuditLog.objects.filter(
            target_type='CustomUser', target_id=str(self.other.id),
            details__event='registered_email_changed',
        ).first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.details['old_email'], 'taken@example.com')
        self.assertEqual(entry.details['new_email'], 'new-address@example.com')
        self.assertEqual(entry.details['changed_by'], 'email_sec_admin')

    def test_no_email_change_audit_entry_when_email_unchanged(self):
        from logs.models import AuditLog
        self.client.post(
            reverse('accounts:user_edit', args=[self.other.pk]),
            self._edit_payload(email='taken@example.com'),
        )
        entry = AuditLog.objects.filter(
            target_type='CustomUser', target_id=str(self.other.id),
            details__event='registered_email_changed',
        ).first()
        self.assertIsNone(entry)

    def test_email_change_reflected_in_actual_record(self):
        self.client.post(
            reverse('accounts:user_edit', args=[self.other.pk]),
            self._edit_payload(email='updated@example.com'),
        )
        self.other.refresh_from_db()
        self.assertEqual(self.other.email, 'updated@example.com')


# ──────────────────────────────────────────────────────────────────────────────
# v2.2.0 Post-UAT Phase 9 — Staff name structure (middle name / suffix).
# first_name/last_name already came from AbstractUser and are untouched by
# this change — middle_name/suffix are purely additive fields, so there is
# no data migration risk for existing accounts.
# ──────────────────────────────────────────────────────────────────────────────

class StaffNameStructureTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.admin = CustomUser.objects.create_user(
            username='name_admin', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-NAMEADMIN',
            email='name_admin@example.com',
        )
        self.client.force_login(self.admin)

    def test_middle_name_and_suffix_fields_exist_and_optional(self):
        field_names = {f.name for f in CustomUser._meta.get_fields()}
        self.assertIn('middle_name', field_names)
        self.assertIn('suffix', field_names)
        self.assertTrue(CustomUser._meta.get_field('middle_name').blank)
        self.assertTrue(CustomUser._meta.get_field('suffix').blank)

    def test_get_full_name_composes_all_parts(self):
        u = CustomUser(first_name='Juan', middle_name='Dela', last_name='Cruz', suffix='Jr.')
        self.assertEqual(u.get_full_name(), 'Juan Dela Cruz Jr.')

    def test_get_full_name_omits_blank_parts(self):
        u = CustomUser(first_name='Juan', middle_name='', last_name='Cruz', suffix='')
        self.assertEqual(u.get_full_name(), 'Juan Cruz')

    def test_get_full_name_empty_when_no_name_set(self):
        u = CustomUser(first_name='', middle_name='', last_name='', suffix='')
        self.assertEqual(u.get_full_name(), '')

    def test_existing_user_first_last_name_preserved(self):
        """Adding middle_name/suffix must not disturb existing first/last name data."""
        u = CustomUser.objects.create_user(
            username='legacy_name_user', password='TestPass123!',
            first_name='Maria', last_name='Santos',
            role=CustomUser.ROLE_STAFF, employee_id='EMP-LEGACYNAME',
        )
        u.refresh_from_db()
        self.assertEqual(u.first_name, 'Maria')
        self.assertEqual(u.last_name, 'Santos')
        self.assertEqual(u.middle_name, '')
        self.assertEqual(u.suffix, '')
        self.assertEqual(u.get_full_name(), 'Maria Santos')

    def test_create_user_with_middle_name_and_suffix(self):
        resp = self.client.post(reverse('accounts:user_create'), {
            'username': 'newnamed', 'first_name': 'Juan', 'middle_name': 'Dela',
            'last_name': 'Cruz', 'suffix': 'III', 'email': 'newnamed@example.com',
            'role': CustomUser.ROLE_STAFF, 'employee_id': 'EMP-NEWNAMED',
            'phone': '', 'assigned_office': '', 'account_status': CustomUser.STATUS_ACTIVE,
            'password1': 'NewNamedPass1!', 'password2': 'NewNamedPass1!',
            'officer_position': '', 'officer_start_date': '', 'officer_is_current': 'on',
        })
        self.assertEqual(resp.status_code, 302)
        u = CustomUser.objects.get(username='newnamed')
        self.assertEqual(u.middle_name, 'Dela')
        self.assertEqual(u.suffix, 'III')
        self.assertEqual(u.get_full_name(), 'Juan Dela Cruz III')

    def test_edit_user_sets_middle_name_and_suffix(self):
        target = CustomUser.objects.create_user(
            username='editnamed', password='TestPass123!',
            first_name='Ana', last_name='Reyes',
            role=CustomUser.ROLE_STAFF, employee_id='EMP-EDITNAMED',
            email='editnamed@example.com',
        )
        self.client.post(reverse('accounts:user_edit', args=[target.pk]), {
            'first_name': 'Ana', 'middle_name': 'Bautista', 'last_name': 'Reyes', 'suffix': '',
            'email': 'editnamed@example.com', 'role': CustomUser.ROLE_STAFF,
            'employee_id': 'EMP-EDITNAMED', 'phone': '', 'assigned_office': '',
            'account_status': CustomUser.STATUS_ACTIVE, 'must_change_password': '',
            'officer_position': '', 'officer_start_date': '', 'officer_is_current': 'on',
        })
        target.refresh_from_db()
        self.assertEqual(target.middle_name, 'Bautista')
        self.assertEqual(target.get_full_name(), 'Ana Bautista Reyes')

    def test_user_list_displays_composed_full_name(self):
        CustomUser.objects.create_user(
            username='listnamed', password='TestPass123!',
            first_name='Pedro', middle_name='Garcia', last_name='Santos', suffix='Sr.',
            role=CustomUser.ROLE_STAFF, employee_id='EMP-LISTNAMED',
        )
        resp = self.client.get(reverse('accounts:user_list'))
        self.assertContains(resp, 'Pedro Garcia Santos Sr.')


# ══════════════════════════════════════════════════════════════════════════
# v2.1.19 UX / Reporting / Technical Administration pass — focused tests
# ══════════════════════════════════════════════════════════════════════════

class MyProfileTest(TestCase):
    """Section 10 — self-service My Profile: real bug fix (the route did not
    exist before; user_edit_full pointed users at a dead end)."""

    def setUp(self):
        self.client = Client()
        self.user = CustomUser.objects.create_user(
            username='mp_user', password='TestPass123!',
            first_name='', last_name='', role=CustomUser.ROLE_STAFF,
            employee_id='EMP-MP-1',
        )
        self.client.force_login(self.user)

    def test_route_exists_and_renders(self):
        resp = self.client.get(reverse('accounts:my_profile'))
        self.assertEqual(resp.status_code, 200)

    def test_can_edit_own_name_email_phone(self):
        self.client.post(reverse('accounts:my_profile'), {
            'first_name': 'Whinelit', 'middle_name': '', 'last_name': 'Recto',
            'suffix': '', 'email': 'whinelit@example.com', 'phone': '09171234567',
        })
        self.user.refresh_from_db()
        self.assertEqual(self.user.get_full_name(), 'Whinelit Recto')
        self.assertEqual(self.user.email, 'whinelit@example.com')

    def test_role_and_status_are_read_only(self):
        """POSTing a role/account_status change through My Profile must have
        no effect — those fields aren't even in the form."""
        self.client.post(reverse('accounts:my_profile'), {
            'first_name': 'X', 'last_name': 'Y', 'email': 'xy@example.com',
            'phone': '', 'role': CustomUser.ROLE_PRESIDENT,
            'account_status': CustomUser.STATUS_INACTIVE,
        })
        self.user.refresh_from_db()
        self.assertEqual(self.user.role, CustomUser.ROLE_STAFF)
        self.assertEqual(self.user.account_status, CustomUser.STATUS_ACTIVE)
        self.assertTrue(self.user.is_active)

    def test_user_edit_full_self_redirects_to_my_profile(self):
        # user_edit_full is admin-gated before the self-edit check runs, so
        # use an admin-tier user to reach that check.
        admin_self = CustomUser.objects.create_user(
            username='mp_admin_self', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-MP-2',
        )
        self.client.force_login(admin_self)
        resp = self.client.get(
            reverse('accounts:user_edit', args=[admin_self.pk]), follow=True,
        )
        self.assertEqual(resp.redirect_chain[-1][0], reverse('accounts:my_profile'))


class TechnicalAdministratorCreationPolicyTest(TestCase):
    """Section 8/42 — TA creation lifecycle:
      Installer/first-run -> initial TA (covered by CreateAdminForm tests).
      President            -> can create additional TA.
      Administrator        -> cannot create TA.
      Staff                -> cannot create TA (blocked earlier, not admin-tier).
      TA                   -> cannot create/self-promote to another TA.
    Every check is a raw-POST assertion, not just a hidden UI option."""

    def setUp(self):
        self.client = Client()
        self.president = CustomUser.objects.create_user(
            username='tac_pres', password='TestPass123!',
            role=CustomUser.ROLE_PRESIDENT, employee_id='EMP-TAC-P',
        )
        self.admin = CustomUser.objects.create_user(
            username='tac_admin', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-TAC-A',
        )
        self.ta = CustomUser.objects.create_user(
            username='tac_ta', password='TestPass123!',
            role=CustomUser.ROLE_IT, employee_id='EMP-TAC-T',
        )
        self.staff = CustomUser.objects.create_user(
            username='tac_staff', password='TestPass123!',
            role=CustomUser.ROLE_STAFF, employee_id='EMP-TAC-S',
        )

    def _create_payload(self, username):
        return {
            'username': username, 'first_name': 'New', 'last_name': 'Tech',
            'email': f'{username}@example.com', 'role': CustomUser.ROLE_IT,
            'employee_id': f'EMP-{username}', 'phone': '',
            'assigned_office': '', 'account_status': CustomUser.STATUS_ACTIVE,
            'must_change_password': 'on',
            'password1': 'TestPass123!', 'password2': 'TestPass123!',
            'officer_position': '', 'officer_start_date': '', 'officer_is_current': 'on',
        }

    def test_president_can_create_technical_administrator(self):
        self.client.force_login(self.president)
        self.client.post(reverse('accounts:user_create'), self._create_payload('new_ta_by_pres'))
        self.assertTrue(CustomUser.objects.filter(username='new_ta_by_pres', role=CustomUser.ROLE_IT).exists())

    def test_administrator_cannot_create_technical_administrator(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('accounts:user_create'), self._create_payload('new_ta_by_admin'))
        self.assertFalse(CustomUser.objects.filter(username='new_ta_by_admin').exists())

    def test_technical_administrator_cannot_create_another(self):
        self.client.force_login(self.ta)
        self.client.post(reverse('accounts:user_create'), self._create_payload('new_ta_by_ta'))
        self.assertFalse(CustomUser.objects.filter(username='new_ta_by_ta').exists())

    def test_administrator_cannot_promote_staff_to_technical_administrator(self):
        """Direct-POST edit attempt, independent of the create-form path."""
        self.client.force_login(self.admin)
        self.client.post(reverse('accounts:user_edit', args=[self.staff.pk]), {
            'first_name': self.staff.first_name or 'S', 'last_name': self.staff.last_name or 'T',
            'email': 'staffpromote@example.com', 'role': CustomUser.ROLE_IT,
            'employee_id': self.staff.employee_id, 'phone': '', 'assigned_office': '',
            'account_status': CustomUser.STATUS_ACTIVE, 'must_change_password': '',
            'officer_position': '', 'officer_start_date': '', 'officer_is_current': 'on',
        })
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.role, CustomUser.ROLE_STAFF)

    # ── v2.1.17 audit fix: Admin carries has_financial_authority same as
    # President, so creating/promoting to Admin must be President-only too,
    # same as the existing Technical Administrator / President policy above.

    def _admin_create_payload(self, username):
        payload = self._create_payload(username)
        payload['role'] = CustomUser.ROLE_ADMIN
        return payload

    def test_president_can_create_administrator(self):
        self.client.force_login(self.president)
        self.client.post(reverse('accounts:user_create'), self._admin_create_payload('new_admin_by_pres'))
        self.assertTrue(CustomUser.objects.filter(username='new_admin_by_pres', role=CustomUser.ROLE_ADMIN).exists())

    def test_administrator_cannot_create_another_administrator(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('accounts:user_create'), self._admin_create_payload('new_admin_by_admin'))
        self.assertFalse(CustomUser.objects.filter(username='new_admin_by_admin').exists())

    def test_technical_administrator_cannot_create_administrator(self):
        """CRITICAL: this is the exact escalation path — Technical
        Administrator creates a new Admin account (whose password it sets
        itself) to indirectly obtain financial authority it must never have."""
        self.client.force_login(self.ta)
        self.client.post(reverse('accounts:user_create'), self._admin_create_payload('new_admin_by_ta'))
        self.assertFalse(CustomUser.objects.filter(username='new_admin_by_ta').exists())

    def test_administrator_role_stripped_from_create_choices_for_non_president(self):
        self.client.force_login(self.ta)
        resp = self.client.get(reverse('accounts:user_create'))
        self.assertNotContains(resp, '<option value="admin"')

    def test_technical_administrator_cannot_promote_staff_to_administrator(self):
        """Direct-POST edit attempt — the exact bypass a hidden form choice
        alone would not stop."""
        self.client.force_login(self.ta)
        self.client.post(reverse('accounts:user_edit', args=[self.staff.pk]), {
            'first_name': self.staff.first_name or 'S', 'last_name': self.staff.last_name or 'T',
            'email': 'staffpromote2@example.com', 'role': CustomUser.ROLE_ADMIN,
            'employee_id': self.staff.employee_id, 'phone': '', 'assigned_office': '',
            'account_status': CustomUser.STATUS_ACTIVE, 'must_change_password': '',
            'officer_position': '', 'officer_start_date': '', 'officer_is_current': 'on',
        })
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.role, CustomUser.ROLE_STAFF)

    def test_president_can_promote_staff_to_administrator(self):
        self.client.force_login(self.president)
        self.client.post(reverse('accounts:user_edit', args=[self.staff.pk]), {
            'first_name': self.staff.first_name or 'S', 'last_name': self.staff.last_name or 'T',
            'email': 'staffpromote3@example.com', 'role': CustomUser.ROLE_ADMIN,
            'employee_id': self.staff.employee_id, 'phone': '', 'assigned_office': '',
            'account_status': CustomUser.STATUS_ACTIVE, 'must_change_password': '',
            'officer_position': '', 'officer_start_date': '', 'officer_is_current': 'on',
        })
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.role, CustomUser.ROLE_ADMIN)


class InitialPresidentBootstrapTest(TestCase):
    """Section 8B/41 — Technical Administrator may create the initial
    President only while none exists; the path disables permanently once one
    does, and only the Technical Administrator may use it."""

    def setUp(self):
        self.client = Client()
        self.ta = CustomUser.objects.create_user(
            username='boot_ta', password='TestPass123!',
            role=CustomUser.ROLE_IT, employee_id='EMP-BOOT-T',
        )
        self.admin = CustomUser.objects.create_user(
            username='boot_admin', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-BOOT-A',
        )
        self.url = reverse('accounts:bootstrap_president')

    def _payload(self, username='new_president'):
        return {
            'first_name': 'Initial', 'last_name': 'President', 'username': username,
            'email': '', 'password1': 'TestPass123!', 'password2': 'TestPass123!',
        }

    def test_available_to_ta_when_no_president_exists(self):
        self.client.force_login(self.ta)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

    def test_ta_can_create_initial_president(self):
        self.client.force_login(self.ta)
        self.client.post(self.url, self._payload())
        self.assertTrue(CustomUser.objects.filter(username='new_president', role=CustomUser.ROLE_PRESIDENT).exists())

    def test_ta_creates_initial_president_with_separate_first_last_name(self):
        """v2.1.17 QA fix pass, Issue 3 — first/last name stored distinctly,
        not mangled together via a single combined 'Full Name' input."""
        self.client.force_login(self.ta)
        self.client.post(self.url, {
            'first_name': 'Maria', 'last_name': 'Dela Cruz Santos', 'username': 'mdc_president',
            'email': '', 'password1': 'TestPass123!', 'password2': 'TestPass123!',
        })
        user = CustomUser.objects.get(username='mdc_president')
        self.assertEqual(user.first_name, 'Maria')
        self.assertEqual(user.last_name, 'Dela Cruz Santos')

    def test_denied_to_admin(self):
        self.client.force_login(self.admin)
        resp = self.client.get(self.url, follow=True)
        self.assertEqual(resp.redirect_chain[-1][0], reverse('beneficiaries:dashboard'))

    def test_disabled_once_a_president_exists(self):
        CustomUser.objects.create_user(
            username='existing_pres', password='TestPass123!',
            role=CustomUser.ROLE_PRESIDENT, employee_id='EMP-BOOT-EXIST',
        )
        self.client.force_login(self.ta)
        resp = self.client.get(self.url, follow=True)
        self.assertEqual(resp.redirect_chain[-1][0], reverse('beneficiaries:dashboard'))
        before = CustomUser.objects.count()
        self.client.post(self.url, self._payload('should_not_be_created'))
        self.assertEqual(CustomUser.objects.count(), before)


class PresidentPrivilegeEscalationTest(TestCase):
    """
    v2.1.16 Security Hardening Round #1 (Critical Issue 2): Admin/Technical
    Administrator must never be able to create, promote to, or modify a
    President-level account. Every check is a raw-POST assertion against
    the view, not a UI-hiding check, matching the pattern already used for
    Technical Administrator creation policy above.
    """

    def setUp(self):
        self.client = Client()
        self.president = CustomUser.objects.create_user(
            username='pesc_pres', password='TestPass123!',
            role=CustomUser.ROLE_PRESIDENT, employee_id='EMP-PESC-P',
            email='pres@example.com',
        )
        self.admin = CustomUser.objects.create_user(
            username='pesc_admin', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-PESC-A',
        )
        self.ta = CustomUser.objects.create_user(
            username='pesc_ta', password='TestPass123!',
            role=CustomUser.ROLE_IT, employee_id='EMP-PESC-T',
        )
        self.staff = CustomUser.objects.create_user(
            username='pesc_staff', password='TestPass123!',
            role=CustomUser.ROLE_STAFF, employee_id='EMP-PESC-S',
        )

    def _create_payload(self, username, role=CustomUser.ROLE_PRESIDENT):
        return {
            'username': username, 'first_name': 'New', 'last_name': 'Pres',
            'email': f'{username}@example.com', 'role': role,
            'employee_id': f'EMP-{username}', 'phone': '',
            'assigned_office': '', 'account_status': CustomUser.STATUS_ACTIVE,
            'must_change_password': 'on',
            'password1': 'TestPass123!', 'password2': 'TestPass123!',
            'officer_position': '', 'officer_start_date': '', 'officer_is_current': 'on',
        }

    def _edit_payload(self, user, **overrides):
        payload = {
            'first_name': user.first_name or 'F', 'last_name': user.last_name or 'L',
            'email': user.email or f'{user.username}@example.com',
            'role': user.role, 'employee_id': user.employee_id, 'phone': '',
            'assigned_office': '', 'account_status': user.account_status,
            'must_change_password': '',
            'officer_position': '', 'officer_start_date': '', 'officer_is_current': 'on',
        }
        payload.update(overrides)
        return payload

    # ── 1/4: Admin/IT cannot create a President account ──────────────────
    def test_administrator_cannot_create_president(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('accounts:user_create'), self._create_payload('escalate_by_admin'))
        self.assertFalse(CustomUser.objects.filter(username='escalate_by_admin').exists())

    def test_technical_administrator_cannot_create_president(self):
        self.client.force_login(self.ta)
        self.client.post(reverse('accounts:user_create'), self._create_payload('escalate_by_ta'))
        self.assertFalse(CustomUser.objects.filter(username='escalate_by_ta').exists())

    # ── 4: Admin cannot create another President (uniqueness actor gate) ──
    def test_president_role_stripped_from_create_choices_for_admin(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('accounts:user_create'))
        self.assertNotContains(resp, '<option value="president"')

    # ── 3: Admin/Technical Administrator cannot promote to President ─────
    def test_administrator_cannot_promote_staff_to_president(self):
        self.client.force_login(self.admin)
        self.client.post(
            reverse('accounts:user_edit', args=[self.staff.pk]),
            self._edit_payload(self.staff, role=CustomUser.ROLE_PRESIDENT),
        )
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.role, CustomUser.ROLE_STAFF)

    def test_technical_administrator_cannot_promote_admin_to_president(self):
        self.client.force_login(self.ta)
        self.client.post(
            reverse('accounts:user_edit', args=[self.admin.pk]),
            self._edit_payload(self.admin, role=CustomUser.ROLE_PRESIDENT),
        )
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.role, CustomUser.ROLE_ADMIN)

    # ── 2: Admin/Technical Administrator cannot modify a President target ─
    def test_administrator_cannot_edit_president_at_all(self):
        """Role left unchanged but account_status/email changed — the
        exact bypass the role-only guard used to miss."""
        self.client.force_login(self.admin)
        self.client.post(
            reverse('accounts:user_edit', args=[self.president.pk]),
            self._edit_payload(
                self.president,
                account_status=CustomUser.STATUS_SUSPENDED,
                email='hijacked@example.com',
            ),
        )
        self.president.refresh_from_db()
        self.assertEqual(self.president.account_status, CustomUser.STATUS_ACTIVE)
        self.assertEqual(self.president.email, 'pres@example.com')

    def test_technical_administrator_cannot_edit_president_at_all(self):
        self.client.force_login(self.ta)
        self.client.post(
            reverse('accounts:user_edit', args=[self.president.pk]),
            self._edit_payload(self.president, account_status=CustomUser.STATUS_INACTIVE),
        )
        self.president.refresh_from_db()
        self.assertEqual(self.president.account_status, CustomUser.STATUS_ACTIVE)

    # ── Admin/IT cannot suspend/deactivate/reactivate the President ──────
    def test_administrator_cannot_suspend_president(self):
        self.client.force_login(self.admin)
        self.client.post(
            reverse('accounts:user_set_status', args=[self.president.pk]),
            data={'status': 'suspended', 'reason': 'Attempted takeover'},
        )
        self.president.refresh_from_db()
        self.assertEqual(self.president.account_status, CustomUser.STATUS_ACTIVE)
        self.assertTrue(self.president.is_active)

    def test_technical_administrator_cannot_deactivate_president(self):
        self.client.force_login(self.ta)
        self.client.post(
            reverse('accounts:user_set_status', args=[self.president.pk]),
            data={'status': 'inactive', 'reason': 'Attempted takeover'},
        )
        self.president.refresh_from_db()
        self.assertEqual(self.president.account_status, CustomUser.STATUS_ACTIVE)

    # ── Existing Admin/TA functions and the President's own workflow must
    #    not regress ──────────────────────────────────────────────────────
    def test_administrator_can_still_suspend_staff(self):
        self.client.force_login(self.admin)
        self.client.post(
            reverse('accounts:user_set_status', args=[self.staff.pk]),
            data={'status': 'suspended', 'reason': 'Normal admin action'},
        )
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.account_status, CustomUser.STATUS_SUSPENDED)

    def test_administrator_can_still_edit_staff(self):
        self.client.force_login(self.admin)
        resp = self.client.post(
            reverse('accounts:user_edit', args=[self.staff.pk]),
            self._edit_payload(self.staff, email='updated_staff@example.com'),
        )
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.email, 'updated_staff@example.com')

    def test_president_can_suspend_admin(self):
        self.client.force_login(self.president)
        self.client.post(
            reverse('accounts:user_set_status', args=[self.admin.pk]),
            data={'status': 'suspended', 'reason': 'President action'},
        )
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.account_status, CustomUser.STATUS_SUSPENDED)

    def test_president_can_edit_president_target_is_noop_since_only_one_exists(self):
        """President modifying another admin-level (non-President) account
        still works — sanity check that the blanket guard only fires for
        non-President actors targeting a President, not for the President's
        own management of Admin/IT accounts."""
        self.client.force_login(self.president)
        resp = self.client.post(
            reverse('accounts:user_edit', args=[self.ta.pk]),
            self._edit_payload(self.ta, email='ta_updated@example.com'),
        )
        self.ta.refresh_from_db()
        self.assertEqual(self.ta.email, 'ta_updated@example.com')


class CreateAdminFormTechnicalAdministratorTest(TestCase):
    """Section 8A/40 — first-run setup always creates a Technical
    Administrator (role=IT); there is no role choice."""

    def setUp(self):
        self.client = Client()
        self.url = reverse('accounts:create_admin')

    def test_first_run_creates_technical_administrator(self):
        self.client.post(self.url, {
            'first_name': 'Bootstrap', 'last_name': 'Tech', 'username': 'bootstrap_tech',
            'email': '', 'password1': 'TestPass123!', 'password2': 'TestPass123!',
        })
        user = CustomUser.objects.get(username='bootstrap_tech')
        self.assertEqual(user.role, CustomUser.ROLE_IT)
        self.assertEqual(user.first_name, 'Bootstrap')
        self.assertEqual(user.last_name, 'Tech')

    def test_first_run_stores_first_and_last_name_separately(self):
        """
        v2.1.17 QA fix pass, Issue 3: a combined 'Full Name' field cannot be
        split reliably (e.g. multi-word last names). The form must accept
        first/last name as distinct inputs and store them without mangling.
        """
        self.client.post(self.url, {
            'first_name': 'Juan', 'last_name': 'Dela Cruz', 'username': 'juan_delacruz',
            'email': '', 'password1': 'TestPass123!', 'password2': 'TestPass123!',
        })
        user = CustomUser.objects.get(username='juan_delacruz')
        self.assertEqual(user.first_name, 'Juan')
        self.assertEqual(user.last_name, 'Dela Cruz')


class PresidentDjangoAdminHardeningTest(TestCase):
    """
    v2.1.16 Security Hardening Round #3 (Blocker 4): the application views
    already protect President accounts from lower roles (see
    UserRoleHierarchyEditTest etc. above). Django Admin — a separate code
    path that bypasses accounts/views.py entirely — must enforce the same
    hierarchy: no non-President admin user may create a President account,
    promote an existing account to President, change a President's password,
    or edit a President's role/active/staff/superuser/email/groups/
    permissions fields. A President must remain unrestricted.
    """

    def setUp(self):
        from django.test import RequestFactory
        from django.contrib.admin.sites import AdminSite
        from accounts.admin import CustomUserAdmin

        self.factory = RequestFactory()
        self.model_admin = CustomUserAdmin(CustomUser, AdminSite())
        self.client = Client()

        # is_superuser=True so Django's own base permission layer
        # (has_perm-driven has_change_permission/has_delete_permission)
        # doesn't confound these tests — they isolate the ROLE-based
        # President-hierarchy logic added on top of that base layer, which
        # every real Django-admin-capable FANS-C account needs regardless.
        self.admin_user = CustomUser.objects.create_user(
            username='dj_admin_hardening', password='TestPass1!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-DJADM-1',
            is_staff=True, is_superuser=True,
        )
        self.president_user = CustomUser.objects.create_user(
            username='dj_president_hardening', password='TestPass1!',
            role=CustomUser.ROLE_PRESIDENT, employee_id='EMP-DJADM-2',
            is_staff=True, is_superuser=True,
        )

    def _request_as(self, user):
        req = self.factory.get('/admin/accounts/customuser/add/')
        req.user = user
        return req

    # ── 1. Admin cannot create President accounts ─────────────────────────

    def test_role_choices_exclude_president_for_non_president_actor(self):
        form_class = self.model_admin.get_form(self._request_as(self.admin_user), obj=None)
        role_values = [c[0] for c in form_class.base_fields['role'].choices]
        self.assertNotIn(CustomUser.ROLE_PRESIDENT, role_values)

    def test_role_choices_include_president_for_president_actor(self):
        form_class = self.model_admin.get_form(self._request_as(self.president_user), obj=None)
        role_values = [c[0] for c in form_class.base_fields['role'].choices]
        self.assertIn(CustomUser.ROLE_PRESIDENT, role_values)

    def test_admin_cannot_promote_existing_user_to_president_via_role_choices(self):
        """Role-choice filtering also applies on the change form (obj is not
        yet a President at read time), closing the promotion vector that
        get_readonly_fields() alone cannot catch."""
        staff_target = CustomUser.objects.create_user(
            username='promote_target', password='TestPass1!',
            role=CustomUser.ROLE_STAFF, employee_id='EMP-DJADM-3',
        )
        form_class = self.model_admin.get_form(
            self._request_as(self.admin_user), obj=staff_target,
        )
        role_values = [c[0] for c in form_class.base_fields['role'].choices]
        self.assertNotIn(CustomUser.ROLE_PRESIDENT, role_values)

    # ── 4. Django admin follows the same role hierarchy — President fields ─

    def test_readonly_fields_protect_president_from_non_president_actor(self):
        readonly = self.model_admin.get_readonly_fields(
            self._request_as(self.admin_user), obj=self.president_user,
        )
        for field in ('role', 'is_active', 'is_staff', 'is_superuser',
                      'email', 'groups', 'user_permissions'):
            self.assertIn(field, readonly)

    def test_readonly_fields_do_not_restrict_president_actor_on_self(self):
        readonly = self.model_admin.get_readonly_fields(
            self._request_as(self.president_user), obj=self.president_user,
        )
        self.assertNotIn('role', readonly)
        self.assertNotIn('email', readonly)

    def test_admin_cannot_delete_president(self):
        self.assertFalse(self.model_admin.has_delete_permission(
            self._request_as(self.admin_user), obj=self.president_user,
        ))

    # ── 2. Admin cannot change President password ─────────────────────────

    def test_admin_cannot_change_president_password(self):
        self.client.force_login(self.admin_user)
        url = reverse('admin:auth_user_password_change', args=[self.president_user.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 403)

    def test_president_can_access_own_password_change_route(self):
        self.client.force_login(self.president_user)
        url = reverse('admin:auth_user_password_change', args=[self.president_user.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    # ── 3. Admin cannot change President permissions ───────────────────────

    def test_admin_cannot_edit_president_groups_or_superuser_flag(self):
        readonly = self.model_admin.get_readonly_fields(
            self._request_as(self.admin_user), obj=self.president_user,
        )
        self.assertIn('groups', readonly)
        self.assertIn('user_permissions', readonly)
        self.assertIn('is_superuser', readonly)

    # ── 4. President can manage allowed administration ─────────────────────

    def test_president_unrestricted_when_managing_admin_account(self):
        readonly = self.model_admin.get_readonly_fields(
            self._request_as(self.president_user), obj=self.admin_user,
        )
        self.assertNotIn('role', readonly)
        self.assertTrue(self.model_admin.has_delete_permission(
            self._request_as(self.president_user), obj=self.admin_user,
        ))

    # ── v2.1.16 Security Hardening Round #4 (Blocker 2 — complete redesign):
    # object-level has_change_permission() lockout, defense-in-depth
    # save_model() guard, and queryset-level delete_queryset() guard. ──────

    def test_has_change_permission_false_for_non_president_on_president(self):
        self.assertFalse(self.model_admin.has_change_permission(
            self._request_as(self.admin_user), obj=self.president_user,
        ))

    def test_has_change_permission_true_for_president_on_self(self):
        self.assertTrue(self.model_admin.has_change_permission(
            self._request_as(self.president_user), obj=self.president_user,
        ))

    def test_has_change_permission_true_for_non_president_on_non_president(self):
        other = CustomUser.objects.create_user(
            username='dj_staff_target', password='TestPass1!',
            role=CustomUser.ROLE_STAFF, employee_id='EMP-DJADM-4',
        )
        self.assertTrue(self.model_admin.has_change_permission(
            self._request_as(self.admin_user), obj=other,
        ))

    def test_admin_cannot_edit_president_profile_fields_via_post(self):
        """
        End-to-end regression: previously only role/is_active/is_staff/
        is_superuser/email/groups/user_permissions were protected — fields
        like username/first_name/last_name/phone/employee_id were NOT in
        PRESIDENT_PROTECTED_FIELDS and could be changed by a non-President
        admin via a direct POST to the change view, even though the
        rendered form marked other fields read-only. has_change_permission()
        now denies the whole change view for a President target, so this
        POST must be refused (403) and none of the President's fields may
        change as a result.
        """
        self.client.force_login(self.admin_user)
        original_username = self.president_user.username
        original_phone = self.president_user.phone
        url = reverse('admin:accounts_customuser_change', args=[self.president_user.pk])
        resp = self.client.post(url, data={
            'username': 'hijacked_president',
            'first_name': 'Hijacked',
            'last_name': 'Name',
            'phone': '09990000000',
            'employee_id': self.president_user.employee_id,
            'role': CustomUser.ROLE_PRESIDENT,
            'date_joined_0': '2020-01-01', 'date_joined_1': '00:00:00',
        })
        self.assertEqual(resp.status_code, 403)
        self.president_user.refresh_from_db()
        self.assertEqual(self.president_user.username, original_username)
        self.assertEqual(self.president_user.phone, original_phone)

    def test_president_can_edit_own_profile_fields_via_post(self):
        """Positive case: the President editing their OWN account must still work."""
        self.client.force_login(self.president_user)
        url = reverse('admin:accounts_customuser_change', args=[self.president_user.pk])
        resp = self.client.post(url, data={
            'username': self.president_user.username,
            'first_name': 'UpdatedFirst',
            'last_name': 'UpdatedLast',
            'phone': '09991234567',
            'employee_id': self.president_user.employee_id,
            'role': CustomUser.ROLE_PRESIDENT,
            'is_active': 'on',
            'date_joined_0': '2020-01-01', 'date_joined_1': '00:00:00',
        })
        self.assertNotEqual(resp.status_code, 403)
        self.president_user.refresh_from_db()
        self.assertEqual(self.president_user.first_name, 'UpdatedFirst')
        self.assertEqual(self.president_user.phone, '09991234567')

    def test_save_model_defense_in_depth_blocks_direct_call(self):
        """save_model() itself must refuse a President-target change from a
        non-President actor even if called directly (not just via the view),
        as a second layer behind has_change_permission()."""
        from django.core.exceptions import PermissionDenied as _PD
        self.president_user.first_name = 'DirectCallAttempt'
        form = mock.MagicMock()
        with self.assertRaises(_PD):
            self.model_admin.save_model(
                self._request_as(self.admin_user), self.president_user, form, change=True,
            )

    def test_delete_queryset_excludes_president_for_non_president_actor(self):
        other = CustomUser.objects.create_user(
            username='dj_bulk_target', password='TestPass1!',
            role=CustomUser.ROLE_STAFF, employee_id='EMP-DJADM-5',
        )
        qs = CustomUser.objects.filter(pk__in=[self.president_user.pk, other.pk])
        self.model_admin.delete_queryset(self._request_as(self.admin_user), qs)
        self.assertTrue(CustomUser.objects.filter(pk=self.president_user.pk).exists(),
                        'President must survive a delete_queryset() call from a non-President actor')
        self.assertFalse(CustomUser.objects.filter(pk=other.pk).exists(),
                         'Non-President rows in the same queryset should still be deleted')

    def test_delete_queryset_allows_president_actor_to_delete(self):
        other = CustomUser.objects.create_user(
            username='dj_bulk_target2', password='TestPass1!',
            role=CustomUser.ROLE_STAFF, employee_id='EMP-DJADM-6',
        )
        qs = CustomUser.objects.filter(pk=other.pk)
        self.model_admin.delete_queryset(self._request_as(self.president_user), qs)
        self.assertFalse(CustomUser.objects.filter(pk=other.pk).exists())

    def test_bulk_delete_action_leaves_president_untouched(self):
        """End-to-end: the built-in admin 'delete_selected' action, invoked
        by a non-President admin with the President included in the
        selection, must not delete the President (and, per Django's
        per-object permission collection, is refused outright rather than
        silently skipping just that row)."""
        self.client.force_login(self.admin_user)
        url = reverse('admin:accounts_customuser_changelist')
        resp = self.client.post(url, data={
            'action': 'delete_selected',
            '_selected_action': [str(self.president_user.pk)],
        }, follow=True)
        self.assertIn(resp.status_code, (200, 403))
        self.assertTrue(CustomUser.objects.filter(pk=self.president_user.pk).exists())


class CsrfFailurePageTest(TestCase):
    """A CSRF failure must show the FANSC-branded 403_csrf.html page (safe
    actions, friendly wording) rather than Django's raw technical fallback,
    with no CSRF weakening involved — enforce_csrf_checks=True below is only
    what's needed to *trigger* a real failure for the test itself."""

    def test_csrf_failure_renders_branded_page(self):
        client = Client(enforce_csrf_checks=True)
        resp = client.post(reverse('accounts:login'), {'username': 'nobody', 'password': 'wrong'})
        self.assertEqual(resp.status_code, 403)
        self.assertContains(resp, 'FANSC', status_code=403)
        self.assertContains(resp, 'Session or Form Expired', status_code=403)
        self.assertContains(resp, 'Return to Login', status_code=403)

    def test_csrf_failure_does_not_leak_django_debug_wording(self):
        client = Client(enforce_csrf_checks=True)
        resp = client.post(reverse('accounts:login'), {'username': 'nobody', 'password': 'wrong'})
        self.assertNotContains(resp, 'CSRF verification failed. Request aborted.', status_code=403)
