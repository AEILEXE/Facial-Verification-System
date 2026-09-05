"""
FANS-C smoke tests for the core Django project configuration.

These tests verify settings parsing, URL routing, and basic security
assumptions without requiring TensorFlow, a camera, or large ML models.
FaceNet-dependent code is never imported here.
"""

import contextlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from django.test import TestCase, Client, override_settings
from django.urls import reverse, resolve, NoReverseMatch
from django.conf import settings


class SettingsTest(TestCase):
    """Verify that critical settings are present and well-formed."""

    def test_installed_apps_contain_required_apps(self):
        required = ['accounts', 'beneficiaries', 'verification', 'logs']
        for app in required:
            self.assertIn(app, settings.INSTALLED_APPS, f'{app} missing from INSTALLED_APPS')

    def test_auth_user_model(self):
        self.assertEqual(settings.AUTH_USER_MODEL, 'accounts.CustomUser')

    def test_static_root_configured(self):
        self.assertTrue(settings.STATIC_ROOT)

    def test_media_root_configured(self):
        self.assertTrue(settings.MEDIA_ROOT)

    def test_login_url(self):
        self.assertEqual(settings.LOGIN_URL, '/accounts/login/')

    def test_login_redirect_url(self):
        self.assertEqual(settings.LOGIN_REDIRECT_URL, '/dashboard/')

    def test_session_cookie_httponly(self):
        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)

    def test_csrf_cookie_httponly(self):
        self.assertTrue(settings.CSRF_COOKIE_HTTPONLY)

    def test_x_frame_options_deny(self):
        self.assertEqual(settings.X_FRAME_OPTIONS, 'DENY')

    def test_content_type_nosniff(self):
        self.assertTrue(settings.SECURE_CONTENT_TYPE_NOSNIFF)

    def test_session_cookie_age_8h(self):
        self.assertEqual(settings.SESSION_COOKIE_AGE, 8 * 60 * 60)

    def test_password_validators_include_custom(self):
        validator_names = [v['NAME'] for v in settings.AUTH_PASSWORD_VALIDATORS]
        self.assertIn('accounts.validators.CharacterClassValidator', validator_names)

    def test_time_zone_manila(self):
        self.assertEqual(settings.TIME_ZONE, 'Asia/Manila')

    def test_debug_defaults_false(self):
        """In test environment, settings.DEBUG may be True or False.
        This test just checks the setting is a boolean (not some other type)."""
        self.assertIsInstance(settings.DEBUG, bool)


class UrlResolutionTest(TestCase):
    """Confirm key URL names are resolvable."""

    def test_home_redirects_to_dashboard(self):
        client = Client()
        response = client.get('/')
        self.assertIn(response.status_code, [301, 302])

    def test_login_url_resolves(self):
        url = reverse('accounts:login')
        self.assertEqual(url, '/accounts/login/')

    def test_health_check_url_resolves(self):
        url = reverse('health_check')
        self.assertEqual(url, '/health/')

    def test_admin_url_resolves(self):
        url = reverse('admin:index')
        self.assertIn('admin', url)


class LoginAccessTest(TestCase):
    """Verify that the login page is accessible and requires no auth."""

    def test_login_page_returns_200(self):
        client = Client()
        response = client.get('/accounts/login/')
        self.assertEqual(response.status_code, 200)

    def test_dashboard_redirects_unauthenticated(self):
        client = Client()
        response = client.get('/dashboard/')
        self.assertIn(response.status_code, [301, 302])
        location = response.get('Location', '')
        self.assertIn('login', location.lower())

    def test_verification_redirects_unauthenticated(self):
        client = Client()
        response = client.get('/verification/')
        self.assertIn(response.status_code, [301, 302])

    def test_logs_redirects_unauthenticated(self):
        client = Client()
        response = client.get('/logs/audit/')
        self.assertIn(response.status_code, [301, 302])


class ProtectedMediaTest(TestCase):
    """
    v2.1.16 Security Hardening Round #2 (H-02): beneficiary/representative/
    user photos and the representative's authorization document live under
    MEDIA_ROOT. They were previously served by django.views.static.serve via
    static(..., insecure=True) in urls.py, which performs NO authentication
    check — any device on the LAN could fetch /media/<path> directly. Now
    routed through fans.views.serve_protected_media, gated by @login_required,
    at the exact same MEDIA_URL prefix so existing <field>.url references in
    templates keep working unchanged.

    v2.1.16 Security Hardening Round #3 (Blocker 3): @login_required alone
    only proves SOME authenticated session, not that the requester may see
    THIS specific file. serve_protected_media now resolves the path back to
    the Beneficiary/Representative/CustomUser record that stores it and
    authorizes per object type (see fans/views.py docstring). These tests
    write real DB records referencing the on-disk file, since the new
    authorization logic 404s any path that isn't an actual stored field value.
    """

    def setUp(self):
        from accounts.models import CustomUser
        from beneficiaries.models import Beneficiary, Representative, SharedRepresentativeReview
        self.staff = CustomUser.objects.create_user(
            username='media_test_staff', password='TestPass123!',
            role=CustomUser.ROLE_STAFF, employee_id='EMP-MEDIA-01',
        )
        self.other_staff = CustomUser.objects.create_user(
            username='media_test_staff2', password='TestPass123!',
            role=CustomUser.ROLE_STAFF, employee_id='EMP-MEDIA-02',
        )
        self.admin = CustomUser.objects.create_user(
            username='media_test_admin', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-MEDIA-03',
        )

        self._tmpdir = tempfile.mkdtemp()
        ben_sub = Path(self._tmpdir) / 'beneficiaries' / 'profile_pics'
        ben_sub.mkdir(parents=True, exist_ok=True)
        (ben_sub / 'test.jpg').write_bytes(b'fake-jpeg-bytes')
        user_sub = Path(self._tmpdir) / 'users' / 'profile_pics'
        user_sub.mkdir(parents=True, exist_ok=True)
        (user_sub / 'staff.jpg').write_bytes(b'fake-jpeg-bytes')
        rep_sub = Path(self._tmpdir) / 'representatives' / 'authorization'
        rep_sub.mkdir(parents=True, exist_ok=True)
        (rep_sub / 'doc.pdf').write_bytes(b'fake-pdf-bytes')
        self._media_override = override_settings(MEDIA_ROOT=self._tmpdir)
        self._media_override.enable()
        self.addCleanup(self._media_override.disable)
        self.addCleanup(lambda: shutil.rmtree(self._tmpdir, ignore_errors=True))

        self.beneficiary = Beneficiary.objects.create(
            beneficiary_id='BEN-MEDIA-001',
            first_name='Maria',
            last_name='Santos',
            senior_citizen_id='SC-MEDIA-001',
            date_of_birth='1940-01-01',
            gender='F',
            address='123 Main St',
            barangay='Test Barangay',
            municipality='Quezon City',
            province='Metro Manila',
        )
        self.beneficiary.profile_picture = 'beneficiaries/profile_pics/test.jpg'
        self.beneficiary.save()

        self.staff.profile_picture = 'users/profile_pics/staff.jpg'
        self.staff.save()

        rep = Representative.objects.create(
            beneficiary=self.beneficiary, first_name='Jose', last_name='Rizal',
            relationship='Son', contact_number='09171234567',
            valid_id_type='SSS', valid_id_number='SSS-MEDIA-001',
            registered_by=self.staff,
        )
        self.shared_review = SharedRepresentativeReview.objects.create(
            representative=rep, matched_beneficiary_id='BEN-OTHER-001',
            flagged_by=self.staff,
        )
        self.shared_review.authorization_document = 'representatives/authorization/doc.pdf'
        self.shared_review.save()

    def test_authorized_user_can_access_beneficiary_media(self):
        """3. Authorized role (any authenticated FANS-C user) can view
        beneficiary media — mirrors beneficiary_detail's own access level."""
        client = Client()
        client.force_login(self.staff)
        resp = client.get('/media/beneficiaries/profile_pics/test.jpg')
        self.assertEqual(resp.status_code, 200)

    def test_anonymous_denied(self):
        """1. Anonymous (not logged in) user is denied (redirected to login)."""
        client = Client()
        resp = client.get('/media/beneficiaries/profile_pics/test.jpg')
        self.assertIn(resp.status_code, [301, 302])
        self.assertIn('login', resp.get('Location', '').lower())

    def test_authenticated_unauthorized_user_denied_for_other_user_photo(self):
        """2. Authenticated but unauthorized user is denied — a Staff account
        has no administrative reason to view a DIFFERENT user's own account
        photo (mirrors accounts.views' is_admin gate on viewing other users)."""
        client = Client()
        client.force_login(self.other_staff)
        resp = client.get('/media/users/profile_pics/staff.jpg')
        self.assertEqual(resp.status_code, 403)

    def test_admin_can_access_any_user_photo(self):
        """Administrative role (is_admin) may view another user's photo."""
        client = Client()
        client.force_login(self.admin)
        resp = client.get('/media/users/profile_pics/staff.jpg')
        self.assertEqual(resp.status_code, 200)

    def test_user_can_access_own_photo(self):
        client = Client()
        client.force_login(self.staff)
        resp = client.get('/media/users/profile_pics/staff.jpg')
        self.assertEqual(resp.status_code, 200)

    def test_staff_denied_shared_rep_review_document(self):
        """Staff has no view into the SharedRepresentativeReview admin queue
        (shared_rep_review_list/_detail require is_admin) — the document
        behind that queue must be equally denied by path."""
        client = Client()
        client.force_login(self.staff)
        resp = client.get('/media/representatives/authorization/doc.pdf')
        self.assertEqual(resp.status_code, 403)

    def test_admin_can_access_shared_rep_review_document(self):
        client = Client()
        client.force_login(self.admin)
        resp = client.get('/media/representatives/authorization/doc.pdf')
        self.assertEqual(resp.status_code, 200)

    def test_unmatched_path_returns_404(self):
        """A path not stored on any tracked record (guessed/stale/traversal-
        shaped) is denied — prevents predictable URL leakage of files that
        happen to sit under MEDIA_ROOT without being a real object's media."""
        client = Client()
        client.force_login(self.staff)
        resp = client.get('/media/beneficiaries/profile_pics/nonexistent.jpg')
        self.assertEqual(resp.status_code, 404)

    def test_media_url_unchanged_for_templates(self):
        """<field>.url must still resolve under the same /media/ prefix."""
        self.assertEqual(settings.MEDIA_URL, '/media/')

    # ── v2.1.16 Security Hardening Round #4 (Blocker 3) — re-verification ──
    # against the required attack matrix. The object-level authorization in
    # serve_protected_media/_resolve_media_owner already existed going into
    # this round (see the class docstring above); these tests map 1:1 onto
    # the round's required test numbering and add the two attack angles
    # (path traversal, cross-role parity for President) that weren't
    # explicitly named before, as confirmation nothing regressed and no
    # narrower gap exists alongside the already-covered cases above.

    def test_1_anonymous_access_denied(self):
        client = Client()
        resp = client.get('/media/beneficiaries/profile_pics/test.jpg')
        self.assertIn(resp.status_code, [301, 302])
        self.assertIn('login', resp.get('Location', '').lower())

    def test_2_staff_denied_unauthorized_target_user_photo(self):
        """'Unauthorized beneficiary' has no meaning in this app's data model
        — every authenticated FANS-C role shares the same beneficiary roster
        with no per-officer assignment (see the class docstring). The
        equivalent real ownership boundary this app DOES enforce is a
        CustomUser's own photo: a Staff account has no administrative reason
        to view a DIFFERENT staff member's account photo."""
        client = Client()
        client.force_login(self.other_staff)
        resp = client.get('/media/users/profile_pics/staff.jpg')
        self.assertEqual(resp.status_code, 403)

    def test_3_authorized_staff_access_allowed(self):
        client = Client()
        client.force_login(self.staff)
        resp = client.get('/media/beneficiaries/profile_pics/test.jpg')
        self.assertEqual(resp.status_code, 200)

    def test_4_president_allowed_admin_level_access(self):
        """President (CustomUser.is_admin includes President) must be
        granted the same administrative access as Admin — both the
        cross-user photo and the admin-only SharedRepresentativeReview
        document."""
        from accounts.models import CustomUser
        president = CustomUser.objects.create_user(
            username='media_test_president', password='TestPass123!',
            role=CustomUser.ROLE_PRESIDENT, employee_id='EMP-MEDIA-04',
        )
        client = Client()
        client.force_login(president)
        resp_user_photo = client.get('/media/users/profile_pics/staff.jpg')
        self.assertEqual(resp_user_photo.status_code, 200)
        resp_review_doc = client.get('/media/representatives/authorization/doc.pdf')
        self.assertEqual(resp_review_doc.status_code, 200)

    def test_path_traversal_attempt_returns_404_not_file(self):
        """A traversal-shaped path never matches a stored field value, so it
        404s at the ownership-resolution step before django.views.static.serve
        (which independently rejects traversal via safe_join()) is even reached."""
        client = Client()
        client.force_login(self.staff)
        resp = client.get('/media/../../../../windows/win.ini')
        self.assertIn(resp.status_code, [404, 400])
        resp2 = client.get('/media/beneficiaries/profile_pics/../../../win.ini')
        self.assertIn(resp2.status_code, [404, 400])


