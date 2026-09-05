"""
Tests for the logs app: AuditLog model and audit log view access.
"""

from django.test import TestCase, Client
from accounts.models import CustomUser
from logs.models import AuditLog


def _make_admin():
    return CustomUser.objects.create_user(
        username='audit_admin',
        password='TestPass123!',
        role=CustomUser.ROLE_IT,
        employee_id='EMP-AUDIT',
    )


def _make_staff():
    return CustomUser.objects.create_user(
        username='audit_staff',
        password='TestPass123!',
        role=CustomUser.ROLE_STAFF,
        employee_id='EMP-STF-AUDIT',
    )


class AuditLogModelTest(TestCase):
    """AuditLog model creation and basic field checks."""

    def test_create_audit_log(self):
        admin = _make_admin()
        log = AuditLog.objects.create(
            user=admin,
            action=AuditLog.ACTION_LOGIN,
            target_type='user',
            target_id=str(admin.pk),
            ip_address='127.0.0.1',
            details={'note': 'Logged in successfully.'},
        )
        self.assertIsNotNone(log.pk)
        self.assertEqual(log.user, admin)

    def test_audit_log_has_timestamp(self):
        admin = _make_admin()
        log = AuditLog.objects.create(
            user=admin,
            action=AuditLog.ACTION_LOGIN,
        )
        self.assertIsNotNone(log.timestamp)

    def test_audit_log_anonymous(self):
        log = AuditLog.objects.create(
            user=None,
            action=AuditLog.ACTION_LOGIN_FAILED,
        )
        self.assertIsNone(log.user)
        self.assertIsNotNone(log.pk)

    def test_audit_log_classmethod(self):
        admin = _make_admin()
        log = AuditLog.log(
            action=AuditLog.ACTION_LOGOUT,
            user=admin,
            target_type='user',
            target_id=admin.pk,
        )
        self.assertEqual(log.action, AuditLog.ACTION_LOGOUT)


class AuditLogViewTest(TestCase):
    """Verify audit log list view access control."""

    def test_unauthenticated_redirects(self):
        client = Client()
        response = client.get('/logs/audit/')
        self.assertIn(response.status_code, [301, 302])

    def test_authenticated_staff_can_see_logs(self):
        client = Client()
        staff = _make_staff()
        client.force_login(staff)
        response = client.get('/logs/audit/')
        # Staff may or may not have access depending on role check,
        # but they should not get a 500 error.
        self.assertNotEqual(response.status_code, 500)


class AuditLogAdminAccessTest(TestCase):
    """Phase 4 regression: /logs/audit/ must return 200 for admins, redirect
    non-admins, and never 500 — even with complex nested details dicts."""

    def test_admin_gets_200(self):
        admin = _make_admin()
        client = Client()
        client.force_login(admin)
        resp = client.get('/logs/audit/')
        self.assertEqual(resp.status_code, 200)

    def test_staff_is_redirected(self):
        staff = _make_staff()
        client = Client()
        client.force_login(staff)
        resp = client.get('/logs/audit/')
        self.assertIn(resp.status_code, [301, 302])

    def test_complex_nested_details_do_not_crash_page(self):
        admin = _make_admin()
        AuditLog.objects.create(
            user=admin,
            action=AuditLog.ACTION_VERIFY,
            target_type='beneficiary',
            target_id='BEN-NESTED',
            details={
                'score': 0.91234,
                'all_template_scores': [
                    {'template': 'primary', 'score': 0.912},
                    {'template': 'additional', 'score': 0.888},
                ],
                'liveness_passed': True,
                'reason': None,
                'nested_dict': {'a': 1, 'b': [2, 3]},
            },
        )
        client = Client()
        client.force_login(admin)
        resp = client.get('/logs/audit/')
        self.assertEqual(resp.status_code, 200)

    def test_invalid_action_filter_ignored(self):
        admin = _make_admin()
        client = Client()
        client.force_login(admin)
        resp = client.get('/logs/audit/?action=not_a_valid_action_xyz')
        self.assertEqual(resp.status_code, 200)

    def test_valid_action_filter_applies(self):
        admin = _make_admin()
        AuditLog.objects.create(
            user=admin,
            action=AuditLog.ACTION_LOGIN,
        )
        AuditLog.objects.create(
            user=admin,
            action=AuditLog.ACTION_LOGOUT,
        )
        client = Client()
        client.force_login(admin)
        resp = client.get('/logs/audit/?action=login')
        self.assertEqual(resp.status_code, 200)
        for log in resp.context['logs']:
            self.assertEqual(log.action, AuditLog.ACTION_LOGIN)