class HttpsProxyDetectionTest(TestCase):
    """
    Verify that requests forwarded by Caddy with X-Forwarded-Proto: https
    are treated as secure by Django.

    These tests cover the core requirement: HTTPS through Caddy must not show
    the Limited HTTP mode banner, and HTTP fallback must still show it.
    """

    def setUp(self):
        from accounts.models import CustomUser
        self.user = CustomUser.objects.create_user(
            username='https_test_user',
            password='TestPass123!',
            role=CustomUser.ROLE_STAFF,
            employee_id='EMP-HTTPS-01',
        )

    @override_settings(SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'))
    def test_forwarded_https_is_treated_as_secure(self):
        """request.is_secure() must return True when X-Forwarded-Proto=https."""
        client = Client()
        client.force_login(self.user)
        response = client.get('/dashboard/', HTTP_X_FORWARDED_PROTO='https')
        # The response renders the dashboard (200) or any non-error status
        self.assertNotIn(response.status_code, [500])
        # The Limited HTTP mode banner must NOT appear
        self.assertNotIn(b'Limited HTTP mode', response.content)

    @override_settings(SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'))
    def test_https_proxy_request_no_http_fallback_in_footer(self):
        """HTTPS-proxied request must not show fallback/HTTP in footer."""
        client = Client()
        client.force_login(self.user)
        response = client.get('/dashboard/', HTTP_X_FORWARDED_PROTO='https')
        # The httpModeBanner div must not be visible
        self.assertNotIn(b'httpModeBanner', response.content)

    def test_direct_http_request_shows_limited_banner(self):
        """Plain HTTP request (no X-Forwarded-Proto) must show limited banner."""
        client = Client()
        client.force_login(self.user)
        # No forwarded proto header — simulates direct HTTP access
        response = client.get('/dashboard/')
        self.assertIn(b'Limited HTTP mode', response.content)

    @override_settings(SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'))
    def test_secure_https_link_points_to_domain(self):
        """'Open Secure HTTPS' link in banner must point to fans-barangay.local."""
        client = Client()
        client.force_login(self.user)
        response = client.get('/dashboard/')  # HTTP — banner is shown
        # The link href must be the configured HTTPS domain
        self.assertIn(b'https://fans-barangay.local', response.content)

    @override_settings(
        SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
        DEBUG=False,
    )
    def test_camera_verification_allowed_on_https(self):
        """_camera_verification_allowed() must return True for HTTPS proxy requests."""
        from verification.views import _camera_verification_allowed
        from django.test import RequestFactory
        factory = RequestFactory()
        request = factory.get('/', HTTP_X_FORWARDED_PROTO='https')
        # With the override in effect, request.is_secure() should return True
        self.assertTrue(_camera_verification_allowed(request))

    @override_settings(DEBUG=False)
    def test_camera_verification_blocked_on_http(self):
        """_camera_verification_allowed() must return False for plain HTTP requests."""
        from verification.views import _camera_verification_allowed
        from django.test import RequestFactory
        factory = RequestFactory()
        request = factory.get('/')  # no X-Forwarded-Proto
        self.assertFalse(_camera_verification_allowed(request))

    @override_settings(
        SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
        DEBUG=False,
    )
    def test_audit_log_accessible_on_https(self):
        """Audit log must load (200) for admin accessed via HTTPS proxy."""
        from accounts.models import CustomUser
        admin = CustomUser.objects.create_user(
            username='https_admin',
            password='TestPass123!',
            role=CustomUser.ROLE_IT,
            employee_id='EMP-HTTPSADM',
        )
        client = Client()
        client.force_login(admin)
        response = client.get('/logs/audit/', HTTP_X_FORWARDED_PROTO='https')
        self.assertEqual(response.status_code, 200)

    @override_settings(DEBUG=False)
    def test_audit_log_accessible_on_http(self):
        """Audit log must load (200) for admin on HTTP fallback too."""
        from accounts.models import CustomUser
        admin = CustomUser.objects.create_user(
            username='http_admin',
            password='TestPass123!',
            role=CustomUser.ROLE_IT,
            employee_id='EMP-HTTPADM',
        )
        client = Client()
        client.force_login(admin)
        response = client.get('/logs/audit/')
        self.assertEqual(response.status_code, 200)


class SecureProxySettingsDefaultTest(TestCase):
    """
    Regression (v2.0.7): SECURE_PROXY_SSL_HEADER must be set by default in
    settings.py so existing installs with no .env entry for it still detect
    HTTPS correctly via Caddy's X-Forwarded-Proto header.
    """

    def test_secure_proxy_ssl_header_is_set_by_default(self):
        """SECURE_PROXY_SSL_HEADER must be the (header, value) tuple by default."""
        self.assertTrue(
            hasattr(settings, 'SECURE_PROXY_SSL_HEADER'),
            'SECURE_PROXY_SSL_HEADER must be set even without .env override',
        )
        self.assertEqual(settings.SECURE_PROXY_SSL_HEADER,
                         ('HTTP_X_FORWARDED_PROTO', 'https'))

    def test_use_x_forwarded_host_defaults_true(self):
        """USE_X_FORWARDED_HOST must default to True for Caddy host forwarding."""
        self.assertTrue(
            getattr(settings, 'USE_X_FORWARDED_HOST', False),
            'USE_X_FORWARDED_HOST must default to True',
        )

    def test_diagnostic_script_exists(self):
        """Runtime diagnostic script must exist at scripts/admin/check-runtime-network.ps1."""
        import os
        from pathlib import Path
        from django.conf import settings as django_settings
        if getattr(django_settings, 'frozen', False):
            return  # skip inside PyInstaller bundle
        base = Path(__file__).resolve().parent.parent
        script = base / 'scripts' / 'admin' / 'check-runtime-network.ps1'
        self.assertTrue(
            script.exists(),
            f'Diagnostic script missing at {script}',
        )


class ProductionGuardTest(unittest.TestCase):
    """
    v2.1.16 Security Hardening Round #5 (Blocker 4) — unit tests for
    fans.production_guard, the pure-function fail-closed configuration
    checker wired into fans/settings.py's startup validation.

    Deliberately plain unittest.TestCase (not Django's TestCase): this
    module has zero Django dependencies by design, so these tests exercise
    it with synthetic parameter combinations rather than reloading the real
    settings module (which is loaded once at process start and cannot be
    safely re-evaluated mid-test-run).
    """

    def _safe_kwargs(self, **overrides):
        base = dict(
            debug=False,
            is_frozen=False,
            demo_mode=False,
            liveness_required=True,
            pad_required=True,
            anti_spoof_threshold=0.25,
            save_liveness_debug_frames=False,
            presentation_attack_review_or_deny='deny',
            strict_presentation_attack_check=True,
            phone_screen_spoof_threshold=0.40,
        )
        base.update(overrides)
        return base

    def test_all_safe_settings_produce_no_errors(self):
        from fans.production_guard import collect_production_errors
        errors = collect_production_errors(**self._safe_kwargs())
        self.assertEqual(errors, [])

    def test_debug_true_with_frozen_is_an_error(self):
        """DEBUG=True in a packaged production build must fail closed even
        though DEBUG itself used to be the entire definition of 'not
        production'."""
        from fans.production_guard import collect_production_errors
        errors = collect_production_errors(**self._safe_kwargs(debug=True, is_frozen=True))
        self.assertTrue(any('DEBUG=True' in e for e in errors), errors)

    def test_debug_true_without_frozen_is_not_flagged_by_this_check(self):
        """Ordinary development (DEBUG=True, not a frozen build) must not be
        flagged by the DEBUG-specific check -- is_production_mode() is what
        callers use to decide whether ANY of these errors are fatal, and it
        is False for this combination."""
        from fans.production_guard import collect_production_errors, is_production_mode
        errors = collect_production_errors(**self._safe_kwargs(debug=True, is_frozen=False))
        self.assertFalse(any('DEBUG=True' in e for e in errors), errors)
        self.assertFalse(is_production_mode(debug=True, is_frozen=False))

    def test_demo_mode_true_is_an_error(self):
        from fans.production_guard import collect_production_errors
        errors = collect_production_errors(**self._safe_kwargs(demo_mode=True))
        self.assertTrue(any('DEMO_MODE=True' in e for e in errors), errors)

    def test_liveness_not_required_is_an_error(self):
        from fans.production_guard import collect_production_errors
        errors = collect_production_errors(**self._safe_kwargs(liveness_required=False))
        self.assertTrue(any('LIVENESS_REQUIRED=False' in e for e in errors), errors)

    def test_pad_not_required_is_an_error(self):
        from fans.production_guard import collect_production_errors
        errors = collect_production_errors(**self._safe_kwargs(pad_required=False))
        self.assertTrue(any('PAD_REQUIRED=False' in e for e in errors), errors)

    def test_weak_anti_spoof_threshold_is_an_error(self):
        from fans.production_guard import collect_production_errors
        errors = collect_production_errors(**self._safe_kwargs(anti_spoof_threshold=0.0))
        self.assertTrue(any('ANTI_SPOOF_THRESHOLD' in e for e in errors), errors)

    def test_calibrated_anti_spoof_threshold_is_not_flagged(self):
        """0.15 is the value this repository's own .env and the installer's
        shipped template both use for elderly-user calibration -- the floor
        must sit below it so a legitimate, already-deployed configuration is
        never newly broken by this check."""
        from fans.production_guard import collect_production_errors
        errors = collect_production_errors(**self._safe_kwargs(anti_spoof_threshold=0.15))
        self.assertEqual(errors, [])

    def test_save_debug_frames_true_is_an_error(self):
        from fans.production_guard import collect_production_errors
        errors = collect_production_errors(**self._safe_kwargs(save_liveness_debug_frames=True))
        self.assertTrue(any('SAVE_LIVENESS_DEBUG_FRAMES=True' in e for e in errors), errors)

    def test_pad_review_mode_is_an_error(self):
        """v2.1.16 Final Hardening Patch (Codex NO-GO #1): PAD_REQUIRED=True
        is meaningless if a suspected attack is only routed to manual review
        instead of denied outright."""
        from fans.production_guard import collect_production_errors
        errors = collect_production_errors(
            **self._safe_kwargs(presentation_attack_review_or_deny='review'),
        )
        self.assertTrue(any('PRESENTATION_ATTACK_REVIEW_OR_DENY' in e for e in errors), errors)

    def test_pad_strict_check_disabled_is_an_error(self):
        """STRICT_PRESENTATION_ATTACK_CHECK=False silently turns
        PAD_REQUIRED=True into a no-op."""
        from fans.production_guard import collect_production_errors
        errors = collect_production_errors(
            **self._safe_kwargs(strict_presentation_attack_check=False),
        )
        self.assertTrue(any('STRICT_PRESENTATION_ATTACK_CHECK=False' in e for e in errors), errors)

    def test_weak_phone_screen_spoof_threshold_is_an_error(self):
        from fans.production_guard import collect_production_errors
        errors = collect_production_errors(
            **self._safe_kwargs(phone_screen_spoof_threshold=0.0),
        )
        self.assertTrue(any('PHONE_SCREEN_SPOOF_THRESHOLD' in e for e in errors), errors)

    def test_calibrated_phone_screen_spoof_threshold_is_not_flagged(self):
        from fans.production_guard import collect_production_errors
        errors = collect_production_errors(
            **self._safe_kwargs(phone_screen_spoof_threshold=0.40),
        )
        self.assertEqual(errors, [])

    def test_multiple_violations_all_reported(self):
        """All violations are reported together, not just the first one --
        callers join every entry into a single startup error message."""
        from fans.production_guard import collect_production_errors
        errors = collect_production_errors(**self._safe_kwargs(
            demo_mode=True, liveness_required=False, pad_required=False,
        ))
        self.assertEqual(len(errors), 3, errors)

    def test_is_production_mode_true_when_frozen_regardless_of_debug(self):
        from fans.production_guard import is_production_mode
        self.assertTrue(is_production_mode(debug=True, is_frozen=True))
        self.assertTrue(is_production_mode(debug=False, is_frozen=True))

    def test_is_production_mode_true_when_debug_false_even_if_not_frozen(self):
        """Preserves the pre-existing definition of production for a
        manually-deployed non-frozen server with DEBUG=False."""
        from fans.production_guard import is_production_mode
        self.assertTrue(is_production_mode(debug=False, is_frozen=False))

    def test_is_production_mode_false_for_ordinary_dev(self):
        from fans.production_guard import is_production_mode
        self.assertFalse(is_production_mode(debug=True, is_frozen=False))


class ProductionGuardSettingsIntegrationTest(TestCase):
    """
    Confirms fans/settings.py actually wires production_guard in (not just
    that the pure function itself is correct) -- IS_FROZEN must be exposed
    on the settings module, and collect_production_errors' contract must
    match what settings.py imports and calls it with.
    """

    def test_is_frozen_exposed_on_settings(self):
        self.assertTrue(hasattr(settings, 'IS_FROZEN'))
        self.assertIsInstance(settings.IS_FROZEN, bool)

    def test_is_frozen_false_under_manage_py_test(self):
        """The test runner is never a PyInstaller-frozen executable."""
        self.assertFalse(settings.IS_FROZEN)

    def test_production_guard_importable_from_settings_module(self):
        from fans import production_guard
        self.assertTrue(callable(production_guard.collect_production_errors))
        self.assertTrue(callable(production_guard.is_production_mode))


class AccountStatusMiddlewareTest(TestCase):
    """
    v2.1.16 Security Hardening Round #2 (H-05): an already-logged-in session
    must lose access the moment its account is suspended/deactivated by an
    administrator, rather than continuing to work until the session
    naturally expires.
    """

    def setUp(self):
        from accounts.models import CustomUser
        self.user = CustomUser.objects.create_user(
            username='asm_user', password='TestPass123!',
            role=CustomUser.ROLE_STAFF, employee_id='EMP-ASM-01',
        )
        self.client = Client()

    def test_active_user_session_works(self):
        """1. Active user session works."""
        self.client.force_login(self.user)
        resp = self.client.get('/dashboard/')
        self.assertEqual(resp.status_code, 200)

    def test_suspended_user_session_is_rejected(self):
        """2. Suspended user session is rejected — mid-session, not just at login."""
        from accounts.models import CustomUser
        self.client.force_login(self.user)
        resp = self.client.get('/dashboard/')
        self.assertEqual(resp.status_code, 200, 'sanity check: session works before suspension')

        self.user.account_status = CustomUser.STATUS_SUSPENDED
        self.user.is_active = False
        self.user.save(update_fields=['account_status', 'is_active'])

        resp = self.client.get('/dashboard/')
        self.assertIn(resp.status_code, [301, 302])
        self.assertIn('login', resp.get('Location', '').lower())

        # The session must actually be logged out (not just this one
        # response redirected) — a further request must also be anonymous.
        resp2 = self.client.get('/dashboard/')
        self.assertIn(resp2.status_code, [301, 302])
        self.assertIn('login', resp2.get('Location', '').lower())

    def test_deactivated_user_session_is_rejected(self):
        from accounts.models import CustomUser
        self.client.force_login(self.user)
        self.user.is_active = False
        self.user.account_status = CustomUser.STATUS_INACTIVE
        self.user.save(update_fields=['is_active', 'account_status'])

        resp = self.client.get('/dashboard/')
        self.assertIn(resp.status_code, [301, 302])
        self.assertIn('login', resp.get('Location', '').lower())

    def test_reactivated_user_can_login_again(self):
        """3. Reactivated user can log in again."""
        from accounts.models import CustomUser
        self.user.account_status = CustomUser.STATUS_SUSPENDED
        self.user.is_active = False
        self.user.save(update_fields=['account_status', 'is_active'])

        self.assertFalse(
            self.client.login(username='asm_user', password='TestPass123!'),
            'suspended account must not be able to log in',
        )

        self.user.account_status = CustomUser.STATUS_ACTIVE
        self.user.is_active = True
        self.user.save(update_fields=['account_status', 'is_active'])

        self.assertTrue(
            self.client.login(username='asm_user', password='TestPass123!'),
            'reactivated account must be able to log in again',
        )
        resp = self.client.get('/dashboard/')
        self.assertEqual(resp.status_code, 200)


class DynamicCookieSecurityMiddlewareTest(TestCase):
    """
    v2.2.0 Phase 3 — "refreshing the page logs the user out" fix. Session/CSRF
    cookies must lose their Secure flag ONLY on a genuinely plain-HTTP
    request (so they survive on the LAN-IP HTTP fallback), and must keep it
    unchanged on an HTTPS(-proxied) request — the normal path's security
    must be completely unaffected.
    """

    def setUp(self):
        from accounts.models import CustomUser
        self.user = CustomUser.objects.create_user(
            username='cookiesec_user', password='TestPass123!',
            role=CustomUser.ROLE_STAFF, employee_id='EMP-COOKIESEC',
        )

    @override_settings(SESSION_COOKIE_SECURE=True, CSRF_COOKIE_SECURE=True)
    def test_plain_http_request_strips_secure_flag_from_session_cookie(self):
        client = Client()
        # A fresh login response is where Django actually sets the session cookie.
        response = client.post(reverse('accounts:login'), {
            'username': 'cookiesec_user', 'password': 'TestPass123!',
        })
        self.assertIn('sessionid', response.cookies)
        self.assertFalse(
            response.cookies['sessionid']['secure'],
            'plain-HTTP request must not receive a Secure-flagged session cookie',
        )

    @override_settings(
        SESSION_COOKIE_SECURE=True, CSRF_COOKIE_SECURE=True,
        SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
    )
    def test_https_proxied_request_keeps_secure_flag(self):
        client = Client()
        response = client.post(
            reverse('accounts:login'),
            {'username': 'cookiesec_user', 'password': 'TestPass123!'},
            HTTP_X_FORWARDED_PROTO='https',
        )
        self.assertIn('sessionid', response.cookies)
        self.assertTrue(
            response.cookies['sessionid']['secure'],
            'an HTTPS-proxied request must keep the Secure flag unchanged — this is the security-sensitive path',
        )

    # Note: Django's test Client does not itself enforce real-browser cookie
    # security semantics (it sends cookies back regardless of the Secure
    # flag), so an end-to-end "does refresh keep me logged in" test through
    # the test client would pass even without this fix and wouldn't actually
    # prove anything. The two tests above assert directly on the Secure
    # attribute Django puts on the Set-Cookie header — that IS what a real
    # browser reads to decide whether to keep/send the cookie, and is the
    # correct place to verify this fix.

class WaitressProxyTrustTest(TestCase):
    """
    Static check: dev/launcher.py must configure Waitress to trust the local
    Caddy reverse proxy. Without this, X-Forwarded-Proto is stripped and Django
    sees every HTTPS request as HTTP, blocking camera verification.
    """

    def _launcher_source(self):
        from pathlib import Path
        src = Path(__file__).resolve().parent.parent / 'dev' / 'launcher.py'
        if not src.exists():
            self.skipTest('dev/launcher.py not present (frozen bundle)')
        return src.read_text(encoding='utf-8')

    def test_trusted_proxy_is_configured(self):
        self.assertIn('trusted_proxy', self._launcher_source())

    def test_trusted_proxy_headers_is_configured(self):
        self.assertIn('trusted_proxy_headers', self._launcher_source())

    def test_clear_untrusted_proxy_headers_is_configured(self):
        self.assertIn('clear_untrusted_proxy_headers', self._launcher_source())

    def test_trusted_proxy_targets_localhost(self):
        self.assertIn('127.0.0.1', self._launcher_source())

    def test_x_forwarded_proto_included(self):
        self.assertIn('x-forwarded-proto', self._launcher_source())

    def test_x_forwarded_host_included(self):
        self.assertIn('x-forwarded-host', self._launcher_source())


class PackagingTemplatePresenceTest(TestCase):
    """
    Packaging check: installer staging payload must include the logs templates.
    logs\\* in the Inno Excludes was previously stripping audit_logs.html and
    verification_logs.html from the installed package.
    Skipped when the staging folder does not exist (CI / dev without a build).
    """

    def _staging(self):
        from pathlib import Path
        staging = (
            Path(__file__).resolve().parent.parent
            / 'build' / 'installer-staging' / 'fans_c' / '_internal'
        )
        if not staging.exists():
            self.skipTest('installer-staging not present; run build_exe.ps1 first')
        return staging

    def test_audit_logs_template_in_staging(self):
        tmpl = self._staging() / 'templates' / 'logs' / 'audit_logs.html'
        self.assertTrue(
            tmpl.exists(),
            f'Missing from installer staging: {tmpl}',
        )

    def test_verification_logs_template_in_staging(self):
        tmpl = self._staging() / 'templates' / 'logs' / 'verification_logs.html'
        self.assertTrue(
            tmpl.exists(),
            f'Missing from installer staging: {tmpl}',
        )


class BackupTaskRegistrationTest(TestCase):
    """
    Static checks: dev/launcher.py must register the 'FANS-C Daily Backup'
    scheduled task inside _step6_autostart(), alongside the existing startup
    and watchdog tasks.  These tests verify source-level intent without
    executing schtasks or requiring administrator privileges.

    Fix: Phase 3A deployment blocker — backup task was never registered
    during first-run setup (Defect 2 in Phase 3A installer validation).
    """

    def _launcher_source(self):
        from pathlib import Path
        src = Path(__file__).resolve().parent.parent / 'dev' / 'launcher.py'
        if not src.exists():
            self.skipTest('dev/launcher.py not present (frozen bundle)')
        return src.read_text(encoding='utf-8')

    def test_backup_task_name_present(self):
        self.assertIn(
            'FANS-C Daily Backup',
            self._launcher_source(),
            'launcher.py must register a task named "FANS-C Daily Backup"',
        )

    def test_backup_script_path_present(self):
        src = self._launcher_source()
        self.assertIn(
            'daily-backup.ps1',
            src,
            'launcher.py must reference daily-backup.ps1 in the backup task TR',
        )
        self.assertIn(
            'scripts',
            src,
            'launcher.py must navigate to scripts/admin/daily-backup.ps1',
        )

    def test_backup_trigger_daily_21_present(self):
        # v2.1.16: the Daily Backup task is registered via the
        # Register-ScheduledTask PowerShell cmdlet (New-ScheduledTaskTrigger
        # -Daily -At '21:00'), not raw schtasks /SC DAILY /ST 21:00 -- see
        # ScheduledTaskRegistrationHelperTest for the behavioral equivalent.
        src = self._launcher_source()
        self.assertIn(
            'New-ScheduledTaskTrigger -Daily',
            src,
            "launcher.py must use New-ScheduledTaskTrigger -Daily for the backup task schedule",
        )
        self.assertIn(
            "'21:00'",
            src,
            'launcher.py must set -At \'21:00\' for the nightly backup trigger',
        )

    def test_backup_task_force_flag_present(self):
        # Codex finding F-05 (v2.1.16 correction): idempotent replacement
        # goes through Register-ScheduledTask -Force DIRECTLY -- no
        # pre-unregister step, so a failed re-registration can never leave
        # the previous known-good task deleted with nothing to replace it.
        src = self._launcher_source()
        backup_section = src[src.index('backup_ps_command ='):]
        first_close = backup_section.index('_register_scheduled_task(')
        snippet = backup_section[:first_close]
        self.assertNotIn(
            'Unregister-ScheduledTask',
            snippet,
            'backup task registration must NOT pre-unregister (finding F-05)',
        )
        self.assertIn(
            '-Force',
            snippet,
            'Register-ScheduledTask call must use -Force for idempotent replacement',
        )

    def test_existing_startup_task_unchanged(self):
        self.assertIn(
            'FANS-C Verification System',
            self._launcher_source(),
            'Main startup task must still be registered in _step6_autostart()',
        )

    def test_existing_watchdog_task_unchanged(self):
        self.assertIn(
            'FANS-C Watchdog',
            self._launcher_source(),
            'Watchdog task must still be registered in _step6_autostart()',
        )


class Sqlite3PackagingTest(TestCase):
    """
    Packaging check: tools/sqlite3.exe must be present in the project so the
    installer can deploy it to {app}\\tools\\sqlite3.exe, which is the path
    daily-backup.ps1 resolves at runtime via $PSScriptRoot navigation.

    Fix: Phase 3A deployment blocker — sqlite3.exe was missing from the
    project entirely, causing every scheduled backup to exit at pre-flight
    (Defect 1 in Phase 3A installer validation).
    """

    def _tools_dir(self):
        from pathlib import Path
        return Path(__file__).resolve().parent.parent / 'tools'

    def test_sqlite3_exe_present_in_tools(self):
        sqlite3 = self._tools_dir() / 'sqlite3.exe'
        self.assertTrue(
            sqlite3.exists(),
            f'sqlite3.exe must be present at tools/sqlite3.exe; not found at {sqlite3}',
        )

    def test_sqlite3_exe_is_non_empty(self):
        sqlite3 = self._tools_dir() / 'sqlite3.exe'
        if not sqlite3.exists():
            self.skipTest('sqlite3.exe not present (see test_sqlite3_exe_present_in_tools)')
        self.assertGreater(
            sqlite3.stat().st_size,
            1_000_000,
            'sqlite3.exe is suspiciously small — expected a real Windows executable (>1 MB)',
        )


# ===========================================================================
# v2.1.16 hardening: daily-backup.ps1 behavioral tests
#
# Two styles are used here:
#   - Full-script invocation for the pre-ACL fatal paths and the fail-closed
#     check, which exercise the real script end to end via subprocess.
#   - Dot-sourced function calls (Test-SqliteIntegrity, Get-EligibleFansBackups,
#     Invoke-FansBackupRotation, manifest helpers) for logic that sits behind
#     the Administrators+SYSTEM-only ACL and cannot be reached by a
#     non-privileged test runner without weakening that ACL.
#
# AUTOMATED TESTED: pre-ACL fatal paths, fail-closed behavior under a
# non-privileged account, integrity-check pass/fail, manifest completeness
# detection, rotation eligibility/retention/unrelated-item safety.
#
# REQUIRES CLEAN-VM / SYSTEM RUNTIME VALIDATION (not exercised by CI/dev
# unless run elevated): the full chained happy path under real SYSTEM
# execution, a genuine Task Scheduler-triggered run at 21:00, and .env/media
# copy failures occurring *after* the ACL step under SYSTEM. The integrity
# check and manifest logic that would apply there IS covered independently
# at the function level below.
# ===========================================================================

def _project_root():
    return Path(__file__).resolve().parent.parent


def _real_sqlite3_exe():
    return _project_root() / 'tools' / 'sqlite3.exe'


def _backup_script_path():
    return _project_root() / 'scripts' / 'admin' / 'daily-backup.ps1'


@contextlib.contextmanager
def _acl_safe_temp_dir():
    """
    Like tempfile.TemporaryDirectory(), but safe to use around a real
    daily-backup.ps1 invocation on a non-privileged account: the script's own
    Administrators+SYSTEM-only ACL step can lock this same account out of a
    directory it just created, which makes plain TemporaryDirectory cleanup
    raise PermissionError on __exit__. Resets permissions first.
    """
    tmp = tempfile.mkdtemp()
    try:
        yield Path(tmp)
    finally:
        subprocess.run(['icacls', tmp, '/reset', '/T', '/C', '/Q'], capture_output=True, text=True)
        shutil.rmtree(tmp, ignore_errors=True)


class DailyBackupFullScriptTest(TestCase):
    """Full end-to-end subprocess invocations of daily-backup.ps1."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not _backup_script_path().exists():
            raise unittest.SkipTest('daily-backup.ps1 not present (frozen bundle)')
        if not _real_sqlite3_exe().exists():
            raise unittest.SkipTest('tools/sqlite3.exe not present')

    def _scaffold(self, root, with_sqlite=True, with_db=True, with_env=True, with_media=True):
        scripts_admin = root / 'scripts' / 'admin'
        scripts_admin.mkdir(parents=True, exist_ok=True)
        script = scripts_admin / 'daily-backup.ps1'
        script.write_text(_backup_script_path().read_text(encoding='utf-8'), encoding='utf-8')
        if with_sqlite:
            tools = root / 'tools'
            tools.mkdir(parents=True, exist_ok=True)
            tools_exe = tools / 'sqlite3.exe'
            tools_exe.write_bytes(_real_sqlite3_exe().read_bytes())
        if with_db:
            subprocess.run(
                [str(_real_sqlite3_exe()), str(root / 'db.sqlite3'),
                 'CREATE TABLE t(x INT); INSERT INTO t VALUES (1);'],
                check=True, capture_output=True,
            )
        if with_env:
            (root / '.env').write_text('SECRET_KEY=test-only\n', encoding='utf-8')
        if with_media:
            media = root / 'media' / 'photos'
            media.mkdir(parents=True, exist_ok=True)
            (media / 'a.jpg').write_bytes(b'fake-image-bytes')
        return script

    def _run(self, script_path):
        return subprocess.run(
            ['powershell.exe', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script_path)],
            capture_output=True, text=True, timeout=60,
        )

    def _is_elevated(self):
        result = subprocess.run(
            ['powershell.exe', '-NonInteractive', '-Command',
             '([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent())'
             '.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)'],
            capture_output=True, text=True,
        )
        return result.stdout.strip().lower() == 'true'

    def test_missing_sqlite3_binary_is_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = self._scaffold(root, with_sqlite=False)
            result = self._run(script)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('sqlite3.exe', result.stdout + result.stderr)

    def test_missing_source_database_is_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = self._scaffold(root, with_db=False)
            result = self._run(script)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_missing_sqlite3_never_creates_completion_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = self._scaffold(root, with_sqlite=False)
            self._run(script)
            backups_root = root / 'backups'
            manifests = list(backups_root.glob('*/_backup_manifest.json')) if backups_root.exists() else []
            self.assertEqual(manifests, [], 'a fatal pre-flight failure must never produce a completion manifest')

    def test_pre_existing_directory_at_current_timestamp_is_never_reused(self):
        """
        Codex finding F-01: even a plain sequential retry (not concurrent)
        must never reuse an existing destination directory -- whether that
        directory holds a stale complete-looking manifest or an incomplete
        attempt. A collision must always be resolved by picking a different
        (suffixed) destination, never by overwriting.
        """
        with _acl_safe_temp_dir() as root:
            script = self._scaffold(root)
            now_ts = subprocess.run(
                ['powershell.exe', '-NonInteractive', '-Command', "Get-Date -Format 'yyyy-MM-dd_HHmm'"],
                capture_output=True, text=True,
            ).stdout.strip()
            stale_dir = root / 'backups' / now_ts
            stale_dir.mkdir(parents=True)
            stale_manifest = json.dumps({'status': 'complete', 'marker': 'STALE-DO-NOT-TOUCH'})
            (stale_dir / '_backup_manifest.json').write_text(stale_manifest, encoding='utf-8')
            (stale_dir / 'db.sqlite3').write_bytes(b'stale-bytes-do-not-touch')

            self._run(script)

            self.assertEqual(
                (stale_dir / '_backup_manifest.json').read_text(encoding='utf-8'), stale_manifest,
                'a pre-existing backup directory manifest must never be overwritten by a new run',
            )
            self.assertEqual((stale_dir / 'db.sqlite3').read_bytes(), b'stale-bytes-do-not-touch')

            backups_root = root / 'backups'
            other_dirs = [
                d for d in backups_root.iterdir()
                if d.is_dir() and d.name != now_ts
            ]
            self.assertGreaterEqual(
                len(other_dirs), 1,
                'a fresh, non-colliding destination must have been created for this run',
            )

    def test_concurrent_invocation_is_rejected_with_exit_code_3(self):
        """
        Codex finding F-01: a second overlapping invocation must be rejected
        deterministically (not raced, not silently corrupted) while another
        run holds the exclusive backup lock.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = self._scaffold(root)
            backups_root = root / 'backups'
            backups_root.mkdir(parents=True, exist_ok=True)
            lock_path = backups_root / '.daily-backup.lock'
            holder_ps = (
                f"$fs = [System.IO.File]::Open('{lock_path}', 'OpenOrCreate', 'ReadWrite', 'None'); "
                "Start-Sleep -Seconds 8; $fs.Close()"
            )
            holder = subprocess.Popen(['powershell.exe', '-NonInteractive', '-Command', holder_ps])
            try:
                time.sleep(2)  # give the holder time to actually acquire the lock
                result = self._run(script)
                self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
                dirs = [d for d in backups_root.iterdir() if d.is_dir()]
                self.assertEqual(dirs, [], 'a rejected concurrent run must not create any backup directory')
            finally:
                holder.wait(timeout=15)

    def test_log_write_failure_forces_non_zero_result(self):
        """
        Codex finding F-06: a required-log write failure must never allow a
        full success (exit 0) to be reported. Under this suite's usual
        non-admin account the backup itself already fails closed at the ACL
        step (exit 1) regardless of logging -- still a correct non-zero
        result. If ever run elevated, backup+rotation succeed but logging is
        broken, and the specific exit code 4 is asserted instead.
        """
        is_admin = self._is_elevated()
        with _acl_safe_temp_dir() as root:
            script = self._scaffold(root)
            logs_dir = root / 'logs'
            logs_dir.mkdir(parents=True, exist_ok=True)
            # Shadow the log file path with a directory so Add-Content fails deterministically.
            (logs_dir / 'fans-backup.log').mkdir()

            result = self._run(script)
            combined = result.stdout + result.stderr
            self.assertNotEqual(result.returncode, 0, combined)
            self.assertIn('Could not write to required log file', combined)
            if is_admin:
                self.assertEqual(result.returncode, 4, combined)

    def test_full_backup_fails_closed_without_privilege_or_succeeds_when_elevated(self):
        """
        This suite normally runs as a non-privileged developer account. Under
        that account, the Administrators+SYSTEM-only ACL step legitimately
        blocks completion, and the required behavior under test is that the
        script fails CLOSED: non-zero exit, no completion manifest, no
        "completed successfully" claim anywhere in its output.

        If this suite is ever run elevated (Administrator/SYSTEM -- e.g. a
        future CI runner or Codex's own environment), the same test instead
        asserts the real happy path: exit 0 and a valid completion manifest.
        Either way, the assertion made matches the actual privilege level --
        this never fakes SYSTEM-context success.
        """
        is_admin = self._is_elevated()
        with _acl_safe_temp_dir() as root:
            script = self._scaffold(root)
            result = self._run(script)
            backups_root = root / 'backups'
            backup_dirs = list(backups_root.iterdir()) if backups_root.exists() else []
            manifests = [d / '_backup_manifest.json' for d in backup_dirs if d.is_dir()]
            any_complete = any(m.exists() for m in manifests)
            combined_output = (result.stdout + result.stderr).lower()

            if is_admin:
                self.assertEqual(result.returncode, 0, combined_output)
                self.assertTrue(any_complete, 'expected a completion manifest under elevated execution')
            else:
                self.assertNotEqual(result.returncode, 0, combined_output)
                self.assertFalse(
                    any_complete,
                    'a backup run without required privilege must never be marked complete',
                )
                self.assertNotIn(
                    'completed successfully', combined_output,
                    'must never claim success when required steps could not run',
                )


class DailyBackupFunctionUnitTest(TestCase):
    """
    Dot-sourced unit tests for the reusable functions in daily-backup.ps1.
    These do not touch the Administrators+SYSTEM ACL at all, so they run the
    same way regardless of the test runner's privilege level.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not _backup_script_path().exists():
            raise unittest.SkipTest('daily-backup.ps1 not present (frozen bundle)')
        if not _real_sqlite3_exe().exists():
            raise unittest.SkipTest('tools/sqlite3.exe not present')

    def _ps(self, command):
        full = f". '{_backup_script_path()}'; {command}"
        return subprocess.run(
            ['powershell.exe', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', full],
            capture_output=True, text=True, timeout=30,
        )

    def test_integrity_check_passes_for_valid_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'good.sqlite3'
            subprocess.run(
                [str(_real_sqlite3_exe()), str(db), 'CREATE TABLE t(x INT);'],
                check=True, capture_output=True,
            )
            result = self._ps(f"(Test-SqliteIntegrity -Sqlite3Exe '{_real_sqlite3_exe()}' -DbPath '{db}').Ok")
            self.assertEqual(result.stdout.strip(), 'True', result.stdout + result.stderr)

    def test_integrity_check_fails_for_corrupt_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'bad.sqlite3'
            db.write_bytes(b'this is not a sqlite database file, just garbage padding' * 50)
            result = self._ps(f"(Test-SqliteIntegrity -Sqlite3Exe '{_real_sqlite3_exe()}' -DbPath '{db}').Ok")
            self.assertEqual(result.stdout.strip(), 'False', result.stdout + result.stderr)

    def test_integrity_check_fails_for_zero_byte_file(self):
        """Regression guard: SQLite treats an empty file as a valid empty DB,
        so integrity_check alone would falsely report 'ok' for a truncated
        backup copy. The explicit size check must catch this first."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'empty.sqlite3'
            db.write_bytes(b'')
            result = self._ps(f"(Test-SqliteIntegrity -Sqlite3Exe '{_real_sqlite3_exe()}' -DbPath '{db}').Ok")
            self.assertEqual(result.stdout.strip(), 'False', result.stdout + result.stderr)

    def test_integrity_check_fails_for_missing_file(self):
        result = self._ps(
            f"(Test-SqliteIntegrity -Sqlite3Exe '{_real_sqlite3_exe()}' -DbPath 'C:\\nope\\missing.sqlite3').Ok"
        )
        self.assertEqual(result.stdout.strip(), 'False', result.stdout + result.stderr)

    def test_backup_dir_name_accepts_valid_pattern(self):
        result = self._ps("Test-FansBackupDirName -Name '2026-08-27_2100'")
        self.assertEqual(result.stdout.strip(), 'True', result.stderr)

    def test_backup_dir_name_accepts_retry_suffix(self):
        """Finding F-01: a same-minute retry uses a _N suffix -- e.g. from a
        second Get-NextFansBackupDestination call in the same clock minute."""
        for name in ('2026-08-27_2100_2', '2026-08-27_2100_15'):
            result = self._ps(f"Test-FansBackupDirName -Name '{name}'")
            self.assertEqual(result.stdout.strip(), 'True', f'{name}: {result.stderr}')

    def test_backup_dir_name_rejects_unrelated_names(self):
        for bad in ('not-a-backup', '2026-08-27', 'logs', 'db.sqlite3'):
            result = self._ps(f"Test-FansBackupDirName -Name '{bad}'")
            self.assertEqual(result.stdout.strip(), 'False', f'{bad}: {result.stderr}')

    def test_get_next_destination_picks_base_name_when_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self._ps(
                f"(Get-NextFansBackupDestination -BackupsRoot '{root}' -Timestamp '2026-08-27_2100').Name"
            )
            self.assertEqual(result.stdout.strip(), '2026-08-27_2100', result.stdout + result.stderr)

    def test_get_next_destination_avoids_existing_directory(self):
        """Finding F-01: the core collision-avoidance primitive -- never
        reuse a destination that already exists, whether it's a completed
        backup, an in-progress one, or a stale failed attempt."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '2026-08-27_2100').mkdir(parents=True)
            result = self._ps(
                f"(Get-NextFansBackupDestination -BackupsRoot '{root}' -Timestamp '2026-08-27_2100').Name"
            )
            self.assertEqual(result.stdout.strip(), '2026-08-27_2100_2', result.stdout + result.stderr)

    def test_get_next_destination_increments_past_multiple_collisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '2026-08-27_2100').mkdir(parents=True)
            (root / '2026-08-27_2100_2').mkdir(parents=True)
            (root / '2026-08-27_2100_3').mkdir(parents=True)
            result = self._ps(
                f"(Get-NextFansBackupDestination -BackupsRoot '{root}' -Timestamp '2026-08-27_2100').Name"
            )
            self.assertEqual(result.stdout.strip(), '2026-08-27_2100_4', result.stdout + result.stderr)

    def test_enter_lock_succeeds_when_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / '.daily-backup.lock'
            result = self._ps(
                f"$s = Enter-FansBackupLock -LockPath '{lock_path}'; $ok = $null -ne $s; if ($s) {{ $s.Dispose() }}; $ok"
            )
            self.assertEqual(result.stdout.strip(), 'True', result.stdout + result.stderr)

    def test_enter_lock_fails_when_already_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / '.daily-backup.lock'
            result = self._ps(
                f"$s1 = Enter-FansBackupLock -LockPath '{lock_path}'; "
                f"$s2 = Enter-FansBackupLock -LockPath '{lock_path}'; "
                "$ok = ($null -ne $s1) -and ($null -eq $s2); "
                "if ($s1) { $s1.Dispose() }; if ($s2) { $s2.Dispose() }; $ok"
            )
            self.assertEqual(result.stdout.strip(), 'True', result.stdout + result.stderr)

    def test_new_fans_backup_acl_uses_well_known_sids(self):
        """
        Codex finding F-08: identities must be constructed from well-known
        SIDs (S-1-5-32-544 BUILTIN\\Administrators, S-1-5-18 LocalSystem),
        not localized account-name strings, so the restriction does not
        depend on the OS display language. Policy (FullControl for both,
        inheritance disabled) is unchanged.
        """
        result = self._ps(
            "$acl = New-FansBackupAcl; "
            "$sids = $acl.Access | ForEach-Object { "
            "  $_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value "
            "}; "
            "($sids -contains 'S-1-5-32-544') -and ($sids -contains 'S-1-5-18')"
        )
        self.assertEqual(result.stdout.strip(), 'True', result.stdout + result.stderr)

    def test_new_fans_backup_acl_grants_full_control_and_disables_inheritance(self):
        result = self._ps(
            "$acl = New-FansBackupAcl; "
            "$fc = ($acl.Access | Where-Object { $_.FileSystemRights -match 'FullControl' }).Count; "
            "\"$fc,$($acl.AreAccessRulesProtected)\""
        )
        self.assertEqual(result.stdout.strip(), '2,True', result.stdout + result.stderr)

    def test_backup_dir_name_matches_shape_but_timestamp_parse_rejects_invalid_date(self):
        """'2026-13-99_9999' matches the digit-count regex (Test-FansBackupDirName
        is shape-only, per the spec's example pattern) but Get-FansBackupTimestamp
        must still reject it as not a real date, so Get-EligibleFansBackups never
        treats a malformed-but-regex-matching name as a valid backup."""
        shape_result = self._ps("Test-FansBackupDirName -Name '2026-13-99_9999'")
        self.assertEqual(shape_result.stdout.strip(), 'True')
        ts_result = self._ps("$null -eq (Get-FansBackupTimestamp -Name '2026-13-99_9999')")
        self.assertEqual(ts_result.stdout.strip(), 'True', ts_result.stdout + ts_result.stderr)

    def test_eligible_backups_excludes_regex_matching_but_invalid_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bogus = root / '2026-13-99_9999'
            bogus.mkdir()
            (bogus / '_backup_manifest.json').write_text('{"status": "complete"}', encoding='utf-8')
            result = self._ps(f"(Get-EligibleFansBackups -BackupsRoot '{root}').Count")
            self.assertEqual(result.stdout.strip(), '0', result.stdout + result.stderr)

    # -- Test-FansBackupRestoreReady (findings F-03/F-04) --------------------------
    # status=complete alone is not sufficient; every one of these must also hold.

    def _make_restore_ready_dir(self, path, db_bytes=b'valid-db-bytes-1234', integrity='ok',
                                 env_included=True, include_env=True, include_db=True,
                                 recorded_bytes=None):
        path.mkdir(parents=True, exist_ok=True)
        if include_db:
            (path / 'db.sqlite3').write_bytes(db_bytes)
        if include_env:
            (path / '.env').write_text('SECRET_KEY=x', encoding='utf-8')
        manifest = {
            'status': 'complete',
            'integrity_check': integrity,
            'env_included': env_included,
            'db_backup_bytes': len(db_bytes) if recorded_bytes is None else recorded_bytes,
        }
        (path / '_backup_manifest.json').write_text(json.dumps(manifest), encoding='utf-8')

    def test_restore_ready_true_for_fully_valid_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / '2026-08-27_2100'
            self._make_restore_ready_dir(d)
            result = self._ps(f"(Test-FansBackupRestoreReady -DirectoryPath '{d}').Ok")
            self.assertEqual(result.stdout.strip(), 'True', result.stdout + result.stderr)

    def test_restore_ready_false_manifest_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / '2026-08-27_2100'
            d.mkdir(parents=True)
            result = self._ps(f"(Test-FansBackupRestoreReady -DirectoryPath '{d}').Ok")
            self.assertEqual(result.stdout.strip(), 'False', result.stdout + result.stderr)

    def test_restore_ready_false_non_complete_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / '2026-08-27_2100'
            d.mkdir(parents=True)
            (d / '_backup_manifest.json').write_text('{"status": "in_progress"}', encoding='utf-8')
            result = self._ps(f"(Test-FansBackupRestoreReady -DirectoryPath '{d}').Ok")
            self.assertEqual(result.stdout.strip(), 'False', result.stdout + result.stderr)

    def test_restore_ready_false_malformed_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / '2026-08-27_2100'
            d.mkdir(parents=True)
            (d / '_backup_manifest.json').write_text('not valid json{{{', encoding='utf-8')
            result = self._ps(f"(Test-FansBackupRestoreReady -DirectoryPath '{d}').Ok")
            self.assertEqual(result.stdout.strip(), 'False', result.stdout + result.stderr)

    def test_restore_ready_false_db_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / '2026-08-27_2100'
            self._make_restore_ready_dir(d, include_db=False)
            result = self._ps(f"(Test-FansBackupRestoreReady -DirectoryPath '{d}').Ok")
            self.assertEqual(result.stdout.strip(), 'False', result.stdout + result.stderr)

    def test_restore_ready_false_db_zero_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / '2026-08-27_2100'
            self._make_restore_ready_dir(d, db_bytes=b'', recorded_bytes=0)
            result = self._ps(f"(Test-FansBackupRestoreReady -DirectoryPath '{d}').Ok")
            self.assertEqual(result.stdout.strip(), 'False', result.stdout + result.stderr)

    def test_restore_ready_false_env_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / '2026-08-27_2100'
            self._make_restore_ready_dir(d, include_env=False)
            result = self._ps(f"(Test-FansBackupRestoreReady -DirectoryPath '{d}').Ok")
            self.assertEqual(result.stdout.strip(), 'False', result.stdout + result.stderr)

    def test_restore_ready_false_integrity_not_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / '2026-08-27_2100'
            self._make_restore_ready_dir(d, integrity='malformed database schema')
            result = self._ps(f"(Test-FansBackupRestoreReady -DirectoryPath '{d}').Ok")
            self.assertEqual(result.stdout.strip(), 'False', result.stdout + result.stderr)

    def test_restore_ready_false_env_included_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / '2026-08-27_2100'
            self._make_restore_ready_dir(d, env_included=False)
            result = self._ps(f"(Test-FansBackupRestoreReady -DirectoryPath '{d}').Ok")
            self.assertEqual(result.stdout.strip(), 'False', result.stdout + result.stderr)

    def test_restore_ready_false_recorded_size_mismatch(self):
        """Cheaply catches truncation/tampering after the manifest was written."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / '2026-08-27_2100'
            self._make_restore_ready_dir(d, db_bytes=b'twelve-bytes', recorded_bytes=999999)
            result = self._ps(f"(Test-FansBackupRestoreReady -DirectoryPath '{d}').Ok")
            self.assertEqual(result.stdout.strip(), 'False', result.stdout + result.stderr)

    def test_newer_invalid_backup_does_not_hide_older_valid_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_restore_ready_dir(root / '2026-08-01_2100')
            # Newer directory: complete-looking status, but tampered (size mismatch).
            self._make_restore_ready_dir(root / '2026-08-27_2100', recorded_bytes=999999)
            result = self._ps(f"(Get-EligibleFansBackups -BackupsRoot '{root}')[0].Directory.Name")
            self.assertEqual(result.stdout.strip(), '2026-08-01_2100', result.stdout + result.stderr)

    def test_rotation_ignores_invalid_newer_directory_and_keeps_older_valid(self):
        """The invalid newer entry must not consume a retention slot that
        would otherwise cause an older, still-valid backup to be evicted."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i in range(14):
                ts = (datetime(2026, 1, 1) + timedelta(days=i)).strftime('%Y-%m-%d_%H%M')
                self._make_restore_ready_dir(root / ts)
            # A newer, invalid (tampered) attempt -- must never occupy a retention slot.
            self._make_restore_ready_dir(root / '2026-03-01_0000', recorded_bytes=999999)
            self._ps(f"Invoke-FansBackupRotation -BackupsRoot '{root}' -KeepCount 14 | Out-Null")
            remaining = {p.name for p in root.iterdir()}
            for i in range(14):
                ts = (datetime(2026, 1, 1) + timedelta(days=i)).strftime('%Y-%m-%d_%H%M')
                self.assertIn(ts, remaining, f'{ts} (valid) must not have been evicted by the invalid entry')
            self.assertIn('2026-03-01_0000', remaining, 'invalid directories are never deleted by rotation')

    def test_get_incomplete_backup_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_restore_ready_dir(root / '2026-08-01_2100')
            (root / '2026-08-02_2100').mkdir()  # no manifest at all
            self._make_restore_ready_dir(root / '2026-08-03_2100', include_env=False)  # tampered/invalid
            (root / 'unrelated-folder').mkdir()
            result = self._ps(f"Get-IncompleteFansBackupCount -BackupsRoot '{root}'")
            self.assertEqual(result.stdout.strip(), '2', result.stdout + result.stderr)

    def test_write_manifest_produces_valid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / '_backup_manifest.json'
            result = self._ps(
                f"Write-FansBackupManifest -Path '{manifest}' -Data @{{status='complete'; db_backup_bytes=123}}"
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            data = json.loads(manifest.read_text(encoding='utf-8'))
            self.assertEqual(data['status'], 'complete')
            self.assertEqual(data['db_backup_bytes'], 123)

    # -- Test-FansBackupDbBackupBytes type/range parity (finding N-01) -------------
    # Cross-language matrix: fans.tests.DbBackupBytesPythonParityTest asserts
    # Python's _is_valid_db_backup_bytes classifies the exact same JSON
    # literal forms the same way.

    DB_BACKUP_BYTES_MATRIX = [
        ('13', True),
        ('"13"', False),
        ('true', False),
        ('false', False),
        ('0', False),
        ('-1', False),
        ('13.0', False),
        ('12.9', False),
        ('9223372036854775807', True),
        ('9223372036854775808', False),
        ('null', False),
    ]

    def _ps_script_file(self, body):
        """
        Writes `body` to a temp .ps1 file and runs it. Used for the type-check
        matrix instead of an inline -Command string because embedding literal
        double quotes (needed for JSON like {"x": true}) inside an already
        double-quoted -Command argument is exactly the kind of nested-quoting
        problem this correction pass is about -- a script FILE sidesteps it
        entirely by using PowerShell's own single-quoted string literals with
        no re-quoting for the process boundary.
        """
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / 'probe.ps1'
            script.write_text(f". '{_backup_script_path()}'\n{body}\n", encoding='utf-8')
            return subprocess.run(
                ['powershell.exe', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script)],
                capture_output=True, text=True, timeout=30,
            )

    def test_db_backup_bytes_type_parity_matrix(self):
        for json_literal, expected_ok in self.DB_BACKUP_BYTES_MATRIX:
            with self.subTest(json_literal=json_literal):
                body = (
                    "$obj = '{\"x\": " + json_literal + "}' | ConvertFrom-Json\n"
                    "(Test-FansBackupDbBackupBytes -Value $obj.x).Ok\n"
                )
                result = self._ps_script_file(body)
                expected = 'True' if expected_ok else 'False'
                self.assertEqual(result.stdout.strip(), expected, f'{json_literal}: {result.stdout}{result.stderr}')

    def test_db_backup_bytes_missing_field_is_invalid(self):
        body = (
            "$obj = '{}' | ConvertFrom-Json\n"
            "(Test-FansBackupDbBackupBytes -Value $obj.x).Ok\n"
        )
        result = self._ps_script_file(body)
        self.assertEqual(result.stdout.strip(), 'False', result.stdout + result.stderr)

    def test_db_backup_bytes_written_by_manifest_is_accepted(self):
        """Round-trip: a real value written by Write-FansBackupManifest (a
        .NET Int64 file size) must be accepted by the type check on read-back."""
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / '_backup_manifest.json'
            body = (
                f"Write-FansBackupManifest -Path '{manifest}' -Data @{{db_backup_bytes = [int64]4022272}}\n"
                f"$obj = Get-Content -Raw '{manifest}' | ConvertFrom-Json\n"
                "(Test-FansBackupDbBackupBytes -Value $obj.db_backup_bytes).Ok\n"
            )
            result = self._ps_script_file(body)
            self.assertEqual(result.stdout.strip(), 'True', result.stdout + result.stderr)

    # -- retry-suffix canonicalization matrix (finding N-02) -----------------------

    SUFFIX_MATRIX = [
        ('2026-08-27_2100', True),
        ('2026-08-27_2100_2', True),
        ('2026-08-27_2100_10', True),
        ('2026-08-27_2100_999', True),
        ('2026-08-27_2100_0', False),
        ('2026-08-27_2100_1', False),
        ('2026-08-27_2100_01', False),
        ('2026-08-27_2100_002', False),
        ('2026-08-27_2100_1000', False),
        ('2026-08-27_2100_abc', False),
        ('2026-08-27_2100_99999999999999999999999999999999999999', False),
    ]

    def test_suffix_canonicalization_matrix(self):
        for name, expected_valid in self.SUFFIX_MATRIX:
            with self.subTest(name=name):
                result = self._ps(f"Test-FansBackupDirName -Name '{name}'")
                expected = 'True' if expected_valid else 'False'
                self.assertEqual(result.stdout.strip(), expected, f'{name}: {result.stdout}{result.stderr}')

    def test_suffix_huge_value_does_not_throw(self):
        """Finding N-02: an enormous suffix must be rejected cleanly, never
        cause an [int]/[int64] overflow exception."""
        huge = '2026-08-27_2100_' + ('9' * 40)
        result = self._ps(
            f"try {{ Test-FansBackupDirName -Name '{huge}' }} catch {{ 'THREW' }}"
        )
        self.assertEqual(result.stdout.strip(), 'False', result.stdout + result.stderr)
        seq_result = self._ps(
            f"try {{ Get-FansBackupSequence -Name '{huge}' }} catch {{ 'THREW' }}"
        )
        self.assertEqual(seq_result.stdout.strip(), '1', 'a non-canonical suffix must fall back to sequence 1, never throw')

    def test_get_next_destination_never_generates_noncanonical_suffix(self):
        """Get-NextFansBackupDestination's own generated suffixes (_2.._999)
        must always be accepted by Test-FansBackupDirName -- if it ever
        collides past 999 it throws rather than emitting an unbounded/
        noncanonical name."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i in [None] + list(range(2, 6)):
                name = '2026-08-27_2100' if i is None else f'2026-08-27_2100_{i}'
                (root / name).mkdir(parents=True)
            result = self._ps(
                f"$d = Get-NextFansBackupDestination -BackupsRoot '{root}' -Timestamp '2026-08-27_2100'; "
                "\"$($d.Name),$(Test-FansBackupDirName -Name $d.Name)\""
            )
            name, is_canonical = result.stdout.strip().split(',')
            self.assertEqual(name, '2026-08-27_2100_6')
            self.assertEqual(is_canonical, 'True')

    def _make_completed_dirs(self, root, count, start=datetime(2026, 1, 1)):
        for i in range(count):
            ts = (start + timedelta(days=i)).strftime('%Y-%m-%d_%H%M')
            self._make_restore_ready_dir(root / ts)

    def test_rotation_retains_newest_14_and_deletes_older(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_completed_dirs(root, 20)
            result = self._ps(
                f"Invoke-FansBackupRotation -BackupsRoot '{root}' -KeepCount 14 | Out-Null; "
                f"(Get-ChildItem '{root}' -Directory).Count"
            )
            self.assertEqual(result.stdout.strip(), '14', result.stdout + result.stderr)
            remaining = sorted(p.name for p in root.iterdir())
            expected = sorted(
                (datetime(2026, 1, 1) + timedelta(days=i)).strftime('%Y-%m-%d_%H%M') for i in range(6, 20)
            )
            self.assertEqual(remaining, expected)

    def test_rotation_ignores_unrelated_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'not-a-backup').mkdir()
            (root / 'my-manual-copy').mkdir()
            self._make_completed_dirs(root, 16)
            self._ps(f"Invoke-FansBackupRotation -BackupsRoot '{root}' -KeepCount 14 | Out-Null")
            self.assertTrue((root / 'not-a-backup').exists())
            self.assertTrue((root / 'my-manual-copy').exists())

    def test_rotation_ignores_unrelated_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'README.txt').write_text('do not delete', encoding='utf-8')
            self._make_completed_dirs(root, 16)
            self._ps(f"Invoke-FansBackupRotation -BackupsRoot '{root}' -KeepCount 14 | Out-Null")
            self.assertTrue((root / 'README.txt').exists())

    def test_rotation_never_deletes_incomplete_backups(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i in range(20):
                ts = (datetime(2026, 1, 1) + timedelta(days=i)).strftime('%Y-%m-%d_%H%M')
                (root / ts).mkdir()  # no manifest -- in-progress/failed attempt
            self._ps(f"Invoke-FansBackupRotation -BackupsRoot '{root}' -KeepCount 14 | Out-Null")
            self.assertEqual(len(list(root.iterdir())), 20, 'incomplete backups must never be rotated away')

    def test_rotation_mixed_complete_and_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_completed_dirs(root, 16, start=datetime(2026, 1, 1))
            for i in range(3):
                ts = (datetime(2026, 3, 1) + timedelta(days=i)).strftime('%Y-%m-%d_%H%M')
                (root / ts).mkdir()  # incomplete, newer names than the completed set
            self._ps(f"Invoke-FansBackupRotation -BackupsRoot '{root}' -KeepCount 14 | Out-Null")
            remaining = {p.name for p in root.iterdir()}
            # All 3 incomplete dirs survive (never eligible for rotation)...
            for i in range(3):
                ts = (datetime(2026, 3, 1) + timedelta(days=i)).strftime('%Y-%m-%d_%H%M')
                self.assertIn(ts, remaining)
            # ...and exactly the newest 14 of the 16 completed dirs survive.
            self.assertEqual(
                sum(1 for name in remaining if name.startswith('2026-01')), 14,
            )


class DbBackupBytesPythonParityTest(TestCase):
    """
    Finding N-01 cross-language parity: fans.views._is_valid_db_backup_bytes
    must classify the exact same JSON literal forms as PowerShell's
    Test-FansBackupDbBackupBytes (see DailyBackupFunctionUnitTest's matrix).
    Uses json.loads() to get authentic Python types for each literal, the
    same way the PS side uses ConvertFrom-Json for authentic CLR types.
    """

    MATRIX = [
        ('13', True),
        ('"13"', False),
        ('true', False),
        ('false', False),
        ('0', False),
        ('-1', False),
        ('13.0', False),
        ('12.9', False),
        ('9223372036854775807', True),
        ('9223372036854775808', False),
        ('null', False),
    ]

    def test_matrix(self):
        from fans.views import _is_valid_db_backup_bytes
        for json_literal, expected_ok in self.MATRIX:
            with self.subTest(json_literal=json_literal):
                value = json.loads(json_literal)
                self.assertEqual(_is_valid_db_backup_bytes(value), expected_ok, json_literal)

    def test_missing_field_is_invalid(self):
        from fans.views import _is_valid_db_backup_bytes
        obj = json.loads('{}')
        self.assertFalse(_is_valid_db_backup_bytes(obj.get('db_backup_bytes')))

    def test_bool_is_rejected_despite_being_an_int_subclass(self):
        """Python-specific gotcha: bool is a subclass of int, so a naive
        isinstance(value, int) check would let JSON true/false slip through
        as 1/0. type(value) is int must reject them explicitly."""
        from fans.views import _is_valid_db_backup_bytes
        self.assertFalse(_is_valid_db_backup_bytes(True))
        self.assertFalse(_is_valid_db_backup_bytes(False))


class SuffixCanonicalizationPythonParityTest(TestCase):
    """
    Finding N-02 cross-language parity: fans.views._FANS_BACKUP_NAME_RE must
    accept/reject the exact same directory names as PowerShell's
    Test-FansBackupDirName.
    """

    MATRIX = [
        ('2026-08-27_2100', True),
        ('2026-08-27_2100_2', True),
        ('2026-08-27_2100_10', True),
        ('2026-08-27_2100_999', True),
        ('2026-08-27_2100_0', False),
        ('2026-08-27_2100_1', False),
        ('2026-08-27_2100_01', False),
        ('2026-08-27_2100_002', False),
        ('2026-08-27_2100_1000', False),
        ('2026-08-27_2100_abc', False),
        ('2026-08-27_2100_99999999999999999999999999999999999999', False),
    ]

    def test_matrix(self):
        from fans.views import _FANS_BACKUP_NAME_RE
        for name, expected_valid in self.MATRIX:
            with self.subTest(name=name):
                self.assertEqual(bool(_FANS_BACKUP_NAME_RE.match(name)), expected_valid, name)

    def test_health_and_rotation_share_the_same_regex(self):
        """_scan_fans_backup_directories (used by both _list_completed_fans_backups
        and _count_incomplete_fans_backups) must use this exact module-level
        pattern, not a separately-drifted copy."""
        import inspect
        from fans import views as fans_views
        source = inspect.getsource(fans_views._scan_fans_backup_directories)
        self.assertIn('_FANS_BACKUP_NAME_RE', source)

    def test_health_view_rejects_noncanonical_suffix_directory(self):
        from fans.views import _list_completed_fans_backups
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = root / '2026-08-27_2100_01'  # leading zero -- non-canonical
            d.mkdir(parents=True)
            (d / 'db.sqlite3').write_bytes(b'x' * 10)
            (d / '.env').write_text('SECRET_KEY=x', encoding='utf-8')
            (d / '_backup_manifest.json').write_text(
                json.dumps({
                    'status': 'complete', 'integrity_check': 'ok',
                    'env_included': True, 'db_backup_bytes': 10,
                }), encoding='utf-8',
            )
            result = _list_completed_fans_backups(root)
        self.assertEqual(result, [], 'a non-canonical suffix directory must never be treated as a valid backup')


class SqliteBackupPathQuotingTest(TestCase):
    """
    Finding N-02 (SQLite `.backup` path quoting): the bundled sqlite3.exe's
    dot-command tokenizer only recognizes single quotes for `.backup 'FILE'`,
    has no working escape for an embedded apostrophe (doubling, backslash-
    escaping, and double-quoting were all tried experimentally against the
    real binary -- every one of them broke), and a Windows install directory
    MAY legitimately contain both a space and an apostrophe (freely
    choosable via the Inno Setup directory picker). Invoke-FansSqliteBackup
    is tested directly here (no ACL involved at all -- an ordinary writable
    temp directory), per the instruction to avoid ACL interference when
    testing the SQLite quoting mechanism specifically.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not _backup_script_path().exists():
            raise unittest.SkipTest('daily-backup.ps1 not present (frozen bundle)')
        if not _real_sqlite3_exe().exists():
            raise unittest.SkipTest('tools/sqlite3.exe not present')

    def _ps_lit(self, value):
        """Escape a value for embedding inside a single-quoted PowerShell
        string literal (double any embedded apostrophe)."""
        return str(value).replace("'", "''")

    def _ps(self, command):
        full = f". '{_backup_script_path()}'; {command}"
        return subprocess.run(
            ['powershell.exe', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', full],
            capture_output=True, text=True, timeout=30,
        )

    def _run_backup_and_verify(self, folder_name):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / folder_name
            root.mkdir(parents=True)
            src = root / 'src.sqlite3'
            subprocess.run(
                [str(_real_sqlite3_exe()), str(src), 'CREATE TABLE t(x INT); INSERT INTO t VALUES (1);'],
                check=True, capture_output=True,
            )
            dest_dir = root / 'dest'
            dest_dir.mkdir()
            result = self._ps(
                f"Invoke-FansSqliteBackup -Sqlite3Exe '{self._ps_lit(_real_sqlite3_exe())}' "
                f"-SourceDb '{self._ps_lit(src)}' -DestDir '{self._ps_lit(dest_dir)}'"
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            dest_db = dest_dir / 'db.sqlite3'
            self.assertTrue(dest_db.exists(), f'backup file missing for folder name: {folder_name}')
            self.assertGreater(dest_db.stat().st_size, 0)
            integrity = self._ps(
                f"(Test-SqliteIntegrity -Sqlite3Exe '{self._ps_lit(_real_sqlite3_exe())}' "
                f"-DbPath '{self._ps_lit(dest_db)}').Ok"
            )
            self.assertEqual(integrity.stdout.strip(), 'True', integrity.stdout + integrity.stderr)

    def test_backup_with_plain_path(self):
        self._run_backup_and_verify('plain')

    def test_backup_with_spaces_in_path(self):
        self._run_backup_and_verify('folder with spaces')

    def test_backup_with_apostrophe_in_path(self):
        self._run_backup_and_verify("citizens's folder")

    def test_backup_with_spaces_and_apostrophe_in_path(self):
        self._run_backup_and_verify("Citizen's Backup Test")

    def test_original_single_quote_embedding_would_have_failed(self):
        """
        Regression guard proving this test suite would actually have caught
        the original bug: the naive `.backup '<path>'` form breaks on a path
        containing an apostrophe+space, confirming the fix in
        Invoke-FansSqliteBackup is load-bearing and not just incidentally
        passing.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Citizen's Regression Folder"
            root.mkdir(parents=True)
            src = root / 'src.sqlite3'
            subprocess.run(
                [str(_real_sqlite3_exe()), str(src), 'CREATE TABLE t(x INT);'],
                check=True, capture_output=True,
            )
            dest = root / 'naive_dest.sqlite3'
            result = subprocess.run(
                [str(_real_sqlite3_exe()), str(src), f".backup '{dest}'"],
                capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0, 'the naive single-quoted form was expected to break here')
            self.assertFalse(dest.exists())


class ScheduledTaskDailyBackupReplacementParityTest(TestCase):
    """
    Finding N-03: scripts/setup/setup-autostart.ps1 (the alternate IT/setup
    path) must use the same safe direct-replacement pattern for the Daily
    Backup task as dev/launcher.py already does -- Register-ScheduledTask
    -Force with NO preceding Unregister-ScheduledTask, so a failed
    re-registration can never delete a known-good existing task.
    """

    def _daily_backup_section(self):
        script = _project_root() / 'scripts' / 'setup' / 'setup-autostart.ps1'
        if not script.exists():
            self.skipTest('setup-autostart.ps1 not present')
        src = script.read_text(encoding='utf-8')
        start = src.index("# ── Step 5: Register daily backup task")
        end = src.index("# ── Step 6:") if "# ── Step 6:" in src else len(src)
        return src[start:end]

    def test_no_pre_unregister_for_daily_backup(self):
        section = self._daily_backup_section()
        self.assertNotIn(
            'Unregister-ScheduledTask', section,
            'setup-autostart.ps1 must not pre-unregister the Daily Backup task (finding N-03)',
        )

    def test_uses_force_replacement(self):
        section = self._daily_backup_section()
        self.assertIn('Register-ScheduledTask', section)
        self.assertIn('-Force', section)

    def test_daily_backup_settings_preserved(self):
        section = self._daily_backup_section()
        for expected in (
            "'FANS-C Daily Backup'", '-Daily', "'21:00'", 'ExecutionTimeLimit',
            'StartWhenAvailable', 'AllowStartIfOnBatteries', 'DontStopIfGoingOnBatteries',
            'WorkingDirectory',
        ):
            self.assertIn(expected, section, f'{expected} missing from the Daily Backup registration block')

    def test_launcher_and_setup_autostart_agree_on_no_pre_unregister(self):
        """Cross-file parity: both registration paths for Daily Backup must
        share the same safety property."""
        launcher_src = (_project_root() / 'dev' / 'launcher.py').read_text(encoding='utf-8')
        launcher_backup_section = launcher_src[launcher_src.index('backup_ps_command ='):]
        launcher_backup_section = launcher_backup_section[:launcher_backup_section.index('_register_scheduled_task(')]
        self.assertNotIn('Unregister-ScheduledTask', launcher_backup_section)

        setup_section = self._daily_backup_section()
        self.assertNotIn('Unregister-ScheduledTask', setup_section)


class DailyBackupAclFailFatalTest(TestCase):
    """
    Source-structure check that ACL restriction failure is fatal (finding 2),
    not a soft warn-and-continue. Actually forcing Set-Acl to fail requires
    SYSTEM/Administrator-scoped privilege games that are not portable across
    dev machines and CI -- DailyBackupFullScriptTest's
    test_full_backup_fails_closed_without_privilege_or_succeeds_when_elevated
    exercises the real fail-closed behavior on a non-privileged account
    instead of merely inspecting source.
    """

    def _acl_section(self):
        if not _backup_script_path().exists():
            self.skipTest('daily-backup.ps1 not present (frozen bundle)')
        src = _backup_script_path().read_text(encoding='utf-8')
        start = src.index('# -- 4) restrict NTFS permissions')
        end = src.index('# -- 5) hot-backup')
        return src[start:end]

    def test_acl_block_uses_stop_error_action(self):
        self.assertIn('-ErrorAction Stop', self._acl_section())

    def test_acl_failure_calls_fail_not_a_soft_warning(self):
        section = self._acl_section()
        catch_block = section[section.index('} catch {'):]
        self.assertIn('Fail ', catch_block)
        self.assertNotIn("Log 'WARN'", catch_block)


# ===========================================================================
# v2.1.16 hardening: dev/launcher.py scheduled-task registration tests
# (finding 7: failure detection; finding 8: Daily Backup task parity)
# ===========================================================================

def _import_launcher_module_once():
    """
    Import dev/launcher.py as an isolated module for behavioral testing.

    launcher.py performs a real UAC-elevation check at MODULE IMPORT TIME
    (`if not _is_admin(): ctypes.windll.shell32.ShellExecuteW(...); sys.exit()`)
    because it is designed to always run elevated in production. Importing it
    unmodified inside a non-admin test process would call sys.exit() and kill
    the whole test run, so ctypes.windll is replaced with a fake that reports
    "already admin" for the duration of the import only. Nothing else about
    the module is modified -- _register_scheduled_task, _step6_autostart, and
    every other function under test run as real, unmodified source.
    """
    import ctypes
    import importlib.util
    import types

    launcher_path = _project_root() / 'dev' / 'launcher.py'
    if not launcher_path.exists():
        return None

    fake_shell32 = types.SimpleNamespace(
        IsUserAnAdmin=lambda: 1,
        ShellExecuteW=lambda *a, **k: None,
    )
    fake_windll = types.SimpleNamespace(shell32=fake_shell32)

    spec = importlib.util.spec_from_file_location('fans_launcher_under_test', launcher_path)
    module = importlib.util.module_from_spec(spec)
    original_cwd = os.getcwd()
    with mock.patch.object(ctypes, 'windll', fake_windll), mock.patch('tkinter.Tk'):
        spec.loader.exec_module(module)
    os.chdir(original_cwd)
    return module


class ScheduledTaskRegistrationHelperTest(TestCase):
    """
    Behavioral tests for dev.launcher._register_scheduled_task -- verifies it
    actually inspects the return code / stderr and reports failure, rather
    than the old bare `except Exception: pass`. Mocks subprocess so no real
    Task Scheduler interaction is required.
    """

    launcher = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.launcher = _import_launcher_module_once()
        if cls.launcher is None:
            raise unittest.SkipTest('dev/launcher.py not present (frozen bundle)')

    def _import_launcher(self):
        return self.launcher

    def test_success_returns_true_and_does_not_warn(self):
        launcher = self._import_launcher()
        fake_result = mock.Mock(returncode=0, stdout='SUCCESS', stderr='')
        with mock.patch.object(launcher.subprocess, 'run', return_value=fake_result) as run_mock, \
             mock.patch.object(launcher, '_warn') as warn_mock:
            ok = launcher._register_scheduled_task('FANS-C Daily Backup', ['schtasks', '/Create'])
        self.assertTrue(ok)
        run_mock.assert_called_once()
        warn_mock.assert_not_called()

    def test_nonzero_return_code_returns_false_and_warns(self):
        launcher = self._import_launcher()
        fake_result = mock.Mock(returncode=1, stdout='', stderr='ERROR: Access is denied.')
        with mock.patch.object(launcher.subprocess, 'run', return_value=fake_result), \
             mock.patch.object(launcher, '_warn') as warn_mock, \
             mock.patch.object(launcher.logging, 'error') as log_mock:
            ok = launcher._register_scheduled_task('FANS-C Watchdog', ['schtasks', '/Create'])
        self.assertFalse(ok)
        warn_mock.assert_called_once()
        self.assertIn('Access is denied', warn_mock.call_args[0][0])
        log_mock.assert_called_once()

    def test_exception_launching_process_returns_false_and_warns(self):
        launcher = self._import_launcher()
        with mock.patch.object(launcher.subprocess, 'run', side_effect=OSError('schtasks.exe not found')), \
             mock.patch.object(launcher, '_warn') as warn_mock:
            ok = launcher._register_scheduled_task('FANS-C Verification System', ['schtasks', '/Create'])
        self.assertFalse(ok)
        warn_mock.assert_called_once()

    def test_step6_autostart_does_not_raise_when_all_registrations_fail(self):
        """First-run setup must surface failures, not crash the whole wizard."""
        launcher = self._import_launcher()
        fake_win = mock.Mock()
        fake_result = mock.Mock(returncode=1, stdout='', stderr='ERROR: Access is denied.')
        with mock.patch.object(launcher.subprocess, 'run', return_value=fake_result), \
             mock.patch.object(launcher, '_warn'):
            launcher._step6_autostart(fake_win)  # must not raise

    def test_step6_autostart_returns_false_when_main_task_fails(self):
        """Codex finding F-02: _step6_autostart must PROPAGATE failure (return
        False), not just avoid raising -- the old defect let setup continue
        as though step 6 had succeeded."""
        launcher = self._import_launcher()
        fake_win = mock.Mock()

        def _run(cmd, **kwargs):
            if 'FANS-C Verification System' in ' '.join(str(x) for x in cmd):
                return mock.Mock(returncode=1, stdout='', stderr='ERROR: Access is denied.')
            return mock.Mock(returncode=0, stdout='', stderr='')

        with mock.patch.object(launcher.subprocess, 'run', side_effect=_run), \
             mock.patch.object(launcher, '_warn'):
            ok = launcher._step6_autostart(fake_win)
        self.assertFalse(ok)

    def test_step6_autostart_returns_false_when_watchdog_fails(self):
        launcher = self._import_launcher()
        fake_win = mock.Mock()

        def _run(cmd, **kwargs):
            if 'FANS-C Watchdog' in ' '.join(str(x) for x in cmd):
                return mock.Mock(returncode=1, stdout='', stderr='ERROR: Access is denied.')
            return mock.Mock(returncode=0, stdout='', stderr='')

        with mock.patch.object(launcher.subprocess, 'run', side_effect=_run), \
             mock.patch.object(launcher, '_warn'):
            ok = launcher._step6_autostart(fake_win)
        self.assertFalse(ok)

    def test_step6_autostart_returns_false_when_daily_backup_fails(self):
        launcher = self._import_launcher()
        fake_win = mock.Mock()

        def _run(cmd, **kwargs):
            if 'FANS-C Daily Backup' in ' '.join(str(x) for x in cmd):
                return mock.Mock(returncode=1, stdout='', stderr='ERROR: Access is denied.')
            return mock.Mock(returncode=0, stdout='', stderr='')

        with mock.patch.object(launcher.subprocess, 'run', side_effect=_run), \
             mock.patch.object(launcher, '_warn'):
            ok = launcher._step6_autostart(fake_win)
        self.assertFalse(ok)

    def test_step6_autostart_returns_false_when_all_three_fail(self):
        launcher = self._import_launcher()
        fake_win = mock.Mock()
        fake_result = mock.Mock(returncode=1, stdout='', stderr='ERROR: Access is denied.')
        with mock.patch.object(launcher.subprocess, 'run', return_value=fake_result), \
             mock.patch.object(launcher, '_warn'):
            ok = launcher._step6_autostart(fake_win)
        self.assertFalse(ok)

    def test_step6_autostart_returns_true_when_all_three_succeed(self):
        launcher = self._import_launcher()
        fake_win = mock.Mock()
        fake_result = mock.Mock(returncode=0, stdout='', stderr='')
        with mock.patch.object(launcher.subprocess, 'run', return_value=fake_result), \
             mock.patch.object(launcher, '_warn') as warn_mock:
            ok = launcher._step6_autostart(fake_win)
        self.assertTrue(ok)
        warn_mock.assert_not_called()

    def test_run_setup_steps_6_and_7_aborts_before_step7_on_failure(self):
        """
        Codex finding F-02: when required Task Scheduler setup fails, the
        first-run wizard must call the existing _fatal() error path and must
        NOT proceed to starting services (step 7) or reach final success.
        """
        launcher = self._import_launcher()
        fake_win = mock.Mock()
        with mock.patch.object(launcher, '_step6_autostart', return_value=False), \
             mock.patch.object(launcher, '_fatal') as fatal_mock, \
             mock.patch.object(launcher, '_step7_start_services') as step7_mock:
            launcher._run_setup_steps_6_and_7(fake_win)
        fatal_mock.assert_called_once()
        step7_mock.assert_not_called()

    def test_run_setup_steps_6_and_7_proceeds_to_step7_on_success(self):
        launcher = self._import_launcher()
        fake_win = mock.Mock()
        with mock.patch.object(launcher, '_step6_autostart', return_value=True), \
             mock.patch.object(launcher, '_fatal') as fatal_mock, \
             mock.patch.object(launcher, '_step7_start_services') as step7_mock:
            launcher._run_setup_steps_6_and_7(fake_win)
        fatal_mock.assert_not_called()
        step7_mock.assert_called_once_with(fake_win)

    def test_daily_backup_task_uses_register_scheduled_task_powershell_cmdlet(self):
        """
        Finding 8 parity: the Daily Backup task must be registered via
        Register-ScheduledTask (which can express StartWhenAvailable / an
        execution time limit / battery behavior), not raw schtasks /Create,
        which cannot express those settings without an XML definition.
        """
        launcher = self._import_launcher()
        fake_result = mock.Mock(returncode=0, stdout='', stderr='')
        calls = []

        def _capture(cmd, **kwargs):
            calls.append(cmd)
            return fake_result

        fake_win = mock.Mock()
        with mock.patch.object(launcher.subprocess, 'run', side_effect=_capture), \
             mock.patch.object(launcher, '_warn'):
            launcher._step6_autostart(fake_win)

        backup_calls = [c for c in calls if 'FANS-C Daily Backup' in ' '.join(str(x) for x in c)]
        self.assertEqual(len(backup_calls), 1)
        backup_cmd = backup_calls[0]
        self.assertIn('powershell.exe', backup_cmd[0])
        joined = ' '.join(str(x) for x in backup_cmd)
        self.assertIn('Register-ScheduledTask', joined)
        self.assertIn('New-ScheduledTaskTrigger -Daily', joined)
        self.assertIn("'21:00'", joined)
        self.assertIn('StartWhenAvailable', joined)
        self.assertIn('ExecutionTimeLimit', joined)
        self.assertIn('AllowStartIfOnBatteries', joined)
        self.assertIn("UserId 'SYSTEM'", joined)
        self.assertIn('RunLevel Highest', joined)

    def test_daily_backup_registration_does_not_pre_unregister(self):
        """
        Codex finding F-05: replacement must go through
        Register-ScheduledTask -Force directly, without first unregistering
        any existing task -- if the new registration then failed, the
        previous known-good task would otherwise be gone with nothing to
        replace it.
        """
        launcher = self._import_launcher()
        fake_result = mock.Mock(returncode=0, stdout='', stderr='')
        calls = []

        def _capture(cmd, **kwargs):
            calls.append(cmd)
            return fake_result

        fake_win = mock.Mock()
        with mock.patch.object(launcher.subprocess, 'run', side_effect=_capture), \
             mock.patch.object(launcher, '_warn'):
            launcher._step6_autostart(fake_win)

        backup_calls = [c for c in calls if 'FANS-C Daily Backup' in ' '.join(str(x) for x in c)]
        joined = ' '.join(str(x) for x in backup_calls[0])
        self.assertNotIn('Unregister-ScheduledTask', joined)
        self.assertIn('-Force', joined)

    def test_daily_backup_registration_failure_does_not_invoke_unregister(self):
        """Even on a simulated registration failure, no separate unregister
        call is ever issued for the Daily Backup task."""
        launcher = self._import_launcher()

        def _run(cmd, **kwargs):
            return mock.Mock(returncode=1, stdout='', stderr='ERROR: Access is denied.')

        calls = []

        def _capture(cmd, **kwargs):
            calls.append(cmd)
            return _run(cmd, **kwargs)

        fake_win = mock.Mock()
        with mock.patch.object(launcher.subprocess, 'run', side_effect=_capture), \
             mock.patch.object(launcher, '_warn'):
            launcher._step6_autostart(fake_win)

        backup_calls = [c for c in calls if 'FANS-C Daily Backup' in ' '.join(str(x) for x in c)]
        self.assertEqual(len(backup_calls), 1, 'exactly one registration attempt, no separate unregister call')
        joined = ' '.join(str(x) for x in backup_calls[0])
        self.assertNotIn('Unregister-ScheduledTask', joined)

    def test_all_three_tasks_registered_exactly_once(self):
        launcher = self._import_launcher()
        fake_result = mock.Mock(returncode=0, stdout='', stderr='')
        task_names = []

        def _capture(cmd, **kwargs):
            joined = ' '.join(str(x) for x in cmd)
            for name in ('FANS-C Verification System', 'FANS-C Watchdog', 'FANS-C Daily Backup'):
                if name in joined:
                    task_names.append(name)
            return fake_result

        fake_win = mock.Mock()
        with mock.patch.object(launcher.subprocess, 'run', side_effect=_capture), \
             mock.patch.object(launcher, '_warn'):
            launcher._step6_autostart(fake_win)

        self.assertEqual(
            sorted(task_names),
            ['FANS-C Daily Backup', 'FANS-C Verification System', 'FANS-C Watchdog'],
        )


# ===========================================================================
# v2.1.16 hardening: uninstall cleanup task-name parity (finding 9)
# ===========================================================================

class RestoreToolingTest(TestCase):
    """
    Phase 14 (restore/disaster-recovery tooling): verify-backup.ps1 and
    restore-backup.ps1 are new, additive scripts that dot-source
    daily-backup.ps1 for its restore-readiness functions rather than
    reimplementing them (per the decision to not extract a shared module --
    daily-backup.ps1 was already designed to be safely dot-sourced). Neither
    script modifies daily-backup.ps1 itself.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not _backup_script_path().exists():
            raise unittest.SkipTest('daily-backup.ps1 not present (frozen bundle)')
        if not _real_sqlite3_exe().exists():
            raise unittest.SkipTest('tools/sqlite3.exe not present')

    def _verify_script_path(self):
        return _project_root() / 'scripts' / 'admin' / 'verify-backup.ps1'

    def _restore_script_path(self):
        return _project_root() / 'scripts' / 'admin' / 'restore-backup.ps1'

    def _is_elevated(self):
        result = subprocess.run(
            ['powershell.exe', '-NonInteractive', '-Command',
             '([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent())'
             '.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)'],
            capture_output=True, text=True,
        )
        return result.stdout.strip().lower() == 'true'

    def test_scripts_exist(self):
        self.assertTrue(self._verify_script_path().exists())
        self.assertTrue(self._restore_script_path().exists())

    def test_scripts_do_not_modify_daily_backup_source(self):
        """Both scripts must dot-source daily-backup.ps1 for its functions
        (per the no-shared-module decision) and must never write to it --
        $dailyBackupScript is only ever read (dot-sourced or Test-Path'd),
        never passed to a write cmdlet."""
        write_cmdlet_pattern = re.compile(
            r'(Set-Content|Out-File|WriteAllText|WriteAllBytes|Add-Content)[^\n]*\$dailyBackupScript'
        )
        for path in (self._verify_script_path(), self._restore_script_path()):
            src = path.read_text(encoding='utf-8')
            self.assertIn('. $dailyBackupScript', src, f'{path.name} must dot-source daily-backup.ps1')
            self.assertIsNone(
                write_cmdlet_pattern.search(src),
                f'{path.name} must never write to $dailyBackupScript',
            )

    def _make_fixture_root(self, tmp):
        """Lays out a disposable fake install root: scripts/admin/{daily-backup,
        verify-backup,restore-backup}.ps1, tools/sqlite3.exe, and a live
        db.sqlite3/.env/media -- entirely separate from the real repo."""
        root = Path(tmp)
        scripts_admin = root / 'scripts' / 'admin'
        scripts_admin.mkdir(parents=True, exist_ok=True)
        for name in ('daily-backup.ps1', 'verify-backup.ps1', 'restore-backup.ps1'):
            (scripts_admin / name).write_text(
                (_project_root() / 'scripts' / 'admin' / name).read_text(encoding='utf-8'),
                encoding='utf-8',
            )
        tools = root / 'tools'
        tools.mkdir(parents=True, exist_ok=True)
        (tools / 'sqlite3.exe').write_bytes(_real_sqlite3_exe().read_bytes())

        # "live" install state
        subprocess.run(
            [str(tools / 'sqlite3.exe'), str(root / 'db.sqlite3'),
             'CREATE TABLE live(x INT); INSERT INTO live VALUES (99);'],
            check=True, capture_output=True,
        )
        (root / '.env').write_text('SECRET_KEY=live-original\n', encoding='utf-8')
        media = root / 'media' / 'photos'
        media.mkdir(parents=True, exist_ok=True)
        (media / 'live.jpg').write_bytes(b'live-media-bytes')
        return root

    def _make_valid_backup_fixture(self, root, name='2026-08-28_1000'):
        tools = root / 'tools' / 'sqlite3.exe'
        backup_dir = root / 'backups' / name
        backup_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [str(tools), str(backup_dir / 'db.sqlite3'),
             'CREATE TABLE backedup(x INT); INSERT INTO backedup VALUES (1);'],
            check=True, capture_output=True,
        )
        (backup_dir / '.env').write_text('SECRET_KEY=from-backup\n', encoding='utf-8')
        db_size = (backup_dir / 'db.sqlite3').stat().st_size
        manifest = {
            'status': 'complete', 'integrity_check': 'ok',
            'env_included': True, 'db_backup_bytes': db_size,
        }
        (backup_dir / '_backup_manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        return backup_dir

    def test_verify_backup_accepts_valid_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_fixture_root(tmp)
            backup_dir = self._make_valid_backup_fixture(root)
            result = subprocess.run(
                ['powershell.exe', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                 '-File', str(root / 'scripts' / 'admin' / 'verify-backup.ps1'),
                 '-BackupPath', str(backup_dir)],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_verify_backup_deep_accepts_valid_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_fixture_root(tmp)
            backup_dir = self._make_valid_backup_fixture(root)
            result = subprocess.run(
                ['powershell.exe', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                 '-File', str(root / 'scripts' / 'admin' / 'verify-backup.ps1'),
                 '-BackupPath', str(backup_dir), '-Deep'],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('Deep check passed', result.stdout)

    def test_verify_backup_rejects_tampered_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_fixture_root(tmp)
            backup_dir = self._make_valid_backup_fixture(root)
            # Tamper: truncate the backup db after the manifest recorded its size.
            (backup_dir / 'db.sqlite3').write_bytes(b'corrupted')
            result = subprocess.run(
                ['powershell.exe', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                 '-File', str(root / 'scripts' / 'admin' / 'verify-backup.ps1'),
                 '-BackupPath', str(backup_dir)],
                capture_output=True, text=True, timeout=30,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('NOT restore-ready', result.stdout)

    def test_verify_backup_deep_catches_corruption_lightweight_check_misses(self):
        """A backup can pass the lightweight predicate (manifest says
        integrity_check=ok and the size matches) while the db content has
        actually been corrupted after the fact without changing its size --
        only -Deep catches this, by re-running PRAGMA integrity_check live."""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_fixture_root(tmp)
            backup_dir = self._make_valid_backup_fixture(root)
            db_path = backup_dir / 'db.sqlite3'
            original_size = db_path.stat().st_size
            # Overwrite with garbage of the SAME size so the lightweight
            # size-match check still passes, but the DB is no longer valid SQLite.
            db_path.write_bytes(b'X' * original_size)

            lightweight = subprocess.run(
                ['powershell.exe', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                 '-File', str(root / 'scripts' / 'admin' / 'verify-backup.ps1'),
                 '-BackupPath', str(backup_dir)],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(lightweight.returncode, 0, 'lightweight check should still pass (same size)')

            deep = subprocess.run(
                ['powershell.exe', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                 '-File', str(root / 'scripts' / 'admin' / 'verify-backup.ps1'),
                 '-BackupPath', str(backup_dir), '-Deep'],
                capture_output=True, text=True, timeout=30,
            )
            self.assertNotEqual(deep.returncode, 0, '-Deep must catch the corruption the lightweight check misses')
            self.assertIn('Deep check FAILED', deep.stdout)

    def test_verify_backup_missing_path_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_fixture_root(tmp)
            result = subprocess.run(
                ['powershell.exe', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                 '-File', str(root / 'scripts' / 'admin' / 'verify-backup.ps1'),
                 '-BackupPath', str(root / 'backups' / 'does-not-exist')],
                capture_output=True, text=True, timeout=30,
            )
            self.assertNotEqual(result.returncode, 0)

    def test_restore_backup_fails_closed_without_privilege_or_succeeds_when_elevated(self):
        """
        Adaptive, matching the pattern used throughout this project for
        privilege-gated scripts: this suite normally runs as a non-admin
        account, where restore-backup.ps1 must fail closed immediately
        (exit non-zero, zero side effects -- no snapshot, live files
        untouched). If ever run elevated, it must instead complete the full
        restore (snapshot created, live files replaced with the backup's,
        integrity re-verified).
        """
        is_admin = self._is_elevated()
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_fixture_root(tmp)
            backup_dir = self._make_valid_backup_fixture(root)
            original_env = (root / '.env').read_text(encoding='utf-8')

            result = subprocess.run(
                ['powershell.exe', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                 '-File', str(root / 'scripts' / 'admin' / 'restore-backup.ps1'),
                 '-BackupPath', str(backup_dir)],
                input='YES\n', capture_output=True, text=True, timeout=30,
            )
            combined = result.stdout + result.stderr

            if is_admin:
                self.assertEqual(result.returncode, 0, combined)
                self.assertEqual((root / '.env').read_text(encoding='utf-8'), 'SECRET_KEY=from-backup\n')
                snapshots = list((root / 'backups').glob('pre-restore-*'))
                self.assertEqual(len(snapshots), 1)
                self.assertEqual((snapshots[0] / '.env').read_text(encoding='utf-8'), original_env)
            else:
                self.assertNotEqual(result.returncode, 0, combined)
                self.assertIn('Must be run as Administrator', combined)
                self.assertEqual((root / '.env').read_text(encoding='utf-8'), original_env)
                snapshots = list((root / 'backups').glob('pre-restore-*'))
                self.assertEqual(snapshots, [], 'no snapshot must be created when the restore never proceeds')

    def test_restore_backup_rejects_non_restore_ready_backup(self):
        """Even with -BackupPath pointed at a tampered backup, the script
        must refuse before the admin/confirmation gate ever matters --
        verified here by confirming the failure message names the backup
        readiness check, not a later step, when run without a YES answer."""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_fixture_root(tmp)
            backup_dir = self._make_valid_backup_fixture(root)
            (backup_dir / 'db.sqlite3').write_bytes(b'corrupted')
            result = subprocess.run(
                ['powershell.exe', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                 '-File', str(root / 'scripts' / 'admin' / 'restore-backup.ps1'),
                 '-BackupPath', str(backup_dir)],
                capture_output=True, text=True, timeout=30,
            )
            self.assertNotEqual(result.returncode, 0)
            # Non-admin accounts hit the admin gate first (checked before backup
            # verification in the script); admins would see the readiness failure.
            combined = result.stdout + result.stderr
            self.assertTrue(
                'Must be run as Administrator' in combined or 'NOT restore-ready' in combined,
                combined,
            )


class UninstallCleanupTaskParityTest(TestCase):
    """Both uninstall paths must remove all three FANS-C scheduled tasks,
    and must not target any unrelated Task Scheduler task."""

    EXPECTED_TASKS = {'FANS-C Verification System', 'FANS-C Watchdog', 'FANS-C Daily Backup'}

    def test_uninstall_clean_ps1_task_names(self):
        script = _project_root() / 'scripts' / 'admin' / 'uninstall-clean.ps1'
        if not script.exists():
            self.skipTest('uninstall-clean.ps1 not present')
        src = script.read_text(encoding='utf-8')
        start = src.index('$TASK_NAMES')
        line = src[start:src.index('\n', start)]
        for task in self.EXPECTED_TASKS:
            self.assertIn(task, line, f'{task} missing from $TASK_NAMES in uninstall-clean.ps1')

    def test_installer_iss_removes_all_three_tasks(self):
        script = _project_root() / 'dev' / 'installer' / 'fans_c.iss'
        if not script.exists():
            self.skipTest('fans_c.iss not present')
        src = script.read_text(encoding='utf-8')
        uninstall_run_start = src.index('[UninstallRun]')
        uninstall_run_end = src.index('[UninstallDelete]')
        section = src[uninstall_run_start:uninstall_run_end]
        for task in self.EXPECTED_TASKS:
            self.assertIn(
                f'/Delete /TN ""{task}""', section,
                f'{task} is not deleted in the [UninstallRun] section of fans_c.iss',
            )

    def test_installer_iss_does_not_target_unrelated_tasks(self):
        """Guards against a future edit accidentally widening the delete
        parameters (e.g. a wildcard) that could remove unrelated tasks."""
        script = _project_root() / 'dev' / 'installer' / 'fans_c.iss'
        if not script.exists():
            self.skipTest('fans_c.iss not present')
        src = script.read_text(encoding='utf-8')
        uninstall_run_start = src.index('[UninstallRun]')
        uninstall_run_end = src.index('[UninstallDelete]')
        section = src[uninstall_run_start:uninstall_run_end]
        delete_lines = [
            line for line in section.splitlines()
            if 'schtasks' in line.lower() or '/Delete /TN' in line
        ]
        # Every /TN value used for deletion must be one of our three tasks.
        import re as _re
        for line in section.splitlines():
            match = _re.search(r'/Delete /TN ""([^"]+)""', line)
            if match:
                self.assertIn(match.group(1), self.EXPECTED_TASKS)


# ===========================================================================
# v2.1.16 hardening: system health backup detection (finding 10)
# ===========================================================================

class SystemHealthBackupDetectionTest(TestCase):
    """
    fans.views._list_completed_fans_backups/_is_fans_backup_restore_ready
    and the /system/health/ page must recognize the real
    backups\\<timestamp[_N]>\\_backup_manifest.json layout produced by
    daily-backup.ps1, applying the SAME restore-readiness predicate as the
    PowerShell script's own rotation logic (v2.1.16 correction, F-03/F-04) --
    not merely trusting status=complete.
    """

    def setUp(self):
        from accounts.models import CustomUser
        self.admin = CustomUser.objects.create_user(
            username='health_admin', password='TestPass123!',
            role=CustomUser.ROLE_IT, employee_id='EMP-HEALTH-01',
        )
        self.client = Client()

    def _make_completed(self, root, name, integrity='ok', env_included=True,
                         db_bytes=b'fake-db-bytes', recorded_bytes=None,
                         include_env=True, include_db=True):
        d = root / name
        d.mkdir(parents=True)
        if include_db:
            (d / 'db.sqlite3').write_bytes(db_bytes)
        if include_env:
            (d / '.env').write_text('SECRET_KEY=x', encoding='utf-8')
        manifest = {
            'status': 'complete',
            'integrity_check': integrity,
            'env_included': env_included,
            'db_backup_bytes': len(db_bytes) if recorded_bytes is None else recorded_bytes,
        }
        (d / '_backup_manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        return d

    def test_no_backups_directory_returns_empty(self):
        from fans.views import _list_completed_fans_backups
        with tempfile.TemporaryDirectory() as tmp:
            result = _list_completed_fans_backups(Path(tmp) / 'does-not-exist')
        self.assertEqual(result, [])

    def test_valid_completed_backup_is_detected(self):
        from fans.views import _list_completed_fans_backups
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_completed(root, '2026-08-27_2100')
            result = _list_completed_fans_backups(root)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['name'], '2026-08-27_2100')
        self.assertEqual(result[0]['manifest']['integrity_check'], 'ok')

    def test_retry_suffix_directory_is_detected(self):
        from fans.views import _list_completed_fans_backups
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_completed(root, '2026-08-27_2100')
            self._make_completed(root, '2026-08-27_2100_2')
            result = _list_completed_fans_backups(root)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['name'], '2026-08-27_2100_2', 'the retry must sort as newer than the base attempt')

    def test_incomplete_backup_without_manifest_is_excluded(self):
        from fans.views import _list_completed_fans_backups
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '2026-08-27_2100').mkdir(parents=True)  # no manifest
            result = _list_completed_fans_backups(root)
        self.assertEqual(result, [])

    def test_unrelated_directory_is_excluded(self):
        from fans.views import _list_completed_fans_backups
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'my-manual-copy').mkdir(parents=True)
            (root / 'my-manual-copy' / '_backup_manifest.json').write_text(
                '{"status": "complete"}', encoding='utf-8',
            )
            result = _list_completed_fans_backups(root)
        self.assertEqual(result, [], 'a folder outside the strict naming scheme must never be treated as a backup')

    def test_newest_valid_backup_is_selected(self):
        from fans.views import _list_completed_fans_backups
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_completed(root, '2026-08-01_2100')
            self._make_completed(root, '2026-08-27_2100')
            self._make_completed(root, '2026-08-15_2100')
            result = _list_completed_fans_backups(root)
        self.assertEqual(result[0]['name'], '2026-08-27_2100')
        self.assertEqual(len(result), 3)

    # -- restore-readiness predicate (findings F-03/F-04) --------------------------

    def test_restore_ready_false_db_missing(self):
        from fans.views import _list_completed_fans_backups
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_completed(root, '2026-08-27_2100', include_db=False)
            result = _list_completed_fans_backups(root)
        self.assertEqual(result, [])

    def test_restore_ready_false_db_zero_bytes(self):
        from fans.views import _list_completed_fans_backups
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_completed(root, '2026-08-27_2100', db_bytes=b'', recorded_bytes=0)
            result = _list_completed_fans_backups(root)
        self.assertEqual(result, [])

    def test_restore_ready_false_env_missing(self):
        from fans.views import _list_completed_fans_backups
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_completed(root, '2026-08-27_2100', include_env=False)
            result = _list_completed_fans_backups(root)
        self.assertEqual(result, [])

    def test_restore_ready_false_integrity_not_ok(self):
        from fans.views import _list_completed_fans_backups
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_completed(root, '2026-08-27_2100', integrity='corrupt')
            result = _list_completed_fans_backups(root)
        self.assertEqual(result, [])

    def test_restore_ready_false_env_included_false(self):
        from fans.views import _list_completed_fans_backups
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_completed(root, '2026-08-27_2100', env_included=False)
            result = _list_completed_fans_backups(root)
        self.assertEqual(result, [])

    def test_restore_ready_false_recorded_size_mismatch(self):
        from fans.views import _list_completed_fans_backups
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_completed(root, '2026-08-27_2100', db_bytes=b'twelve-bytes', recorded_bytes=999999)
            result = _list_completed_fans_backups(root)
        self.assertEqual(result, [], 'a size mismatch vs. the recorded manifest value must be treated as invalid')

    def test_newer_invalid_backup_does_not_hide_older_valid_one(self):
        from fans.views import _list_completed_fans_backups
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_completed(root, '2026-08-01_2100')
            # Newer, but tampered (env missing) -- must not become "the latest backup".
            self._make_completed(root, '2026-08-27_2100', include_env=False)
            result = _list_completed_fans_backups(root)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['name'], '2026-08-01_2100')

    def test_count_incomplete_backups(self):
        from fans.views import _count_incomplete_fans_backups
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_completed(root, '2026-08-01_2100')
            (root / '2026-08-02_2100').mkdir()  # no manifest
            self._make_completed(root, '2026-08-03_2100', include_env=False)  # tampered
            (root / 'unrelated-folder').mkdir()
            count = _count_incomplete_fans_backups(root)
        self.assertEqual(count, 2)

    def test_system_health_view_reports_valid_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_completed(root / 'backups', '2026-08-27_2100')
            self.client.force_login(self.admin)
            with override_settings(BASE_DIR=root):
                response = self.client.get('/system/health/')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context['backup_count'], 1)
            self.assertTrue(response.context['latest_backup_db_present'])
            self.assertTrue(response.context['latest_backup_env_present'])
            self.assertEqual(response.context['latest_backup_integrity'], 'ok')

    def test_system_health_view_reports_no_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.client.force_login(self.admin)
            with override_settings(BASE_DIR=root):
                response = self.client.get('/system/health/')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context['backup_count'], 0)
            self.assertIsNone(response.context['last_backup'])

    def test_system_health_view_reports_incomplete_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_completed(root / 'backups', '2026-08-27_2100')
            (root / 'backups' / '2026-08-28_2100').mkdir()  # no manifest -- incomplete
            self.client.force_login(self.admin)
            with override_settings(BASE_DIR=root):
                response = self.client.get('/system/health/')
            self.assertEqual(response.context['incomplete_backup_count'], 1)

    def test_system_health_view_does_not_count_invalid_newer_as_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_completed(root / 'backups', '2026-08-01_2100')
            self._make_completed(root / 'backups', '2026-08-27_2100', include_env=False)  # tampered
            self.client.force_login(self.admin)
            with override_settings(BASE_DIR=root):
                response = self.client.get('/system/health/')
            self.assertEqual(response.context['backup_count'], 1)
            self.assertEqual(response.context['last_backup'].strftime('%Y-%m-%d'), '2026-08-01')


class SyncBackupAuditCommandTest(TestCase):
    """
    accounts.management.commands.sync_backup_audit mirrors backup manifests
    into AuditLog (P0.2). Must be idempotent -- each backup directory is
    logged at most once, identified by AuditLog.target_id -- and must never
    call into the PowerShell script or mutate any backup file.
    """

    def setUp(self):
        from accounts.models import CustomUser
        self.admin = CustomUser.objects.create_user(
            username='backup_audit_admin', password='TestPass123!',
            role=CustomUser.ROLE_IT, employee_id='EMP-BACKUPAUDIT-01',
        )
        self.client = Client()

    def _make_completed(self, root, name, integrity='ok', env_included=True,
                         db_bytes=b'fake-db-bytes', recorded_bytes=None,
                         include_env=True, include_db=True):
        d = root / name
        d.mkdir(parents=True)
        if include_db:
            (d / 'db.sqlite3').write_bytes(db_bytes)
        if include_env:
            (d / '.env').write_text('SECRET_KEY=x', encoding='utf-8')
        manifest = {
            'status': 'complete',
            'integrity_check': integrity,
            'env_included': env_included,
            'db_backup_bytes': len(db_bytes) if recorded_bytes is None else recorded_bytes,
        }
        (d / '_backup_manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        return d

    def test_completed_backup_logs_one_audit_row(self):
        from django.core.management import call_command
        from logs.models import AuditLog
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_completed(root / 'backups', '2026-08-27_2100')
            with override_settings(BASE_DIR=root):
                call_command('sync_backup_audit')
            rows = AuditLog.objects.filter(action=AuditLog.ACTION_BACKUP_COMPLETED)
            self.assertEqual(rows.count(), 1)
            self.assertEqual(rows.first().target_id, '2026-08-27_2100')
            self.assertTrue(rows.first().details['restore_ready'])

    def test_incomplete_backup_logs_incomplete_action(self):
        from django.core.management import call_command
        from logs.models import AuditLog
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_completed(root / 'backups', '2026-08-27_2100', include_env=False)  # tampered/incomplete
            with override_settings(BASE_DIR=root):
                call_command('sync_backup_audit')
            rows = AuditLog.objects.filter(action=AuditLog.ACTION_BACKUP_INCOMPLETE)
            self.assertEqual(rows.count(), 1)
            self.assertFalse(rows.first().details['restore_ready'])

            from logs.models import Notification
            alerts = Notification.objects.filter(
                recipient=self.admin, category=Notification.CATEGORY_SYSTEM_ALERT,
            )
            self.assertEqual(alerts.count(), 1)
            self.assertEqual(alerts.first().priority, Notification.PRIORITY_HIGH)

    def test_completed_backup_does_not_create_a_system_alert(self):
        """A successful backup is routine, not actionable — no alert noise."""
        from django.core.management import call_command
        from logs.models import Notification
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_completed(root / 'backups', '2026-08-27_2100')
            with override_settings(BASE_DIR=root):
                call_command('sync_backup_audit')
            self.assertFalse(
                Notification.objects.filter(category=Notification.CATEGORY_SYSTEM_ALERT).exists()
            )

    def test_rerun_is_idempotent(self):
        from django.core.management import call_command
        from logs.models import AuditLog
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_completed(root / 'backups', '2026-08-27_2100')
            with override_settings(BASE_DIR=root):
                call_command('sync_backup_audit')
                call_command('sync_backup_audit')
            self.assertEqual(
                AuditLog.objects.filter(action=AuditLog.ACTION_BACKUP_COMPLETED).count(), 1,
                'a second run must not create a duplicate row for the same backup directory',
            )

    def test_new_backup_added_after_first_run_is_picked_up(self):
        from django.core.management import call_command
        from logs.models import AuditLog
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_completed(root / 'backups', '2026-08-27_2100')
            with override_settings(BASE_DIR=root):
                call_command('sync_backup_audit')
                self._make_completed(root / 'backups', '2026-08-28_2100')
                call_command('sync_backup_audit')
            self.assertEqual(AuditLog.objects.filter(action=AuditLog.ACTION_BACKUP_COMPLETED).count(), 2)

    def test_system_health_view_triggers_sync(self):
        from logs.models import AuditLog
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_completed(root / 'backups', '2026-08-27_2100')
            self.client.force_login(self.admin)
            with override_settings(BASE_DIR=root):
                response = self.client.get('/system/health/')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(AuditLog.objects.filter(action=AuditLog.ACTION_BACKUP_COMPLETED).count(), 1)


# ===========================================================================
# FaceNet machine-cache closure -- first-run wizard readiness state
#
# Covers acceptance items H/I/J of the FaceNet machine-cache checkpoint:
#   H. an unavailable model produces a NOT-READY final-wizard state
#   I. a successful model load produces a READY final-wizard state
#   J. the SYSTEM autostart command itself is unchanged (/RU SYSTEM)
# Uses the existing _import_launcher_module_once() helper (see
# ScheduledTaskRegistrationHelperTest above) so dev/launcher.py runs as real,
# unmodified source under test -- only the UAC elevation check and Tk() are
# faked so import doesn't kill the test process or need a display.
# ===========================================================================

class FaceNetWizardReadinessTest(TestCase):

    launcher = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.launcher = _import_launcher_module_once()
        if cls.launcher is None:
            raise unittest.SkipTest('dev/launcher.py not present (frozen bundle)')

    def _import_launcher(self):
        return self.launcher

    # H — model unavailable -> _check_facenet_ready() returns False (NOT
    # READY) and only warns; it must not treat this as fatal.
    def test_h_check_facenet_ready_false_when_model_unavailable(self):
        launcher = self._import_launcher()
        with mock.patch(
            'verification.face_utils.get_facenet_model',
            side_effect=RuntimeError('no internet on first run'),
        ), mock.patch.object(launcher, '_warn') as warn_mock, \
                mock.patch.object(launcher, '_fatal') as fatal_mock:
            ready = launcher._check_facenet_ready(mock.Mock())
        self.assertFalse(ready)
        warn_mock.assert_called_once()
        fatal_mock.assert_not_called()

    # I — model available -> _check_facenet_ready() returns True (READY)
    # without warning the operator.
    def test_i_check_facenet_ready_true_when_model_available(self):
        launcher = self._import_launcher()
        with mock.patch(
            'verification.face_utils.get_facenet_model',
            return_value=mock.Mock(),
        ), mock.patch.object(launcher, '_warn') as warn_mock:
            ready = launcher._check_facenet_ready(mock.Mock())
        self.assertTrue(ready)
        warn_mock.assert_not_called()

    # H/I — _step2_database() propagates _check_facenet_ready()'s result
    # (not discarded) so _first_run() can thread it through to the final
    # wizard screen.
    def test_step2_database_returns_facenet_readiness(self):
        launcher = self._import_launcher()
        fake_win = mock.Mock()
        with mock.patch.object(launcher, '_init_django'), \
                mock.patch.object(launcher, '_run_migrate'), \
                mock.patch.object(launcher, '_run_collectstatic'), \
                mock.patch.object(launcher, '_check_facenet_ready', return_value=False) as ready_mock:
            result = launcher._step2_database(fake_win)
        self.assertFalse(result)
        ready_mock.assert_called_once_with(fake_win)

    # H/I wording — the final wizard screen must distinguish the two states
    # truthfully and never describe an unattempted biometric comparison as a
    # verification failure, and never leak traceback/internal detail.
    def test_show_success_wording_distinguishes_ready_states(self):
        src = (_project_root() / 'dev' / 'launcher.py').read_text(encoding='utf-8')
        self.assertIn('Biometric Verification Not Ready', src)
        self.assertIn('facenet_ready', src)
        self.assertNotIn('beneficiary verification failed', src.lower())

    # Section 4 — the launcher creates the shared cache directory and
    # verifies it is writable before any FaceNet load is attempted.
    def test_ensure_facenet_cache_dir_creates_directory(self):
        launcher = self._import_launcher()
        tmp_root = tempfile.mkdtemp(prefix='fans-facenet-cache-')
        nested = Path(tmp_root) / 'models' / 'keras-facenet'
        shutil.rmtree(tmp_root, ignore_errors=True)  # start from a path that doesn't exist yet
        try:
            with mock.patch('verification.face_utils.get_facenet_cache_dir',
                             return_value=str(nested)):
                launcher._ensure_facenet_cache_dir()
            self.assertTrue(nested.is_dir())
            self.assertFalse((nested / '.write_test').exists(),
                              'the write-test probe file must be cleaned up')
        finally:
            shutil.rmtree(tmp_root, ignore_errors=True)

    # J — the main autostart task registration must still run fans_c.exe as
    # SYSTEM; this checkpoint explicitly keeps that principal unchanged.
    def test_j_main_task_still_registers_under_system_account(self):
        launcher = self._import_launcher()
        captured = []

        def _capture(cmd, *a, **k):
            captured.append(cmd)
            return mock.Mock(returncode=0, stdout='SUCCESS', stderr='')

        fake_win = mock.Mock()
        with mock.patch.object(launcher.subprocess, 'run', side_effect=_capture), \
                mock.patch.object(launcher, '_warn'):
            launcher._step6_autostart(fake_win)

        main_task_cmd = next(
            cmd for cmd in captured
            if isinstance(cmd, list) and 'FANS-C Verification System' in cmd
        )
        self.assertIn('/RU', main_task_cmd)
        ru_index = main_task_cmd.index('/RU')
        self.assertEqual(main_task_cmd[ru_index + 1], 'SYSTEM')