class AuditLogDetailsRobustnessTest(TestCase):
    """
    Phase 4 regression: audit log page must load regardless of details field
    content.  Covers all types Django's JSONField can return: dict, list, str,
    int, float, bool, None, and nested structures.
    """

    def setUp(self):
        self.admin = _make_admin()
        self.client = Client()
        self.client.force_login(self.admin)

    def _get(self):
        return self.client.get('/logs/audit/')

    def test_details_none_handled_by_filter(self):
        """format_audit_details filter handles None without crashing."""
        from logs.templatetags.fans_filters import format_audit_details
        result = format_audit_details(None)
        self.assertEqual(result, '')

    def test_details_empty_dict_loads(self):
        AuditLog.objects.create(
            user=self.admin, action=AuditLog.ACTION_LOGIN, details={},
        )
        self.assertEqual(self._get().status_code, 200)

    def test_details_dict_loads(self):
        AuditLog.objects.create(
            user=self.admin, action=AuditLog.ACTION_VERIFY,
            details={'score': 0.88, 'liveness_passed': True, 'reason': 'ok'},
        )
        self.assertEqual(self._get().status_code, 200)

    def test_details_list_loads(self):
        AuditLog.objects.create(
            user=self.admin, action=AuditLog.ACTION_VERIFY,
            details=['item1', 'item2', 3],
        )
        self.assertEqual(self._get().status_code, 200)

    def test_details_string_loads(self):
        AuditLog.objects.create(
            user=self.admin, action=AuditLog.ACTION_FALLBACK,
            details='plain string detail',
        )
        self.assertEqual(self._get().status_code, 200)

    def test_details_integer_loads(self):
        AuditLog.objects.create(
            user=self.admin, action=AuditLog.ACTION_FALLBACK,
            details=42,
        )
        self.assertEqual(self._get().status_code, 200)

    def test_details_float_loads(self):
        AuditLog.objects.create(
            user=self.admin, action=AuditLog.ACTION_FALLBACK,
            details=3.14,
        )
        self.assertEqual(self._get().status_code, 200)

    def test_details_bool_loads(self):
        AuditLog.objects.create(
            user=self.admin, action=AuditLog.ACTION_FALLBACK,
            details=True,
        )
        self.assertEqual(self._get().status_code, 200)

    def test_details_nested_dict_list_loads(self):
        AuditLog.objects.create(
            user=self.admin, action=AuditLog.ACTION_VERIFY,
            details={
                'nested': {'a': 1, 'b': [2, 3]},
                'scores': [0.9, 0.8],
                'flag': True,
                'note': None,
            },
        )
        self.assertEqual(self._get().status_code, 200)

    def test_details_very_long_string_loads(self):
        AuditLog.objects.create(
            user=self.admin, action=AuditLog.ACTION_FALLBACK,
            details='x' * 5000,
        )
        self.assertEqual(self._get().status_code, 200)

    def test_missing_user_does_not_crash(self):
        AuditLog.objects.create(
            user=None, action=AuditLog.ACTION_LOGIN_FAILED,
            details={'reason': 'bad password'},
        )
        self.assertEqual(self._get().status_code, 200)

    def test_president_gets_200(self):
        from accounts.models import CustomUser
        head = CustomUser.objects.create_user(
            username='hb_audit', password='TestPass123!',
            role=CustomUser.ROLE_PRESIDENT, employee_id='EMP-HB-AUD',
        )
        c = Client()
        c.force_login(head)
        resp = c.get('/logs/audit/')
        self.assertEqual(resp.status_code, 200)

    def test_all_action_types_render(self):
        """Create one log per action type; the page must load 200."""
        for action, _ in AuditLog.ACTION_CHOICES:
            AuditLog.objects.create(
                user=self.admin, action=action,
                details={'action': action},
            )
        self.assertEqual(self._get().status_code, 200)

    def test_pagination_does_not_crash(self):
        for i in range(55):
            AuditLog.objects.create(
                user=self.admin, action=AuditLog.ACTION_LOGIN,
                details={'i': i},
            )
        resp = self.client.get('/logs/audit/?page=2')
        self.assertEqual(resp.status_code, 200)

    def test_invalid_page_does_not_crash(self):
        resp = self.client.get('/logs/audit/?page=not_a_number')
        self.assertEqual(resp.status_code, 200)

    def test_large_page_number_does_not_crash(self):
        resp = self.client.get('/logs/audit/?page=99999')
        self.assertEqual(resp.status_code, 200)


class AuditLogHttpsTest(TestCase):
    """Audit log page must work under both HTTP and HTTPS proxy contexts."""

    def setUp(self):
        from accounts.models import CustomUser
        self.admin = CustomUser.objects.create_user(
            username='al_https_adm', password='TestPass123!',
            role=CustomUser.ROLE_IT, employee_id='EMP-ALHTTPS',
        )
        AuditLog.objects.create(
            user=self.admin, action=AuditLog.ACTION_LOGIN,
            details={'note': 'test'},
        )

    def test_audit_log_loads_via_https_proxy(self):
        from django.test import override_settings
        with override_settings(
            SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
        ):
            c = Client()
            c.force_login(self.admin)
            resp = c.get('/logs/audit/', HTTP_X_FORWARDED_PROTO='https')
            self.assertEqual(resp.status_code, 200)

    def test_audit_log_loads_via_http_fallback(self):
        c = Client()
        c.force_login(self.admin)
        resp = c.get('/logs/audit/')
        self.assertEqual(resp.status_code, 200)


class NotificationTest(TestCase):
    """Notification center: model, notify_admins()/notify_user() dedupe,
    bell context processor, click-through marks read + redirects, mark-all-read."""

    def setUp(self):
        from logs.models import Notification
        self.Notification = Notification
        self.admin1 = CustomUser.objects.create_user(
            username='notif_admin1', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-NOTIF-ADMIN1',
        )
        self.admin2 = CustomUser.objects.create_user(
            username='notif_admin2', password='TestPass123!',
            role=CustomUser.ROLE_IT, employee_id='EMP-NOTIF-ADMIN2',
        )
        self.staff = CustomUser.objects.create_user(
            username='notif_staff', password='TestPass123!',
            role=CustomUser.ROLE_STAFF, employee_id='EMP-NOTIF-STAFF',
        )
        self.client = Client()

    def test_notify_admins_creates_one_per_active_admin_not_staff(self):
        from logs.notifications import notify_admins
        created = notify_admins(
            category=self.Notification.CATEGORY_SYSTEM_ALERT,
            title='Test alert', message='hello', url='/x/', dedupe_key='k1',
        )
        self.assertEqual(len(created), 2)  # admin1 + admin2, not staff
        recipients = {n.recipient for n in created}
        self.assertEqual(recipients, {self.admin1, self.admin2})
        self.assertFalse(self.Notification.objects.filter(recipient=self.staff).exists())

    def test_notify_admins_is_idempotent_per_dedupe_key(self):
        from logs.notifications import notify_admins
        notify_admins(category=self.Notification.CATEGORY_SYSTEM_ALERT, title='A', dedupe_key='dup-1')
        second = notify_admins(category=self.Notification.CATEGORY_SYSTEM_ALERT, title='A again', dedupe_key='dup-1')
        self.assertEqual(second, [])
        self.assertEqual(self.Notification.objects.filter(dedupe_key='dup-1').count(), 2)  # one per admin, still

    def test_notify_admins_excludes_inactive_and_suspended(self):
        from logs.notifications import notify_admins
        self.admin2.account_status = CustomUser.STATUS_SUSPENDED
        self.admin2.save()
        created = notify_admins(category=self.Notification.CATEGORY_SYSTEM_ALERT, title='B', dedupe_key='k2')
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].recipient, self.admin1)

    def test_notify_user_targets_one_recipient(self):
        from logs.notifications import notify_user
        n = notify_user(self.staff, category=self.Notification.CATEGORY_SYSTEM_ALERT, title='Just you', dedupe_key='k3')
        self.assertEqual(n.recipient, self.staff)
        self.assertEqual(self.Notification.objects.count(), 1)

    def test_context_processor_unread_count_and_recent_list(self):
        self.Notification.objects.create(recipient=self.admin1, category=self.Notification.CATEGORY_SYSTEM_ALERT, title='One')
        self.Notification.objects.create(recipient=self.admin1, category=self.Notification.CATEGORY_SYSTEM_ALERT, title='Two')
        self.client.force_login(self.admin1)
        resp = self.client.get('/logs/notifications/')
        self.assertEqual(resp.context['notifications'].paginator.count, 2)

        from logs.context_processors import notifications as _ctx
        from django.test import RequestFactory
        rf = RequestFactory()
        request = rf.get('/')
        request.user = self.admin1
        ctx = _ctx(request)
        self.assertEqual(ctx['unread_notification_count'], 2)
        self.assertEqual(len(ctx['recent_notifications']), 2)

    def test_context_processor_unauthenticated_is_safe(self):
        from logs.context_processors import notifications as _ctx
        from django.contrib.auth.models import AnonymousUser
        from django.test import RequestFactory
        rf = RequestFactory()
        request = rf.get('/')
        request.user = AnonymousUser()
        ctx = _ctx(request)
        self.assertEqual(ctx['unread_notification_count'], 0)
        self.assertEqual(ctx['recent_notifications'], [])

    def test_open_notification_marks_read_and_redirects_to_target_url(self):
        n = self.Notification.objects.create(
            recipient=self.admin1, category=self.Notification.CATEGORY_APPROVAL_REQUIRED,
            title='Needs review', url='/logs/audit/',
        )
        self.client.force_login(self.admin1)
        resp = self.client.get(f'/logs/notifications/{n.id}/open/')
        self.assertRedirects(resp, '/logs/audit/')
        n.refresh_from_db()
        self.assertTrue(n.is_read)
        self.assertIsNotNone(n.read_at)

    def test_cannot_open_another_users_notification(self):
        n = self.Notification.objects.create(recipient=self.admin1, category=self.Notification.CATEGORY_SYSTEM_ALERT, title='Private')
        self.client.force_login(self.staff)
        resp = self.client.get(f'/logs/notifications/{n.id}/open/')
        self.assertEqual(resp.status_code, 404)

    def test_mark_all_read(self):
        self.Notification.objects.create(recipient=self.admin1, category=self.Notification.CATEGORY_SYSTEM_ALERT, title='A')
        self.Notification.objects.create(recipient=self.admin1, category=self.Notification.CATEGORY_SYSTEM_ALERT, title='B')
        self.client.force_login(self.admin1)
        self.client.post('/logs/notifications/mark-all-read/', {'next': '/logs/notifications/'})
        self.assertEqual(
            self.Notification.objects.filter(recipient=self.admin1, is_read=False).count(), 0,
        )

    def test_bell_badge_reflects_unread_count_on_dashboard(self):
        self.Notification.objects.create(recipient=self.admin1, category=self.Notification.CATEGORY_SYSTEM_ALERT, title='A')
        self.client.force_login(self.admin1)
        resp = self.client.get('/dashboard/')
        self.assertContains(resp, 'bi-bell-fill')
        self.assertEqual(resp.context['unread_notification_count'], 1)

    def test_all_seven_categories_are_distinct_and_choosable(self):
        """v2.2.0: notification system must distinguish all 7 requested types."""
        expected = {
            'approval_required', 'approval_reminder', 'verification_review',
            'fraud_alert', 'security_alert', 'password_reset_request', 'system_alert',
        }
        actual = {value for value, _ in self.Notification.CATEGORY_CHOICES}
        self.assertEqual(actual, expected)

    def test_priority_field_defaults_by_category(self):
        from logs.notifications import notify_admins
        fraud = notify_admins(category=self.Notification.CATEGORY_FRAUD_ALERT, title='x', dedupe_key='p1')[0]
        approval = notify_admins(category=self.Notification.CATEGORY_APPROVAL_REQUIRED, title='y', dedupe_key='p2')[0]
        self.assertEqual(fraud.priority, self.Notification.PRIORITY_HIGH)
        self.assertEqual(approval.priority, self.Notification.PRIORITY_MEDIUM)

    def test_priority_can_be_overridden_explicitly(self):
        from logs.notifications import notify_admins
        n = notify_admins(
            category=self.Notification.CATEGORY_APPROVAL_REQUIRED, title='z',
            dedupe_key='p3', priority=self.Notification.PRIORITY_HIGH,
        )[0]
        self.assertEqual(n.priority, self.Notification.PRIORITY_HIGH)

    def test_high_priority_notification_gets_visual_emphasis_in_center(self):
        self.Notification.objects.create(
            recipient=self.admin1, category=self.Notification.CATEGORY_FRAUD_ALERT,
            priority=self.Notification.PRIORITY_HIGH, title='High priority item',
        )
        self.client.force_login(self.admin1)
        resp = self.client.get('/logs/notifications/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'HIGH')


class ApprovalReminderTest(TestCase):
    """sync_approval_reminders() — fires once per still-pending item after
    the reminder window, never before, never twice for the same item."""

    def setUp(self):
        from logs.models import Notification
        self.Notification = Notification
        self.admin = CustomUser.objects.create_user(
            username='reminder_admin', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-REMINDER-ADMIN',
        )

    def test_recent_pending_request_does_not_remind(self):
        from accounts.models import PasswordResetRequest
        from logs.notifications import sync_approval_reminders
        PasswordResetRequest.objects.create(username_entered='someone', contact_note='x')
        sync_approval_reminders()
        self.assertFalse(
            self.Notification.objects.filter(category=self.Notification.CATEGORY_APPROVAL_REMINDER).exists()
        )

    def test_stale_pending_request_reminds_once(self):
        import datetime
        from django.utils import timezone
        from accounts.models import PasswordResetRequest
        from logs.notifications import sync_approval_reminders

        prr = PasswordResetRequest.objects.create(username_entered='someone', contact_note='x')
        PasswordResetRequest.objects.filter(pk=prr.pk).update(
            created_at=timezone.now() - datetime.timedelta(hours=72),
        )
        sync_approval_reminders()
        reminders = self.Notification.objects.filter(category=self.Notification.CATEGORY_APPROVAL_REMINDER)
        self.assertEqual(reminders.count(), 1)
        self.assertEqual(reminders.first().priority, self.Notification.PRIORITY_HIGH)

        # Re-running must not create a second reminder for the same item.
        sync_approval_reminders()
        self.assertEqual(
            self.Notification.objects.filter(category=self.Notification.CATEGORY_APPROVAL_REMINDER).count(), 1,
        )

    def test_stale_pending_stipend_event_reminds_president(self):
        """A payout schedule the President forgot to approve/reject must
        eventually surface a reminder, same as every other pending-request
        type (Phase 6: president-forgets-approval case)."""
        import datetime
        from django.utils import timezone
        from verification.models import StipendEvent
        from logs.notifications import sync_approval_reminders

        president = CustomUser.objects.create_user(
            username='reminder_president', password='TestPass123!',
            role=CustomUser.ROLE_PRESIDENT, employee_id='EMP-REMINDER-PRES',
        )
        event = StipendEvent.objects.create(
            title='Q3 Stipend', date=timezone.localdate() + datetime.timedelta(days=10),
            amount=1000, approval_status=StipendEvent.APPROVAL_PENDING,
            created_by=self.admin,
        )
        StipendEvent.objects.filter(pk=event.pk).update(
            created_at=timezone.now() - datetime.timedelta(hours=72),
        )
        sync_approval_reminders()
        reminders = self.Notification.objects.filter(
            category=self.Notification.CATEGORY_APPROVAL_REMINDER,
            recipient=president,
        )
        self.assertEqual(reminders.count(), 1)
        self.assertIn('Q3 Stipend', reminders.first().title)

        # Re-running must not double-notify.
        sync_approval_reminders()
        self.assertEqual(
            self.Notification.objects.filter(
                category=self.Notification.CATEGORY_APPROVAL_REMINDER, recipient=president,
            ).count(), 1,
        )

    def test_recent_pending_stipend_event_does_not_remind(self):
        import datetime
        from django.utils import timezone
        from verification.models import StipendEvent
        from logs.notifications import sync_approval_reminders

        CustomUser.objects.create_user(
            username='reminder_president2', password='TestPass123!',
            role=CustomUser.ROLE_PRESIDENT, employee_id='EMP-REMINDER-PRES2',
        )
        StipendEvent.objects.create(
            title='Freshly Submitted', date=timezone.localdate() + datetime.timedelta(days=10),
            amount=1000, approval_status=StipendEvent.APPROVAL_PENDING,
            created_by=self.admin,
        )
        sync_approval_reminders()
        self.assertFalse(
            self.Notification.objects.filter(
                category=self.Notification.CATEGORY_APPROVAL_REMINDER,
                title__icontains='Freshly Submitted',
            ).exists()
        )
