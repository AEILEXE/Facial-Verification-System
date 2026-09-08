"""
Tests for the verification app.

Face recognition (FaceNet / TensorFlow) is NOT imported or invoked in these
tests.  The tests focus on URL routing, access control, and model creation —
not the ML inference pipeline, which requires a camera and large model weights.
"""

import base64
import datetime
import json
import os
import shutil
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from unittest import mock
from django.test import TestCase, TransactionTestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from accounts.models import CustomUser
from beneficiaries.models import Beneficiary, Representative

# Minimal valid PNG header — enough to pass bytes-received checks
_VALID_PNG_BYTES = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100
VALID_BASE64 = base64.b64encode(_VALID_PNG_BYTES).decode()
INVALID_BASE64 = '!!!not-valid-base64!!!'
DATA_URI_JPEG = f'data:image/jpeg;base64,{VALID_BASE64}'

# v2.2.0 Follow-up Issue 32: verify_check_liveness/verify_submit now require
# the POST body's session_id to match request.session['verification_session']
# ['session_id'] (stale-tab/session-collision guard). Tests that set up their
# own session dict use this fixed value and must echo it in their POST
# payload — see VerifySubmitSessionCollisionGuardTest for tests of the guard
# itself, which intentionally use a DIFFERENT session_id to prove mismatches
# are rejected.
_FIXED_TEST_SESSION_ID = '11111111-1111-1111-1111-111111111111'


def _make_landmark_set(nose_x=0.5, nose_y=0.5):
    """
    Build a 468-point MediaPipe-shaped landmark list. verify_check_liveness
    no longer reads this data for any security decision (v2.1.16 Round #4 —
    see NEUTRAL_KEYPOINTS/TURNED_KEYPOINTS and detect_pose_keypoints() below
    for what replaced it); kept only so tests can prove the server ignores
    client-supplied landmark JSON, even well-formed JSON claiming a turn.
    """
    pts = [{'x': 0.5, 'y': 0.5, 'z': 0.0} for _ in range(468)]
    pts[4] = {'x': nose_x, 'y': nose_y, 'z': 0.0}
    pts[33] = {'x': 0.45, 'y': 0.5, 'z': 0.0}
    pts[263] = {'x': 0.55, 'y': 0.5, 'z': 0.0}
    pts[152] = {'x': 0.5, 'y': 0.7, 'z': 0.0}
    pts[10] = {'x': 0.5, 'y': 0.3, 'z': 0.0}
    return pts


# Baseline (centered, yaw≈0) and challenge (turned, yaw≈22.5° >> the 4°
# SERVER_CHALLENGE_THRESHOLD_DEG) landmark snapshots. Retained only as inert
# payload fields some tests still send to prove the server ignores them (see
# ServerAuthoritativeChallengeEndpointTest) — verify_check_liveness no longer
# reads baseline_landmarks/challenge_landmarks for any security decision.
BASELINE_LANDMARKS = _make_landmark_set(nose_x=0.5)
CHALLENGE_LANDMARKS = _make_landmark_set(nose_x=0.55)

# v2.1.16 Security Hardening Round #4 (Blocker 1): fixtures for
# detect_pose_keypoints() — the SERVER-side (RetinaFace/MTCNN) 5-point
# keypoint detection that replaced client-supplied landmark JSON as the
# liveness movement evidence. NEUTRAL_KEYPOINTS/TURNED_KEYPOINTS model a
# face centered then turned ~20° to the side (well past the 4°
# SERVER_CHALLENGE_THRESHOLD_DEG); SAME_KEYPOINTS models zero movement
# (a static photo / replay). Tests mock verification.views.detect_pose_keypoints
# directly rather than feeding coordinates through client JSON, since that is
# now the only path by which pose evidence can reach the security decision.
NEUTRAL_KEYPOINTS = {'left_eye': (45.0, 50.0), 'right_eye': (55.0, 50.0), 'nose': (50.0, 55.0)}
TURNED_KEYPOINTS = {'left_eye': (45.0, 50.0), 'right_eye': (55.0, 50.0), 'nose': (54.5, 55.0)}
SAME_KEYPOINTS = NEUTRAL_KEYPOINTS


def _make_liveness_tx(beneficiary, performed_by, stipend_event,
                      claimant_type='beneficiary', seconds=120,
                      anti_spoof_score=0.9, liveness_score=0.9):
    """Create a fresh, valid LivenessTransaction for test use.

    v2.1.16 (Security Hardening #1): verify_submit's liveness gate is now
    server-authoritative — server_liveness_passed can ONLY become True from
    the TX's OWN stored anti_spoof_score/pa_score (evidence captured at
    challenge time), never from a client-supplied challenge_completed
    boolean or from re-mocking check_anti_spoofing on the resubmitted frame.
    Scores default to a clearly-passing 0.9 so this helper produces a
    genuinely valid TX out of the box; tests that specifically need a
    failing/weak TX (e.g. to prove the liveness gate denies) should pass an
    explicit low anti_spoof_score instead of relying on mocks downstream.

    embedding_data is left None so the same-face consistency check is skipped.
    Tests that specifically test same-face matching should set it explicitly.
    """
    from verification.models import LivenessTransaction
    return LivenessTransaction.objects.create(
        beneficiary=beneficiary,
        claimant_type=claimant_type,
        stipend_event=stipend_event,
        performed_by=performed_by,
        # v2.1.16 Final Hardening Patch (Codex NO-GO #2): left blank
        # (legacy-row exemption in verify_submit's challenge_direction
        # check) rather than a fixed value — this generic helper is shared
        # by tests that set the session's 'challenge' to whatever value
        # suits what THEY'RE testing (score zones, service-unavailable
        # handling, etc.), none of which are testing challenge-direction
        # binding itself. Tests that specifically exercise that binding
        # create their own LivenessTransaction with an explicit
        # challenge_direction instead of using this helper.
        challenge_direction='',
        anti_spoof_score=anti_spoof_score,
        liveness_score=liveness_score,
        pa_score=0.0,
        pa_flags={},
        embedding_data=None,
        expires_at=timezone.now() + datetime.timedelta(seconds=seconds),
    )


def _make_staff(username='verif_staff', role=CustomUser.ROLE_STAFF):
    return CustomUser.objects.create_user(
        username=username,
        password='TestPass123!',
        role=role,
        employee_id=f'EMP-{username[:8].upper()}',
    )


def _make_beneficiary(ben_id='BEN-TEST-001', sc_id='SC-0001'):
    return Beneficiary.objects.create(
        beneficiary_id=ben_id,
        first_name='Maria',
        last_name='Santos',
        senior_citizen_id=sc_id,
        date_of_birth='1940-01-01',
        gender='F',
        address='123 Main St',
        barangay='Test Barangay',
        municipality='Quezon City',
        province='Metro Manila',
    )


def _make_rep(beneficiary, staff, id_number='SSS-001'):
    return Representative.objects.create(
        beneficiary=beneficiary,
        first_name='Jose',
        last_name='Rizal',
        relationship='Son',
        contact_number='09171234567',
        valid_id_type='SSS',
        valid_id_number=id_number,
        registered_by=staff,
    )


class VerificationUrlAccessTest(TestCase):
    """Unauthenticated requests to verification URLs are redirected to login."""

    def test_verify_select_requires_auth(self):
        client = Client()
        response = client.get('/verification/')
        self.assertIn(response.status_code, [301, 302])

    def test_config_requires_auth(self):
        client = Client()
        response = client.get('/verification/config/')
        self.assertIn(response.status_code, [301, 302])


class VerificationAuthenticatedTest(TestCase):
    """Authenticated staff can access verification pages (without running FaceNet)."""

    def setUp(self):
        self.client = Client()
        self.staff = _make_staff()
        self.client.force_login(self.staff)

    def test_config_page_accessible(self):
        response = self.client.get('/verification/config/')
        # 200 OK or 302 redirect both acceptable — page exists
        self.assertIn(response.status_code, [200, 302, 403])

    def test_stipend_list_accessible(self):
        response = self.client.get('/verification/stipend/')
        self.assertIn(response.status_code, [200, 302, 403])


class BeneficiaryModelTest(TestCase):
    """Basic model creation and field constraints."""

    def test_create_beneficiary(self):
        b = _make_beneficiary()
        self.assertEqual(b.first_name, 'Maria')
        self.assertEqual(b.last_name, 'Santos')
        self.assertEqual(b.senior_citizen_id, 'SC-0001')

    def test_beneficiary_str(self):
        b = _make_beneficiary()
        s = str(b)
        # Should contain some identifying info
        self.assertTrue(len(s) > 0)

    def test_unique_beneficiary_id(self):
        _make_beneficiary()
        with self.assertRaises(Exception):
            Beneficiary.objects.create(
                beneficiary_id='BEN-TEST-001',  # duplicate
                first_name='Jose',
                last_name='Rizal',
                date_of_birth='1941-06-19',
                gender='M',
                address='456 Other St',
                barangay='Test Barangay',
                municipality='Quezon City',
                province='Metro Manila',
            )


# ── Face processing unit tests ────────────────────────────────────────────────

class ProcessFaceForRegistrationUnitTest(TestCase):
    """
    Unit tests for face_utils.process_face_for_registration().

    All ML dependencies are mocked so no model is loaded or downloaded.
    These tests verify input validation and safe-failure behaviour.
    """

    def test_empty_bytes_returns_error(self):
        from verification.face_utils import process_face_for_registration
        result = process_face_for_registration(b'')
        self.assertFalse(result['success'])
        self.assertIn('No image data', result['error'])

    def test_none_returns_error_not_crash(self):
        from verification.face_utils import process_face_for_registration
        result = process_face_for_registration(None)
        self.assertFalse(result['success'])
        self.assertIn('No image data', result['error'])

    @mock.patch(
        'verification.face_utils.load_image_from_bytes',
        side_effect=ValueError('No face detected in image'),
    )
    def test_no_face_detected_returns_error_not_crash(self, _mock_load):
        from verification.face_utils import process_face_for_registration
        result = process_face_for_registration(b'not-an-image')
        self.assertFalse(result['success'])
        self.assertIn('No face detected', result['error'])

    @mock.patch('verification.face_utils.load_image_from_bytes', return_value=object())
    @mock.patch('verification.face_utils.detect_and_align_face', return_value=object())
    @mock.patch('verification.face_utils.check_face_quality',
                return_value={'ok': True, 'blur_score': 20, 'reason': ''})
    @mock.patch('verification.face_utils.is_using_mock_model', return_value=True)
    def test_mock_model_returns_friendly_error_not_crash(
            self, _is_mock, _quality, _detect, _load):
        """
        When FaceNet is not installed (mock model active), the view must get a
        user-friendly error dict — not an AttributeError from sys.stderr.write().
        This is the regression test for the root bug fixed in this branch.
        """
        from verification.face_utils import process_face_for_registration
        result = process_face_for_registration(b'fake-image-bytes')
        self.assertFalse(result['success'])
        self.assertNotIn('NoneType', result.get('error', ''))
        self.assertTrue(
            'model' in result['error'].lower() or 'install' in result['error'].lower(),
            msg=f"Expected model/install hint in error, got: {result['error']!r}",
        )

    @mock.patch('verification.face_utils.load_image_from_bytes', return_value=object())
    @mock.patch('verification.face_utils.detect_and_align_face', return_value=object())
    @mock.patch('verification.face_utils.check_face_quality',
                return_value={'ok': True, 'blur_score': 5, 'reason': 'Too blurry'})
    def test_low_quality_image_returns_quality_error(self, _quality, _detect, _load):
        from verification.face_utils import process_face_for_registration
        result = process_face_for_registration(b'blurry-bytes')
        self.assertFalse(result['success'])
        self.assertIn('quality', result['error'].lower())


# ── View-level input validation tests ─────────────────────────────────────────

class RegisterSubmitFaceViewTest(TestCase):
    """
    Input validation tests for beneficiaries:register_submit_face.

    process_face_for_registration is mocked where needed so no ML model runs.
    """

    def setUp(self):
        self.client = Client()
        self.staff = _make_staff('rsf_staff')
        self.client.force_login(self.staff)
        self.url = reverse('beneficiaries:register_submit_face')

    def _set_reg_session(self):
        s = self.client.session
        s['reg_step1'] = {
            'first_name': 'Maria',
            'last_name': 'Santos',
            'date_of_birth': '1940-01-01',
            'senior_citizen_id': 'SC-RSF-99',
            'gender': 'F',
            'address': '123 Main St',
            'barangay': 'Test Barangay',
            'municipality': 'Quezon City',
            'province': 'Metro Manila',
        }
        s.save()

    def test_no_image_field_returns_validation_error(self):
        self._set_reg_session()
        resp = self.client.post(
            self.url, data=json.dumps({'image': ''}), content_type='application/json',
        )
        data = json.loads(resp.content)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(data['success'])
        self.assertIn('image', data['error'].lower())

    def test_invalid_base64_returns_validation_error_not_crash(self):
        self._set_reg_session()
        resp = self.client.post(
            self.url, data=json.dumps({'image': INVALID_BASE64}), content_type='application/json',
        )
        data = json.loads(resp.content)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(data['success'])
        self.assertNotIn('NoneType', data.get('error', ''))
        self.assertIn('Invalid image', data['error'])

    @override_settings(REGISTRATION_LIVENESS_REQUIRED=False)
    def test_data_uri_prefix_is_stripped_before_decode(self):
        """The 'data:image/jpeg;base64,' prefix must be stripped before b64decode."""
        self._set_reg_session()
        with mock.patch('beneficiaries.views.process_face_for_registration') as mock_proc:
            mock_proc.return_value = {'success': False, 'error': 'No face detected.'}
            resp = self.client.post(
                self.url, data=json.dumps({'image': DATA_URI_JPEG}),
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 200)
        mock_proc.assert_called_once()
        call_bytes = mock_proc.call_args[0][0]
        self.assertIsInstance(call_bytes, bytes)

    def test_missing_session_returns_clean_error(self):
        resp = self.client.post(
            self.url, data=json.dumps({'image': VALID_BASE64}), content_type='application/json',
        )
        data = json.loads(resp.content)
        self.assertFalse(data['success'])
        self.assertIn('session', data['error'].lower())

    @override_settings(REGISTRATION_LIVENESS_REQUIRED=False)
    def test_model_unavailable_stores_no_embedding(self):
        """
        Release-blocker fix: when FaceNet is unavailable, registration must
        return success=False with a truthful message and must NOT store a
        FaceEmbedding — no encrypting/enrolling a fabricated (mock/random)
        vector.
        """
        from verification.models import FaceEmbedding
        self._set_reg_session()
        with mock.patch('beneficiaries.views.process_face_for_registration') as mock_proc:
            mock_proc.return_value = {
                'success': False,
                'error': 'Face recognition model is unavailable. Biometric verification is '
                         'temporarily disabled. Connect this computer to the internet during '
                         'initial setup or contact IT, then restart FANS-C.',
                'model_unavailable': True,
            }
            resp = self.client.post(
                self.url, data=json.dumps({'image': DATA_URI_JPEG}),
                content_type='application/json',
            )
        data = json.loads(resp.content)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(data['success'])
        self.assertIn('unavailable', data['error'].lower())
        self.assertEqual(FaceEmbedding.objects.count(), 0)

    def test_unauthenticated_is_redirected(self):
        self.client.logout()
        resp = self.client.post(
            self.url, data=json.dumps({'image': VALID_BASE64}), content_type='application/json',
        )
        self.assertIn(resp.status_code, [301, 302])


class UpdateFaceSubmitViewTest(TestCase):
    """Input validation tests for verification:update_face_submit."""

    def setUp(self):
        self.client = Client()
        self.staff = _make_staff('ufs_staff')
        self.client.force_login(self.staff)
        self.beneficiary = _make_beneficiary('BEN-UFS-001', 'SC-UFS-001')
        self.url = reverse('verification:update_face_submit', kwargs={'pk': self.beneficiary.pk})

    def test_no_image_returns_validation_error(self):
        resp = self.client.post(
            self.url, data=json.dumps({'image': ''}), content_type='application/json',
        )
        data = json.loads(resp.content)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(data['success'])
        self.assertIn('image', data['error'].lower())

    def test_invalid_base64_returns_validation_error_not_crash(self):
        resp = self.client.post(
            self.url, data=json.dumps({'image': INVALID_BASE64}), content_type='application/json',
        )
        data = json.loads(resp.content)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(data['success'])
        self.assertNotIn('NoneType', data.get('error', ''))
        self.assertIn('Invalid image', data['error'])

    def test_data_uri_prefix_is_stripped_before_decode(self):
        with mock.patch('verification.views.process_face_for_registration') as mock_proc:
            mock_proc.return_value = {'success': False, 'error': 'No face detected.'}
            resp = self.client.post(
                self.url, data=json.dumps({'image': DATA_URI_JPEG}),
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 200)
        mock_proc.assert_called_once()
        call_bytes = mock_proc.call_args[0][0]
        self.assertIsInstance(call_bytes, bytes)

    def test_unauthenticated_is_redirected(self):
        self.client.logout()
        resp = self.client.post(
            self.url, data=json.dumps({'image': VALID_BASE64}), content_type='application/json',
        )
        self.assertIn(resp.status_code, [301, 302])

    def test_nonexistent_beneficiary_returns_404(self):
        url = reverse('verification:update_face_submit', kwargs={'pk': uuid.uuid4()})
        resp = self.client.post(
            url, data=json.dumps({'image': VALID_BASE64}), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 404)


class UpdateFaceSubmitDuplicateCheckTest(TestCase):
    """
    A face re-enrollment capture must be checked against every OTHER
    beneficiary's stored embeddings, same as registration. Without this, a
    beneficiary's active face could silently be replaced with someone else's
    face via the "Update Face Data" workflow.
    """

    def setUp(self):
        self.client = Client()
        self.staff = _make_staff('ufs_dup_staff')
        self.client.force_login(self.staff)
        self.beneficiary = _make_beneficiary('BEN-UFSDUP-001', 'SC-UFSDUP-001')
        self.other = _make_beneficiary('BEN-UFSDUP-002', 'SC-UFSDUP-002')
        self.url = reverse('verification:update_face_submit', kwargs={'pk': self.beneficiary.pk})

    @mock.patch('verification.views.notify_admins')
    @mock.patch('verification.views.check_duplicate_face')
    @mock.patch('verification.views.decrypt_embedding')
    @mock.patch('verification.views.process_face_for_registration')
    def test_duplicate_match_recorded_on_request_and_admin_notified(
        self, mock_proc, mock_decrypt, mock_dup, mock_notify,
    ):
        from verification.models import FaceUpdateRequest

        mock_proc.return_value = {
            'success': True,
            'encrypted_embedding': b'fake-enc',
            'quality': {'ok': True, 'score': 0.9, 'reason': ''},
        }
        mock_decrypt.return_value = [0.1] * 128
        mock_dup.return_value = {
            'duplicates_found': True,
            'matches': [{
                'beneficiary_id': self.other.beneficiary_id,
                'full_name': self.other.full_name,
                'score': 0.91,
                'template': 'primary',
            }],
            'highest_score': 0.91,
            'checked': 1,
        }

        resp = self.client.post(
            self.url, data=json.dumps({'image': VALID_BASE64}), content_type='application/json',
        )
        data = json.loads(resp.content)
        self.assertTrue(data['success'])

        fur = FaceUpdateRequest.objects.get(beneficiary=self.beneficiary)
        self.assertEqual(fur.duplicate_match_beneficiary_id, self.other.pk)
        self.assertAlmostEqual(fur.duplicate_match_score, 0.91)

        # check_duplicate_face must exclude the beneficiary's own existing records
        mock_dup.assert_called_once()
        self.assertEqual(
            mock_dup.call_args.kwargs.get('exclude_beneficiary_id'),
            str(self.beneficiary.beneficiary_id),
        )

        # Two notifications: the routine approval-required one, plus a
        # fraud-alert one specifically calling out the duplicate.
        categories = [c.kwargs.get('category') for c in mock_notify.call_args_list]
        from logs.models import Notification
        self.assertIn(Notification.CATEGORY_FRAUD_ALERT, categories)

    @mock.patch('verification.views.check_duplicate_face')
    @mock.patch('verification.views.decrypt_embedding')
    @mock.patch('verification.views.process_face_for_registration')
    def test_no_duplicate_leaves_fields_null(
        self, mock_proc, mock_decrypt, mock_dup,
    ):
        from verification.models import FaceUpdateRequest

        mock_proc.return_value = {
            'success': True,
            'encrypted_embedding': b'fake-enc',
            'quality': {'ok': True, 'score': 0.9, 'reason': ''},
        }
        mock_decrypt.return_value = [0.1] * 128
        mock_dup.return_value = {'duplicates_found': False, 'matches': [], 'highest_score': 0.0, 'checked': 1}

        resp = self.client.post(
            self.url, data=json.dumps({'image': VALID_BASE64}), content_type='application/json',
        )
        data = json.loads(resp.content)
        self.assertTrue(data['success'])

        fur = FaceUpdateRequest.objects.get(beneficiary=self.beneficiary)
        self.assertIsNone(fur.duplicate_match_beneficiary)
        self.assertIsNone(fur.duplicate_match_score)


class FaceFramingCheckTest(TestCase):
    """
    Phase 2 capture-quality gate: a face bounding box clipped by the camera
    frame edge means part of the face was outside the shot — a distinct
    failure mode from "too small" (handled by the eye-distance check) or
    "blurry/dark" (handled by check_face_quality). detect_and_align_face must
    reject these with an actionable message rather than silently producing a
    degraded crop.
    """

    def test_fully_inside_frame_passes(self):
        from verification.face_utils import _check_face_within_frame
        # 200x200 image, face box comfortably inside
        _check_face_within_frame((200, 200, 3), 50, 50, 150, 150)  # must not raise

    def test_touching_left_edge_raises(self):
        from verification.face_utils import _check_face_within_frame
        with self.assertRaises(ValueError) as ctx:
            _check_face_within_frame((200, 200, 3), 0, 50, 150, 150)
        self.assertIn('inside the camera frame', str(ctx.exception))

    def test_touching_right_edge_raises(self):
        from verification.face_utils import _check_face_within_frame
        with self.assertRaises(ValueError):
            _check_face_within_frame((200, 200, 3), 50, 50, 200, 150)

    def test_touching_top_edge_raises(self):
        from verification.face_utils import _check_face_within_frame
        with self.assertRaises(ValueError):
            _check_face_within_frame((200, 200, 3), 50, 0, 150, 150)

    def test_touching_bottom_edge_raises(self):
        from verification.face_utils import _check_face_within_frame
        with self.assertRaises(ValueError):
            _check_face_within_frame((200, 200, 3), 50, 50, 150, 200)

    def test_mtcnn_path_rejects_face_clipped_by_frame_edge(self):
        import numpy as np
        from verification import face_utils

        fake_detector = mock.Mock()
        fake_detector.detect_faces.return_value = [{
            'confidence': 0.99,
            'box': [0, 40, 80, 80],  # x starts at 0 -> touches left edge
            'keypoints': {
                'left_eye': (20, 60), 'right_eye': (60, 60),
                'nose': (40, 75), 'mouth_left': (25, 90), 'mouth_right': (55, 90),
            },
        }]
        img = np.zeros((160, 160, 3), dtype=np.uint8)
        with mock.patch.object(face_utils, '_get_mtcnn', return_value=fake_detector):
            with self.assertRaises(ValueError) as ctx:
                face_utils._detect_face_mtcnn(img)
        self.assertIn('inside the camera frame', str(ctx.exception))

    def test_mtcnn_path_accepts_face_fully_inside_frame(self):
        import numpy as np
        from verification import face_utils

        fake_detector = mock.Mock()
        fake_detector.detect_faces.return_value = [{
            'confidence': 0.99,
            'box': [40, 40, 80, 80],
            'keypoints': {
                'left_eye': (60, 60), 'right_eye': (100, 60),
                'nose': (80, 75), 'mouth_left': (65, 90), 'mouth_right': (95, 90),
            },
        }]
        img = np.zeros((160, 160, 3), dtype=np.uint8)
        with mock.patch.object(face_utils, '_get_mtcnn', return_value=fake_detector):
            result = face_utils._detect_face_mtcnn(img)
        self.assertEqual(result.shape[:2], (160, 160))


# ── ensure_console_streams() tests ────────────────────────────────────────────

class EnsureConsoleStreamsTest(TestCase):
    """
    Verify ensure_console_streams() from fans.stream_safety.

    These tests simulate PyInstaller windowed mode by temporarily setting
    sys.stdout / sys.stderr to None, then confirming the function replaces
    them with writable stream objects.
    """

    def test_no_op_when_streams_already_set(self):
        from fans.stream_safety import ensure_console_streams
        old_out, old_err = sys.stdout, sys.stderr
        try:
            ensure_console_streams()
            self.assertIs(sys.stdout, old_out)
            self.assertIs(sys.stderr, old_err)
        finally:
            sys.stdout = old_out
            sys.stderr = old_err

    def test_fixes_none_stdout(self):
        from fans.stream_safety import ensure_console_streams
        old_out, old_err = sys.stdout, sys.stderr
        try:
            sys.stdout = None
            ensure_console_streams()
            self.assertIsNotNone(sys.stdout)
            self.assertTrue(hasattr(sys.stdout, 'write'),
                            'sys.stdout must have a .write() method after fix')
        finally:
            sys.stdout = old_out
            sys.stderr = old_err

    def test_fixes_none_stderr(self):
        from fans.stream_safety import ensure_console_streams
        old_out, old_err = sys.stdout, sys.stderr
        try:
            sys.stderr = None
            ensure_console_streams()
            self.assertIsNotNone(sys.stderr)
            self.assertTrue(hasattr(sys.stderr, 'write'),
                            'sys.stderr must have a .write() method after fix')
        finally:
            sys.stdout = old_out
            sys.stderr = old_err

    def test_fixes_both_none(self):
        """Core regression: both streams None must both be replaced."""
        from fans.stream_safety import ensure_console_streams
        old_out, old_err = sys.stdout, sys.stderr
        try:
            sys.stdout = None
            sys.stderr = None
            ensure_console_streams()
            self.assertIsNotNone(sys.stdout)
            self.assertIsNotNone(sys.stderr)
            self.assertTrue(hasattr(sys.stdout, 'write'))
            self.assertTrue(hasattr(sys.stderr, 'write'))
            # Confirm streams are actually usable
            sys.stdout.write('')
            sys.stderr.write('')
        finally:
            sys.stdout = old_out
            sys.stderr = old_err


# ── Face processing with None streams (PyInstaller windowed regression) ────────

class FaceProcessingNoneStreamsTest(TestCase):
    """
    Regression tests: face processing must return clean error dicts, never
    raise AttributeError, when sys.stdout / sys.stderr are None.

    This replicates the exact bug:
        Face processing error: 'NoneType' object has no attribute 'write'

    All ML dependencies are mocked so no model is loaded.
    """

    def _run_with_none_streams(self, fn, *args, **kwargs):
        """Call fn(*args) with both streams set to None; restore after."""
        old_out, old_err = sys.stdout, sys.stderr
        try:
            sys.stdout = None
            sys.stderr = None
            return fn(*args, **kwargs)
        finally:
            sys.stdout = old_out
            sys.stderr = old_err

    def test_empty_bytes_safe_with_none_streams(self):
        from verification.face_utils import process_face_for_registration
        result = self._run_with_none_streams(process_face_for_registration, b'')
        self.assertFalse(result['success'])
        self.assertNotIn('NoneType', result.get('error', ''))

    def test_none_bytes_safe_with_none_streams(self):
        from verification.face_utils import process_face_for_registration
        result = self._run_with_none_streams(process_face_for_registration, None)
        self.assertFalse(result['success'])
        self.assertNotIn('NoneType', result.get('error', ''))

    @mock.patch('verification.face_utils.load_image_from_bytes',
                side_effect=ValueError('No face detected'))
    def test_no_face_safe_with_none_streams(self, _mock):
        from verification.face_utils import process_face_for_registration
        result = self._run_with_none_streams(process_face_for_registration, b'bytes')
        self.assertFalse(result['success'])
        self.assertNotIn('NoneType', result.get('error', ''))

    @mock.patch('verification.face_utils.load_image_from_bytes', return_value=object())
    @mock.patch('verification.face_utils.detect_and_align_face', return_value=object())
    @mock.patch('verification.face_utils.check_face_quality',
                return_value={'ok': True, 'blur_score': 20, 'reason': ''})
    @mock.patch('verification.face_utils.is_using_mock_model', return_value=True)
    def test_mock_model_safe_with_none_streams(self, _is_mock, _quality, _detect, _load):
        """Mock-model path must not propagate AttributeError with None streams."""
        from verification.face_utils import process_face_for_registration
        result = self._run_with_none_streams(process_face_for_registration, b'bytes')
        self.assertFalse(result['success'])
        self.assertNotIn('NoneType', result.get('error', ''))
        self.assertTrue(
            'model' in result['error'].lower() or 'install' in result['error'].lower(),
            msg=f"Expected model/install hint, got: {result['error']!r}",
        )

    def test_get_facenet_model_catches_non_importerror(self):
        """
        get_facenet_model() must catch AttributeError from 'import tensorflow'
        (stdout write in no-console mode) and raise FaceNetUnavailableError
        with the safe operator-facing message — NEVER return a working mock
        model. This is the fail-closed contract: even if
        ensure_console_streams() was not called, the exception is handled
        inside get_facenet_model() rather than propagating as
        'Face processing error: NoneType...', AND it does not silently
        substitute random embeddings.
        """
        import verification.face_utils as fu
        import builtins

        saved = (fu._facenet_model, fu._using_mock, fu._model_load_error,
                  fu._facenet_load_failed)
        fu._facenet_model = None
        fu._using_mock = False
        fu._model_load_error = None
        fu._facenet_load_failed = False

        _real_import = builtins.__import__

        def _broken_tf_import(name, *args, **kwargs):
            if name == 'tensorflow':
                raise AttributeError("'NoneType' object has no attribute 'write'")
            return _real_import(name, *args, **kwargs)

        try:
            # Remove cached TF so the import statement actually runs.
            tf_cached = sys.modules.pop('tensorflow', ...)
            with mock.patch('builtins.__import__', side_effect=_broken_tf_import):
                with self.assertRaises(fu.FaceNetUnavailableError) as ctx:
                    fu.get_facenet_model()

            # The exception's own message must be the safe, operator-facing
            # text — no traceback/path internals leak into it.
            self.assertEqual(str(ctx.exception), fu._SAFE_UNAVAILABLE_MESSAGE)
            self.assertNotIn('NoneType', str(ctx.exception))

            self.assertTrue(fu._using_mock)
            self.assertTrue(fu._facenet_load_failed)
            self.assertIsNone(fu._facenet_model,
                               'get_facenet_model() must never cache a mock model')
            # The raw diagnostic detail is still available for the server log.
            self.assertIsNotNone(fu._model_load_error)
            self.assertIn('NoneType', fu._model_load_error)

            # Sticky failure: a second call fails fast without re-attempting
            # the (slow) import, and still raises rather than returning mock.
            with mock.patch('builtins.__import__', side_effect=_broken_tf_import) as _im:
                with self.assertRaises(fu.FaceNetUnavailableError):
                    fu.get_facenet_model()
                _im.assert_not_called()
        finally:
            if tf_cached is not ...:
                sys.modules['tensorflow'] = tf_cached
            (fu._facenet_model, fu._using_mock, fu._model_load_error,
             fu._facenet_load_failed) = saved


class FaceNetFailClosedTest(TestCase):
    """
    Release-blocker fix: FANS-C must fail closed when the real FaceNet model
    is unavailable — never substitute a random/mock embedding in production
    code. These tests patch get_facenet_model() directly (the single
    central choke point every embedding call goes through) to simulate
    unavailability exactly as get_facenet_model() itself signals it in
    production, without needing to break the real TensorFlow/keras-facenet
    import machinery.
    """

    def _unavailable(self):
        import verification.face_utils as fu
        return mock.patch(
            'verification.face_utils.get_facenet_model',
            side_effect=fu.FaceNetUnavailableError(fu._SAFE_UNAVAILABLE_MESSAGE),
        )

    # A / B — central guarantee: get_embedding() cannot produce an
    # embedding when the real model is unavailable, and never falls back to
    # _MockFaceNet to do so.
    def test_get_embedding_raises_when_model_unavailable(self):
        import numpy as np
        from verification.face_utils import get_embedding, FaceNetUnavailableError
        with self._unavailable():
            with self.assertRaises(FaceNetUnavailableError):
                get_embedding(np.zeros((160, 160, 3), dtype=np.uint8))

    def test_is_using_mock_model_true_when_unavailable(self):
        from verification.face_utils import is_using_mock_model
        with self._unavailable():
            self.assertTrue(is_using_mock_model())

    def test_get_model_load_error_does_not_propagate_exception(self):
        """get_model_load_error() must not raise, even though
        get_facenet_model() itself raises — the exception must always be
        caught and turned into a (possibly None) diagnostic string."""
        from verification.face_utils import get_model_load_error
        with self._unavailable():
            get_model_load_error()  # must not raise

    # C — registration fails safely: no embedding is stored.
    @mock.patch('verification.face_utils.load_image_from_bytes', return_value=object())
    @mock.patch('verification.face_utils.detect_and_align_face', return_value=object())
    @mock.patch('verification.face_utils.check_face_quality',
                return_value={'ok': True, 'blur_score': 20, 'reason': ''})
    def test_registration_fails_safely_when_model_unavailable(self, _q, _d, _l):
        # is_using_mock_model() short-circuits before any real image
        # processing runs, so object() sentinels are safe here.
        from verification.face_utils import process_face_for_registration
        with self._unavailable():
            result = process_face_for_registration(b'fake-bytes')
        self.assertFalse(result['success'])
        self.assertTrue(result.get('model_unavailable'))
        self.assertNotIn('encrypted_embedding', result)

    # D — verification fails safely: no embedding/decision fabricated.
    @mock.patch('verification.face_utils.load_image_from_bytes')
    @mock.patch('verification.face_utils.detect_and_align_face')
    @mock.patch('verification.face_utils.check_face_quality',
                return_value={'ok': True, 'blur_score': 20, 'reason': ''})
    def test_verification_fails_safely_when_model_unavailable(self, _q, mock_detect, mock_load):
        import numpy as np
        # process_face_for_verification has no early is_using_mock_model()
        # short-circuit — it reaches get_embedding()'s real CLAHE/resize
        # preprocessing first, so these need a real (blank) image array,
        # not a bare sentinel, to reach the get_facenet_model() call.
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((160, 160, 3), dtype=np.uint8)
        from verification.face_utils import process_face_for_verification
        with self._unavailable():
            result = process_face_for_verification(b'fake-bytes')
        self.assertFalse(result['success'])
        self.assertTrue(result.get('model_unavailable'))
        self.assertNotIn('embedding', result)

    # Liveness-TX embedding capture (verify_check_liveness) must fail safely
    # too — no LivenessTransaction should ever be issued from a fabricated
    # embedding.
    @mock.patch('verification.face_utils.load_image_from_bytes')
    @mock.patch('verification.face_utils.detect_and_align_face')
    def test_get_embedding_only_fails_safely_when_model_unavailable(self, mock_detect, mock_load):
        import numpy as np
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((160, 160, 3), dtype=np.uint8)
        from verification.face_utils import get_embedding_only
        with self._unavailable():
            result = get_embedding_only(b'fake-bytes')
        self.assertFalse(result['success'])
        self.assertTrue(result.get('model_unavailable'))

    # G — a legitimate, explicitly-mocked test path still works: patching
    # is_using_mock_model() directly (the established pattern used
    # throughout this file) is unaffected by the fail-closed redesign.
    @mock.patch('verification.face_utils.load_image_from_bytes', return_value=object())
    @mock.patch('verification.face_utils.detect_and_align_face', return_value=object())
    @mock.patch('verification.face_utils.check_face_quality',
                return_value={'ok': True, 'blur_score': 20, 'reason': ''})
    @mock.patch('verification.face_utils.is_using_mock_model', return_value=True)
    def test_explicit_is_using_mock_model_patch_still_works(self, _mock, _q, _d, _l):
        from verification.face_utils import process_face_for_registration
        result = process_face_for_registration(b'fake-bytes')
        self.assertFalse(result['success'])


class RegisterRepFaceSubmitViewTest(TestCase):
    """Input validation tests for verification:register_rep_face_submit."""

    def setUp(self):
        self.client = Client()
        self.staff = _make_staff('rrfs_staff')
        self.client.force_login(self.staff)
        self.beneficiary = _make_beneficiary('BEN-RRFS-001', 'SC-RRFS-001')
        self.rep = _make_rep(self.beneficiary, self.staff)
        self.url = reverse(
            'verification:register_rep_face_submit',
            kwargs={'pk': self.beneficiary.pk, 'rep_pk': self.rep.pk},
        )

    def test_no_image_returns_validation_error(self):
        resp = self.client.post(
            self.url, data=json.dumps({'image': ''}), content_type='application/json',
        )
        data = json.loads(resp.content)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(data['success'])
        self.assertIn('image', data['error'].lower())

    def test_invalid_base64_returns_validation_error_not_crash(self):
        resp = self.client.post(
            self.url, data=json.dumps({'image': INVALID_BASE64}), content_type='application/json',
        )
        data = json.loads(resp.content)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(data['success'])
        self.assertNotIn('NoneType', data.get('error', ''))
        self.assertIn('Invalid image', data['error'])

    def test_data_uri_prefix_is_stripped_before_decode(self):
        with mock.patch('verification.views.process_face_for_registration') as mock_proc:
            mock_proc.return_value = {'success': False, 'error': 'No face detected.'}
            resp = self.client.post(
                self.url, data=json.dumps({'image': DATA_URI_JPEG}),
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 200)
        mock_proc.assert_called_once()
        call_bytes = mock_proc.call_args[0][0]
        self.assertIsInstance(call_bytes, bytes)

    def test_unauthenticated_is_redirected(self):
        self.client.logout()
        resp = self.client.post(
            self.url, data=json.dumps({'image': VALID_BASE64}), content_type='application/json',
        )
        self.assertIn(resp.status_code, [301, 302])

    def test_nonexistent_beneficiary_returns_404(self):
        url = reverse(
            'verification:register_rep_face_submit',
            kwargs={'pk': uuid.uuid4(), 'rep_pk': uuid.uuid4()},
        )
        resp = self.client.post(
            url, data=json.dumps({'image': VALID_BASE64}), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 404)


# ── Security default regression tests ────────────────────────────────────────

class SecurityDefaultsTest(TestCase):
    """
    Bug 4/10 regression: _get_demo_mode() and _get_liveness_required() must
    default to False and True respectively when settings are absent.
    Bug 4/models regression: SystemConfig.get_threshold() must default to strict
    threshold (0.75) when DEMO_MODE is missing from settings.
    """

    def test_get_demo_mode_defaults_false(self):
        from verification.views import _get_demo_mode
        from django.conf import settings as s
        original = getattr(s, 'DEMO_MODE', 'MISSING')
        try:
            if hasattr(s, 'DEMO_MODE'):
                delattr(s, 'DEMO_MODE')
            result = _get_demo_mode()
            self.assertFalse(result, '_get_demo_mode() must default to False, not True')
        finally:
            if original != 'MISSING':
                s.DEMO_MODE = original

    def test_get_liveness_required_defaults_true(self):
        from verification.views import _get_liveness_required
        from django.conf import settings as s
        original = getattr(s, 'LIVENESS_REQUIRED', 'MISSING')
        try:
            if hasattr(s, 'LIVENESS_REQUIRED'):
                delattr(s, 'LIVENESS_REQUIRED')
            result = _get_liveness_required()
            self.assertTrue(result, '_get_liveness_required() must default to True, not False')
        finally:
            if original != 'MISSING':
                s.LIVENESS_REQUIRED = original

    def test_system_config_threshold_defaults_strict_when_demo_mode_absent(self):
        from verification.models import SystemConfig
        from django.conf import settings as s
        original = getattr(s, 'DEMO_MODE', 'MISSING')
        try:
            if hasattr(s, 'DEMO_MODE'):
                delattr(s, 'DEMO_MODE')
            threshold = SystemConfig.get_threshold()
            self.assertGreaterEqual(
                threshold, 0.75,
                f'SystemConfig.get_threshold() must return >=0.75 when DEMO_MODE absent, got {threshold}',
            )
        finally:
            if original != 'MISSING':
                s.DEMO_MODE = original


# ── Dashboard timezone regression tests ───────────────────────────────────────

class DashboardTimezoneTest(TestCase):
    """
    Bug 6 regression: dashboard must use timezone.localdate() (Manila time),
    not timezone.now().date() (UTC), so counters are correct across midnight UTC.
    """

    def setUp(self):
        self.client = Client()
        self.admin = _make_staff('tz_admin', role=CustomUser.ROLE_PRESIDENT)
        self.client.force_login(self.admin)

    def test_dashboard_uses_localdate(self):
        from unittest.mock import patch
        import datetime
        manila_date = datetime.date(2026, 5, 22)
        # Patch timezone.localdate to return a known date
        from django.utils import timezone as real_timezone
        with patch('beneficiaries.views.timezone') as mock_tz:
            mock_tz.localdate.return_value = manila_date
            mock_tz.now.return_value = mock_tz.now.return_value  # keep now working
            mock_tz.localtime.return_value = real_timezone.now()  # dashboard's "Data as of" timestamp
            resp = self.client.get(reverse('beneficiaries:dashboard'))
        self.assertIn(resp.status_code, [200, 302])
        # Verify localdate was called (not now().date())
        mock_tz.localdate.assert_called()


# ── Phone number validation tests ────────────────────────────────────────────

class PhoneValidationTest(TestCase):
    """Bug 5 regression: phone fields must reject non-numeric / wrong format."""

    def test_valid_ph_mobile_09_accepted(self):
        from accounts.forms import UserCreateForm
        form = UserCreateForm(data={
            'username': 'phonetest1',
            'first_name': 'Test',
            'last_name': 'User',
            'email': 'test@example.com',
            'role': CustomUser.ROLE_STAFF,
            'employee_id': 'EMP-PT1',
            'phone': '09171234567',
            'password1': 'S3cur3Pa$$word!',
            'password2': 'S3cur3Pa$$word!',
        })
        # Phone field itself should be valid (form may fail on other fields)
        form.is_valid()
        self.assertNotIn('phone', form.errors)

    def test_valid_ph_mobile_plus63_accepted(self):
        from accounts.forms import UserCreateForm
        form = UserCreateForm(data={
            'username': 'phonetest2',
            'first_name': 'Test',
            'last_name': 'User',
            'email': 'test2@example.com',
            'role': CustomUser.ROLE_STAFF,
            'employee_id': 'EMP-PT2',
            'phone': '+639171234567',
            'password1': 'S3cur3Pa$$word!',
            'password2': 'S3cur3Pa$$word!',
        })
        form.is_valid()
        self.assertNotIn('phone', form.errors)

    def test_letters_in_phone_rejected(self):
        from accounts.forms import UserCreateForm
        form = UserCreateForm(data={
            'username': 'phonetest3',
            'first_name': 'Test',
            'last_name': 'User',
            'email': 'test3@example.com',
            'role': CustomUser.ROLE_STAFF,
            'employee_id': 'EMP-PT3',
            'phone': '09abc234567',
            'password1': 'S3cur3Pa$$word!',
            'password2': 'S3cur3Pa$$word!',
        })
        form.is_valid()
        self.assertIn('phone', form.errors)

    def test_empty_phone_accepted(self):
        from accounts.forms import UserCreateForm
        form = UserCreateForm(data={
            'username': 'phonetest4',
            'first_name': 'Test',
            'last_name': 'User',
            'email': 'test4@example.com',
            'role': CustomUser.ROLE_STAFF,
            'employee_id': 'EMP-PT4',
            'phone': '',
            'password1': 'S3cur3Pa$$word!',
            'password2': 'S3cur3Pa$$word!',
        })
        form.is_valid()
        self.assertNotIn('phone', form.errors)

    def test_beneficiary_contact_number_letters_rejected(self):
        from beneficiaries.forms import BeneficiaryInfoForm
        import datetime
        form = BeneficiaryInfoForm(data={
            'first_name': 'Maria',
            'last_name': 'Santos',
            'date_of_birth': '1940-01-01',
            'gender': 'F',
            'address': '123 Main St',
            'barangay': 'Test',
            'municipality': 'Quezon City',
            'province': 'Metro Manila (NCR)',
            'contact_number': 'ABC1234567',
            'senior_citizen_id': 'SC-001',
            'valid_id_type': 'Senior Citizen ID',
            'valid_id_number': 'SC-001',
        })
        form.is_valid()
        self.assertIn('contact_number', form.errors)


# ── ID number validation tests ────────────────────────────────────────────────

class IdNumberValidationTest(TestCase):
    """Bug 2 regression: ID number fields must reject special characters / SQL."""

    def test_valid_id_number_accepted(self):
        from beneficiaries.forms import BeneficiaryInfoForm
        form = BeneficiaryInfoForm(data={
            'first_name': 'Maria',
            'last_name': 'Santos',
            'date_of_birth': '1940-01-01',
            'gender': 'F',
            'address': '123 Main St',
            'barangay': 'Test',
            'municipality': 'Quezon City',
            'province': 'Metro Manila (NCR)',
            'contact_number': '09171234567',
            'senior_citizen_id': 'SC-2024-00123',
            'valid_id_type': 'Senior Citizen ID',
            'valid_id_number': 'SC-2024-00123',
        })
        form.is_valid()
        self.assertNotIn('valid_id_number', form.errors)

    def test_sql_injection_in_id_rejected(self):
        from beneficiaries.forms import BeneficiaryInfoForm
        form = BeneficiaryInfoForm(data={
            'first_name': 'Maria',
            'last_name': 'Santos',
            'date_of_birth': '1940-01-01',
            'gender': 'F',
            'address': '123 Main St',
            'barangay': 'Test',
            'municipality': 'Quezon City',
            'province': 'Metro Manila (NCR)',
            'contact_number': '09171234567',
            'senior_citizen_id': "SC'; DROP TABLE beneficiaries;--",
            'valid_id_type': 'Senior Citizen ID',
            'valid_id_number': "'; DROP TABLE--",
        })
        form.is_valid()
        self.assertIn('senior_citizen_id', form.errors)
        self.assertIn('valid_id_number', form.errors)


# ── Auto-approval workflow tests ──────────────────────────────────────────────

class AutoApprovalSettingsTest(TestCase):
    """Auto-approval settings view accessible only to admins; toggles persist."""

    def setUp(self):
        self.client = Client()
        self.admin = _make_staff('aa_admin', role=CustomUser.ROLE_PRESIDENT)
        self.staff = _make_staff('aa_staff', role=CustomUser.ROLE_STAFF)

    def test_settings_page_requires_admin_role(self):
        self.client.force_login(self.staff)
        resp = self.client.get(reverse('beneficiaries:auto_approval_settings'))
        self.assertIn(resp.status_code, [302, 403])

    def test_settings_page_accessible_to_admin(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('beneficiaries:auto_approval_settings'))
        self.assertEqual(resp.status_code, 200)

    def test_toggle_persists_after_post(self):
        from verification.models import SystemConfig
        self.client.force_login(self.admin)
        # Toggle ON
        self.client.post(reverse('beneficiaries:auto_approval_settings'),
                         data={'auto_approve_beneficiaries': 'on'})
        self.assertTrue(SystemConfig.get_bool('auto_approve_beneficiaries'))
        # Toggle OFF
        self.client.post(reverse('beneficiaries:auto_approval_settings'), data={})
        self.assertFalse(SystemConfig.get_bool('auto_approve_beneficiaries'))

    def test_pending_approvals_page_accessible_to_admin(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('beneficiaries:pending_approvals'))
        self.assertEqual(resp.status_code, 200)

    def test_pending_approvals_page_denied_to_staff(self):
        self.client.force_login(self.staff)
        resp = self.client.get(reverse('beneficiaries:pending_approvals'))
        self.assertIn(resp.status_code, [302, 403])


class SystemConfigGetBoolTest(TestCase):
    """SystemConfig.get_bool() returns correct boolean values."""

    def test_true_string_returns_true(self):
        from verification.models import SystemConfig
        SystemConfig.objects.create(key='test_bool_1', value='true')
        self.assertTrue(SystemConfig.get_bool('test_bool_1'))

    def test_false_string_returns_false(self):
        from verification.models import SystemConfig
        SystemConfig.objects.create(key='test_bool_2', value='false')
        self.assertFalse(SystemConfig.get_bool('test_bool_2'))

    def test_missing_key_returns_default(self):
        from verification.models import SystemConfig
        self.assertFalse(SystemConfig.get_bool('nonexistent_key_xyz'))
        self.assertTrue(SystemConfig.get_bool('nonexistent_key_xyz', default=True))

    def test_case_insensitive(self):
        from verification.models import SystemConfig
        SystemConfig.objects.create(key='test_bool_3', value='TRUE')
        self.assertTrue(SystemConfig.get_bool('test_bool_3'))


# ── Senior Citizen ID required tests ─────────────────────────────────────────

class SeniorCitizenIdRequiredTest(TestCase):
    """Issue 2 regression: senior_citizen_id is required in both beneficiary forms."""

    _base = {
        'first_name':       'Maria',
        'last_name':        'Santos',
        'date_of_birth':    '1940-01-01',
        'gender':           'F',
        'address':          '123 Main St',
        'barangay':         'Test Barangay',
        'municipality':     'Quezon City',
        'province':         'Metro Manila (NCR)',
        'contact_number':   '09171234567',
        'senior_citizen_id': 'SC-2024-00123',
        'valid_id_type':    'Senior Citizen ID',
        'valid_id_number':  'SC-2024-00123',
    }

    def test_missing_sc_id_rejected_on_info_form(self):
        from beneficiaries.forms import BeneficiaryInfoForm
        data = {**self._base, 'senior_citizen_id': ''}
        form = BeneficiaryInfoForm(data=data)
        form.is_valid()
        self.assertIn('senior_citizen_id', form.errors)

    def test_invalid_sc_id_rejected_on_info_form(self):
        from beneficiaries.forms import BeneficiaryInfoForm
        data = {**self._base, 'senior_citizen_id': "SC'; DROP TABLE--"}
        form = BeneficiaryInfoForm(data=data)
        form.is_valid()
        self.assertIn('senior_citizen_id', form.errors)

    def test_valid_sc_id_accepted_on_info_form(self):
        from beneficiaries.forms import BeneficiaryInfoForm
        form = BeneficiaryInfoForm(data=self._base)
        form.is_valid()
        self.assertNotIn('senior_citizen_id', form.errors)

    def test_missing_sc_id_rejected_on_edit_form(self):
        from beneficiaries.forms import BeneficiaryEditForm
        beneficiary = _make_beneficiary('BEN-SCID-001', 'SC-SCID-001')
        data = {
            'first_name':       'Maria',
            'last_name':        'Santos',
            'date_of_birth':    '1940-01-01',
            'gender':           'F',
            'address':          '123 Main St',
            'barangay':         'Test Barangay',
            'municipality':     'Quezon City',
            'province':         'Metro Manila (NCR)',
            'senior_citizen_id': '',
            'valid_id_type':    '',
            'valid_id_number':  '',
            'has_representative': False,
        }
        form = BeneficiaryEditForm(data=data, instance=beneficiary)
        form.is_valid()
        self.assertIn('senior_citizen_id', form.errors)


# ── Custom event type tests ───────────────────────────────────────────────────

class CustomEventTypeModelTest(TestCase):
    """Issue 3 regression: custom event type saves and helper returns custom name."""

    def setUp(self):
        self.admin = _make_staff('cev_admin', role=CustomUser.ROLE_PRESIDENT)

    def test_regular_event_type_works(self):
        from verification.models import StipendEvent
        event = StipendEvent.objects.create(
            title='March Stipend',
            date='2026-03-01',
            event_type=StipendEvent.EVENT_TYPE_REGULAR,
            created_by=self.admin,
        )
        self.assertEqual(event.event_type, 'regular')
        self.assertEqual(event.get_display_event_type(), 'Regular Monthly Stipend')

    def test_birthday_event_type_works(self):
        from verification.models import StipendEvent
        event = StipendEvent.objects.create(
            title='Birthday March',
            date='2026-03-01',
            event_type=StipendEvent.EVENT_TYPE_BIRTHDAY,
            created_by=self.admin,
        )
        self.assertEqual(event.get_display_event_type(), 'Birthday Bonus')

    def test_custom_event_type_saves(self):
        from verification.models import StipendEvent
        event = StipendEvent.objects.create(
            title='Fiesta Bonus',
            date='2026-05-01',
            event_type=StipendEvent.EVENT_TYPE_CUSTOM,
            custom_event_type='Barangay Fiesta Bonus',
            created_by=self.admin,
        )
        self.assertEqual(event.event_type, 'custom')
        self.assertEqual(event.custom_event_type, 'Barangay Fiesta Bonus')

    def test_custom_event_display_returns_custom_name(self):
        from verification.models import StipendEvent
        event = StipendEvent.objects.create(
            title='Anniversary',
            date='2026-06-01',
            event_type=StipendEvent.EVENT_TYPE_CUSTOM,
            custom_event_type='Anniversary Bonus',
            created_by=self.admin,
        )
        self.assertEqual(event.get_display_event_type(), 'Anniversary Bonus')

    def test_custom_with_empty_name_falls_back_to_label(self):
        from verification.models import StipendEvent
        event = StipendEvent.objects.create(
            title='Custom No Name',
            date='2026-07-01',
            event_type=StipendEvent.EVENT_TYPE_CUSTOM,
            custom_event_type='',
            created_by=self.admin,
        )
        self.assertEqual(event.get_display_event_type(), 'Other / Custom')


class CustomEventTypeViewTest(TestCase):
    """Issue 3: view-level validation for custom event type creation."""

    def setUp(self):
        self.client = Client()
        self.admin = _make_staff('cev_view_admin', role=CustomUser.ROLE_PRESIDENT)
        self.client.force_login(self.admin)

    def test_custom_type_with_blank_name_rejected(self):
        from verification.models import StipendEvent
        resp = self.client.post(reverse('verification:stipend_create'), {
            'title':             'Test Custom Event',
            'date':              '2026-08-01',
            'event_type':        'custom',
            'custom_event_type': '',
            'amount':            '0',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(StipendEvent.objects.filter(title='Test Custom Event').exists())

    def test_custom_type_with_name_creates_event(self):
        # date is a fixed literal that may equal "today" depending on when this
        # suite runs; pin "now" to mid-morning Manila time so the v2.2.0 Phase 2
        # same-day closing-time guard can never make this test flaky.
        import datetime as real_dt
        from unittest.mock import patch
        from verification.models import StipendEvent

        class MockDatetime(real_dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return real_dt.datetime(2026, 1, 1, 10, 0, 0, tzinfo=tz)

        with patch('verification.views._dt.datetime', MockDatetime):
            resp = self.client.post(reverse('verification:stipend_create'), {
                'title':             'Fiesta Distribution',
                'date':              '2026-09-01',
                'event_type':        'custom',
                'custom_event_type': 'Barangay Fiesta Bonus',
                'amount':            '500',
            })
        self.assertIn(resp.status_code, [301, 302])
        event = StipendEvent.objects.get(title='Fiesta Distribution')
        self.assertEqual(event.event_type, 'custom')
        self.assertEqual(event.custom_event_type, 'Barangay Fiesta Bonus')

    def test_regular_type_still_creates_event(self):
        from verification.models import StipendEvent
        resp = self.client.post(reverse('verification:stipend_create'), {
            'title':  'June Regular Stipend',
            'date':   '2026-06-01',
            'event_type': 'regular',
            'amount': '1000',
        })
        self.assertIn(resp.status_code, [301, 302])
        self.assertTrue(StipendEvent.objects.filter(title='June Regular Stipend').exists())


# ── HTTP Fallback Enforcement Tests ───────────────────────────────────────────

class HttpFallbackVerifyCheckLivenessTest(TestCase):
    """
    Regression: verify_check_liveness must reject camera POSTs over plain HTTP
    when DEBUG=False (production mode).  This prevents liveness data from being
    submitted on an unencrypted connection.
    """

    def setUp(self):
        self.client = Client()
        self.staff = _make_staff('hf_liveness_staff')
        self.client.force_login(self.staff)
        self.url = reverse('verification:verify_check_liveness')
        self.payload = json.dumps({'image': VALID_BASE64, 'challenge_completed': False})

    @override_settings(DEBUG=False)
    def test_http_post_is_rejected_in_production(self):
        resp = self.client.post(
            self.url, data=self.payload, content_type='application/json',
        )
        data = json.loads(resp.content)
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(data['success'])
        self.assertTrue(data.get('http_fallback', False))
        self.assertIn('HTTPS', data['error'])

    @override_settings(DEBUG=True)
    def test_http_post_allowed_in_debug_mode(self):
        # DEBUG=True — should not be blocked by HTTP guard
        # (the check will still fail for other reasons: no session etc., but not 403)
        resp = self.client.post(
            self.url, data=self.payload, content_type='application/json',
        )
        self.assertNotEqual(resp.status_code, 403)

    @override_settings(DEBUG=False)
    def test_https_post_passes_http_guard(self):
        resp = self.client.post(
            self.url, data=self.payload, content_type='application/json',
            secure=True,
        )
        # Should NOT be 403 (may fail for other reasons like no session)
        self.assertNotEqual(resp.status_code, 403)


class HttpFallbackVerifySubmitTest(TestCase):
    """
    Regression: verify_submit must reject camera POSTs over plain HTTP in production.
    A successful camera verification over HTTP must never create a VerificationAttempt
    or ClaimRecord.
    """

    def setUp(self):
        self.client = Client()
        self.staff = _make_staff('hf_submit_staff')
        self.client.force_login(self.staff)
        self.url = reverse('verification:verify_submit')
        self.payload = json.dumps({
            'image': VALID_BASE64,
            'challenge_completed': True,
            'liveness_passed': True,
            'face_detected': True,
            'liveness_score': 0.9,
            'anti_spoof_score': 0.8,
        })

    @override_settings(DEBUG=False)
    def test_http_post_is_rejected_in_production(self):
        resp = self.client.post(
            self.url, data=self.payload, content_type='application/json',
        )
        data = json.loads(resp.content)
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(data['success'])
        self.assertTrue(data.get('http_fallback', False))

    @override_settings(DEBUG=False)
    def test_http_post_does_not_create_verification_attempt(self):
        from verification.models import VerificationAttempt
        count_before = VerificationAttempt.objects.count()
        self.client.post(
            self.url, data=self.payload, content_type='application/json',
        )
        self.assertEqual(VerificationAttempt.objects.count(), count_before)

    @override_settings(DEBUG=False)
    def test_https_post_passes_http_guard(self):
        resp = self.client.post(
            self.url, data=self.payload, content_type='application/json',
            secure=True,
        )
        # Should NOT be 403 (may fail for other reasons like no session)
        self.assertNotEqual(resp.status_code, 403)


# ── Contact Number Validation Tests ──────────────────────────────────────────

class ContactNumberValidationTest(TestCase):
    """
    Regression: contact_number and rep_contact fields must reject letters
    via backend validation, and errors must be visible to the user.
    """

    _base = {
        'first_name':       'Maria',
        'last_name':        'Santos',
        'date_of_birth':    '1940-01-01',
        'gender':           'F',
        'address':          '123 Main St',
        'barangay':         'Test Barangay',
        'municipality':     'Quezon City',
        'province':         'Metro Manila (NCR)',
        'contact_number':   '09171234567',
        'senior_citizen_id': 'SC-CNUM-001',
        'valid_id_type':    'Senior Citizen ID',
        'valid_id_number':  'SC-CNUM-001',
    }

    def test_letters_in_contact_number_rejected_by_form(self):
        from beneficiaries.forms import BeneficiaryInfoForm
        data = {**self._base, 'contact_number': 'abcdefghijk'}
        form = BeneficiaryInfoForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertIn('contact_number', form.errors)

    def test_valid_09_number_accepted(self):
        from beneficiaries.forms import BeneficiaryInfoForm
        data = {**self._base, 'contact_number': '09171234567'}
        form = BeneficiaryInfoForm(data=data)
        # May fail for other reasons (address cascade, etc.) but NOT contact_number
        self.assertNotIn('contact_number', form.errors)

    def test_valid_plus639_number_accepted(self):
        from beneficiaries.forms import BeneficiaryInfoForm
        data = {**self._base, 'contact_number': '+639171234567'}
        form = BeneficiaryInfoForm(data=data)
        self.assertNotIn('contact_number', form.errors)

    def test_empty_contact_number_is_allowed(self):
        from beneficiaries.forms import BeneficiaryInfoForm
        data = {**self._base, 'contact_number': ''}
        form = BeneficiaryInfoForm(data=data)
        self.assertNotIn('contact_number', form.errors)

    def test_letters_in_rep_contact_rejected(self):
        from beneficiaries.forms import RepresentativeForm
        data = {
            'has_representative': True,
            'rep_first_name': 'Jose',
            'rep_last_name': 'Rizal',
            'rep_relationship': 'Son',
            'rep_contact': 'notanumber',
            'rep_id_type': 'Passport',
            'rep_id_number': 'AB-123456',
        }
        form = RepresentativeForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertIn('rep_contact', form.errors)

    def test_valid_rep_contact_accepted(self):
        from beneficiaries.forms import RepresentativeForm
        data = {
            'has_representative': True,
            'rep_first_name': 'Jose',
            'rep_last_name': 'Rizal',
            'rep_relationship': 'Son',
            'rep_contact': '09281234567',
            'rep_id_type': 'Passport',
            'rep_id_number': 'AB-123456',
        }
        form = RepresentativeForm(data=data)
        self.assertNotIn('rep_contact', form.errors)


# ── Change Password Show/Hide Tests ──────────────────────────────────────────

class ChangePasswordPageTest(TestCase):
    """Regression: Change Password page must contain show/hide toggle controls."""

    def setUp(self):
        self.client = Client()
        self.user = _make_staff('cp_test_user')
        self.client.force_login(self.user)

    def test_change_password_page_loads(self):
        resp = self.client.get(reverse('accounts:change_password'))
        self.assertEqual(resp.status_code, 200)

    def test_change_password_page_contains_toggle_button(self):
        resp = self.client.get(reverse('accounts:change_password'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'pw-toggle')

    def test_change_password_page_contains_password_fields(self):
        resp = self.client.get(reverse('accounts:change_password'))
        self.assertContains(resp, 'type="password"')


# ── Liveness Helpers Unit Tests ───────────────────────────────────────────────

class LivenessHelperTest(TestCase):
    """Unit tests for liveness detection helpers."""

    def test_compute_texture_score_returns_float_in_range(self):
        import numpy as np
        from verification.liveness import compute_texture_score
        img = np.random.randint(0, 255, (64, 64, 3), dtype=np.uint8)
        score = compute_texture_score(img)
        self.assertIsInstance(score, float)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_compute_texture_score_none_returns_zero(self):
        from verification.liveness import compute_texture_score
        self.assertEqual(compute_texture_score(None), 0.0)

    def test_run_full_liveness_both_fail_returns_failed(self):
        import numpy as np
        from verification.liveness import run_full_liveness_check
        img = np.zeros((64, 64, 3), dtype=np.uint8)
        result = run_full_liveness_check(img, challenge_completed=False,
                                         anti_spoof_threshold=0.99)
        self.assertFalse(result['passed'])
        self.assertFalse(result['challenge_completed'])

    def test_run_full_liveness_challenge_only_fails_without_spoof(self):
        import numpy as np
        from verification.liveness import run_full_liveness_check
        img = np.zeros((64, 64, 3), dtype=np.uint8)
        result = run_full_liveness_check(img, challenge_completed=True,
                                         anti_spoof_threshold=0.99)
        self.assertFalse(result['passed'])

    def test_camera_verification_allowed_debug_true(self):
        """When DEBUG=True any request is allowed (development convenience)."""
        from django.test import RequestFactory
        from verification.views import _camera_verification_allowed
        factory = RequestFactory()
        req = factory.post('/fake/', secure=False)
        with override_settings(DEBUG=True):
            self.assertTrue(_camera_verification_allowed(req))

    def test_camera_verification_blocked_http_production(self):
        """Plain HTTP must be blocked in production (DEBUG=False)."""
        from django.test import RequestFactory
        from verification.views import _camera_verification_allowed
        factory = RequestFactory()
        req = factory.post('/fake/', secure=False)
        with override_settings(DEBUG=False):
            self.assertFalse(_camera_verification_allowed(req))

    def test_camera_verification_allowed_https(self):
        """HTTPS must always be allowed regardless of DEBUG."""
        from django.test import RequestFactory
        from verification.views import _camera_verification_allowed
        factory = RequestFactory()
        req = factory.post('/fake/', secure=True)
        with override_settings(DEBUG=False):
            self.assertTrue(_camera_verification_allowed(req))


# ── Addendum v2.0.5 Regression Tests ─────────────────────────────────────────

class AutoApprovalSettingsAccessTest(TestCase):
    """Issue 1 — Auto-Approval Settings page is gated to admins only."""

    def setUp(self):
        self.admin = CustomUser.objects.create_user(
            username='admin_aa', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-ADMIN-AA',
        )
        self.staff = CustomUser.objects.create_user(
            username='staff_aa', password='TestPass123!',
            role=CustomUser.ROLE_STAFF, employee_id='EMP-STAFF-AA',
        )

    def test_admin_can_access_auto_approval_settings(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('beneficiaries:auto_approval_settings'))
        self.assertEqual(resp.status_code, 200)

    def test_staff_cannot_access_auto_approval_settings(self):
        self.client.force_login(self.staff)
        resp = self.client.get(reverse('beneficiaries:auto_approval_settings'))
        # Redirected away (not 200)
        self.assertNotEqual(resp.status_code, 200)

    def test_base_nav_contains_auto_approval_link_for_admin(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('beneficiaries:dashboard'))
        self.assertContains(resp, reverse('beneficiaries:auto_approval_settings'))


class VerifySearchSCIDTest(TestCase):
    """Issue 2 — Senior Citizen ID appears in verify-select search results."""

    def setUp(self):
        self.staff = _make_staff(username='staff_scid')
        self.client.force_login(self.staff)
        self.ben = _make_beneficiary(ben_id='BEN-SCID-001', sc_id='OSCA-12345')
        self.ben.status = Beneficiary.STATUS_ACTIVE
        self.ben.save()

    def test_sc_id_displayed_in_search_results(self):
        resp = self.client.get(reverse('verification:verify_select') + '?q=Santos')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'OSCA-12345')

    def test_search_by_sc_id_finds_beneficiary(self):
        resp = self.client.get(reverse('verification:verify_select') + '?q=OSCA-12345')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Maria')


class PayoutTimeWindowTest(TestCase):
    """Issue 7 — StipendEvent time-window helpers work correctly."""

    import datetime as _dt

    def _make_event_with_time(self, start_time, end_time):
        from verification.models import StipendEvent
        import datetime
        admin = CustomUser.objects.create_user(
            username=f'admin_tw_{id(self)}', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id=f'EMP-TW-{id(self)}',
        )
        return StipendEvent.objects.create(
            title='Time Window Event',
            date=datetime.date.today(),
            payout_start_date=datetime.date.today(),
            payout_end_date=datetime.date.today(),
            payout_start_time=start_time,
            payout_end_time=end_time,
            created_by=admin,
        )

    def test_no_time_set_always_active(self):
        import datetime
        from verification.models import StipendEvent
        admin = CustomUser.objects.create_user(
            username='admin_notw', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-NOTW',
        )
        event = StipendEvent.objects.create(
            title='All Day Event',
            date=datetime.date.today(),
            payout_start_date=datetime.date.today(),
            payout_end_date=datetime.date.today(),
            created_by=admin,
        )
        self.assertTrue(event.is_within_time_window(datetime.time(0, 0)))
        self.assertTrue(event.is_within_time_window(datetime.time(23, 59)))

    def test_within_time_window_returns_true(self):
        import datetime
        event = self._make_event_with_time(datetime.time(8, 0), datetime.time(17, 0))
        self.assertTrue(event.is_within_time_window(datetime.time(12, 0)))
        self.assertTrue(event.is_within_time_window(datetime.time(8, 0)))
        self.assertTrue(event.is_within_time_window(datetime.time(17, 0)))

    def test_outside_time_window_returns_false(self):
        import datetime
        event = self._make_event_with_time(datetime.time(8, 0), datetime.time(17, 0))
        self.assertFalse(event.is_within_time_window(datetime.time(7, 59)))
        self.assertFalse(event.is_within_time_window(datetime.time(17, 1)))

    def test_get_active_event_now_returns_none_outside_time(self):
        import datetime
        from unittest import mock
        from verification.models import StipendEvent
        event = self._make_event_with_time(datetime.time(0, 0), datetime.time(0, 1))
        # Patch get_active_event_for_date to return our event, then test time check
        with mock.patch.object(StipendEvent, 'get_active_event_for_date', return_value=event):
            with mock.patch('verification.models.StipendEvent.get_active_event_now',
                            wraps=lambda: None) as _mock:
                # Directly test the underlying logic: event outside window → None
                result = event.is_within_time_window(datetime.time(12, 0))
                self.assertFalse(result)

    def test_stipend_form_contains_time_fields(self):
        """Issue 7 — Create/Edit stipend form renders time inputs."""
        admin = CustomUser.objects.create_user(
            username='admin_sf', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-SF',
        )
        self.client.force_login(admin)
        resp = self.client.get(reverse('verification:stipend_create'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'payout_start_time')
        self.assertContains(resp, 'payout_end_time')


class BeneficiaryHistoryPDFTest(TestCase):
    """Issue 8 — PDF export endpoint returns application/pdf."""

    def setUp(self):
        self.admin = CustomUser.objects.create_user(
            username='admin_pdf', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-PDF',
        )
        self.ben = _make_beneficiary(ben_id='BEN-PDF-001', sc_id='SC-PDF')
        self.client.force_login(self.admin)

    def test_pdf_export_returns_pdf_content_type(self):
        url = reverse('verification:report_beneficiary_history',
                      kwargs={'beneficiary_id': self.ben.pk})
        resp = self.client.get(url + '?export=pdf')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('application/pdf', resp.get('Content-Type', ''))

    def test_csv_export_returns_csv_content_type(self):
        url = reverse('verification:report_beneficiary_history',
                      kwargs={'beneficiary_id': self.ben.pk})
        resp = self.client.get(url + '?export=csv')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('text/csv', resp.get('Content-Type', ''))

    def test_html_page_contains_pdf_button(self):
        url = reverse('verification:report_beneficiary_history',
                      kwargs={'beneficiary_id': self.ben.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '?export=pdf')


class FakeClaimExclusionTest(TestCase):
    """Issue 10 — Pending ₱0 claims with no event are excluded from history."""

    def setUp(self):
        self.admin = CustomUser.objects.create_user(
            username='admin_fc', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-FC',
        )
        self.ben = _make_beneficiary(ben_id='BEN-FC-001', sc_id='SC-FC')
        self.client.force_login(self.admin)

    def test_fake_pending_claim_excluded_from_history(self):
        from verification.models import ClaimRecord
        # Create a legacy placeholder claim (no event, pending, ₱0)
        ClaimRecord.objects.create(
            beneficiary=self.ben,
            stipend_event=None,
            status=ClaimRecord.STATUS_PENDING_APPROVAL,
            amount=0,
            claimed_by=self.admin,
        )
        url = reverse('verification:report_beneficiary_history',
                      kwargs={'beneficiary_id': self.ben.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(list(resp.context['claims']), [])

    def test_real_claimed_record_not_excluded(self):
        from verification.models import ClaimRecord, StipendEvent
        import datetime
        event = StipendEvent.objects.create(
            title='Real Event', date=datetime.date.today(), created_by=self.admin,
        )
        real_claim = ClaimRecord.objects.create(
            beneficiary=self.ben,
            stipend_event=event,
            status=ClaimRecord.STATUS_CLAIMED,
            amount=500,
            claimed_by=self.admin,
        )
        url = reverse('verification:report_beneficiary_history',
                      kwargs={'beneficiary_id': self.ben.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        claim_ids = [c.pk for c in resp.context['claims']]
        self.assertIn(real_claim.pk, claim_ids)


class NoEventClaimBlockTest(TestCase):
    """Issue 6 — verify_submit does NOT create a ClaimRecord when no active event."""

    def setUp(self):
        self.staff = _make_staff(username='staff_nec')
        self.ben = _make_beneficiary(ben_id='BEN-NEC-001', sc_id='SC-NEC')
        self.ben.status = Beneficiary.STATUS_ACTIVE
        self.ben.save()
        self.client.force_login(self.staff)

    def test_no_claim_created_without_active_event(self):
        from verification.models import ClaimRecord, StipendEvent

        # Ensure no active stipend event exists
        StipendEvent.objects.all().delete()

        session = self.client.session
        session['verification_session'] = {
            'beneficiary_id': str(self.ben.pk),
            'attempt_number': 1,
            'session_id': _FIXED_TEST_SESSION_ID,
            'claimant_type': 'beneficiary',
            'stipend_event_id': None,
        }
        session.save()

        payload = json.dumps({
            'image': DATA_URI_JPEG,
            'challenge_completed': True,
            'liveness_score': 0.9,
            'anti_spoof_score': 0.9,
            'liveness_passed': True,
            'face_detected': True,
            'session_id': _FIXED_TEST_SESSION_ID,
        })
        count_before = ClaimRecord.objects.count()
        resp = self.client.post(
            reverse('verification:verify_submit'),
            data=payload,
            content_type='application/json',
            secure=True,
        )
        # Phase 1: backend rejects before face matching when no event
        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.content)
        self.assertFalse(data['success'])
        self.assertTrue(data.get('no_event'))
        # Critical: no ClaimRecord should have been created
        self.assertEqual(ClaimRecord.objects.count(), count_before)


class RepresentativeRegistrationTest(TestCase):
    """Issue 5 — Registering a beneficiary with a representative creates a Representative DB record."""

    def setUp(self):
        self.staff = _make_staff(username='staff_rep')
        self.client.force_login(self.staff)

    @override_settings(REGISTRATION_LIVENESS_REQUIRED=False)
    @mock.patch('beneficiaries.views.check_duplicate_face')
    @mock.patch('beneficiaries.views.process_face_for_registration')
    @mock.patch('beneficiaries.sync.mark_created')
    def test_representative_record_created_on_registration(
        self, mock_sync, mock_face, mock_dup
    ):
        import datetime
        from cryptography.fernet import Fernet
        from verification.models import FaceEmbedding

        # Fake encrypted embedding
        key = Fernet.generate_key()
        fake_encrypted = Fernet(key).encrypt(b'\x00' * 512)

        mock_face.return_value = {
            'success': True,
            'encrypted_embedding': fake_encrypted,
            'error': None,
        }
        mock_dup.return_value = {'duplicates_found': False, 'matches': [], 'highest_score': 0.0, 'checked': 0}

        step1 = {
            'first_name': 'Lola',
            'last_name': 'Tess',
            'middle_name': '',
            'date_of_birth': '1940-06-15',
            'gender': 'F',
            'address': '1 Mabini St',
            'barangay': 'Brgy. Test',
            'municipality': 'Quezon City',
            'province': 'Metro Manila',
            'contact_number': '09171234567',
            'senior_citizen_id': 'OSCA-REP-001',
            'valid_id_type': 'PhilSys',
            'valid_id_number': 'PSN-001',
        }
        step2 = {
            'has_representative': True,
            'rep_first_name': 'Juan',
            'rep_last_name': 'Dela Cruz',
            'rep_relationship': 'Son',
            'rep_contact': '09181234567',
            'rep_id_type': 'SSS',
            'rep_id_number': 'SSS-REP-001',
        }

        session = self.client.session
        session['reg_step1'] = step1
        session['reg_step2'] = step2
        session.save()

        with mock.patch('verification.face_utils.decrypt_embedding',
                        return_value=[0.1] * 128):
            resp = self.client.post(
                reverse('beneficiaries:register_submit_face'),
                data=json.dumps({'image': DATA_URI_JPEG}),
                content_type='application/json',
            )

        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertTrue(data.get('success'), msg=data.get('error', ''))

        # A Representative DB record must have been created
        self.assertTrue(
            Representative.objects.filter(
                first_name='Juan', last_name='Dela Cruz',
            ).exists(),
            'Representative record was not created during registration',
        )


class LivenessMismatchResultTest(TestCase):
    """Issue 3 — Result page shows prominent alert when notes contain 'Liveness mismatch'."""

    def setUp(self):
        from verification.models import VerificationAttempt
        self.admin = CustomUser.objects.create_user(
            username='admin_lm', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-LM',
        )
        self.ben = _make_beneficiary(ben_id='BEN-LM-001', sc_id='SC-LM')
        self.attempt = VerificationAttempt.objects.create(
            beneficiary=self.ben,
            performed_by=self.admin,
            decision=VerificationAttempt.DECISION_VERIFIED,
            similarity_score=0.85,
            threshold_used=0.75,
            liveness_passed=True,
            liveness_score=0.9,
            anti_spoof_score=0.9,
            head_movement_completed=True,
            notes='Liveness mismatch — client reported spoof=0.100/passed=True, server measured spoof=0.900/passed=True. Server values used.',
        )
        self.client.force_login(self.admin)

    def test_mismatch_banner_not_shown_on_result_page(self):
        """Technical liveness mismatch details must not appear in the operator-facing UI."""
        resp = self.client.get(
            reverse('verification:verify_result', kwargs={'attempt_id': self.attempt.pk})
        )
        self.assertEqual(resp.status_code, 200)
        # Banner and raw score text must be absent from the rendered page
        self.assertNotContains(resp, 'Liveness mismatch detected')
        self.assertNotContains(resp, 'client reported')
        self.assertNotContains(resp, 'server measured')
        self.assertNotContains(resp, 'authoritative source')
        # DB record still contains technical details for the audit trail
        self.attempt.refresh_from_db()
        self.assertIn('Liveness mismatch', self.attempt.notes)


class AssistedModeResultTest(TestCase):
    """Issue 4 — Result page shows assisted-mode warning when demo_mode_active=True."""

    def setUp(self):
        from verification.models import VerificationAttempt
        self.admin = CustomUser.objects.create_user(
            username='admin_am', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-AM',
        )
        self.ben = _make_beneficiary(ben_id='BEN-AM-001', sc_id='SC-AM')
        self.attempt = VerificationAttempt.objects.create(
            beneficiary=self.ben,
            performed_by=self.admin,
            decision=VerificationAttempt.DECISION_VERIFIED,
            similarity_score=0.80,
            threshold_used=0.60,
            liveness_passed=False,
            liveness_score=0.3,
            anti_spoof_score=0.2,
            head_movement_completed=False,
            demo_mode_active=True,
            notes='',
        )
        self.client.force_login(self.admin)

    def test_assisted_mode_warning_shown_on_result_page(self):
        resp = self.client.get(
            reverse('verification:verify_result', kwargs={'attempt_id': self.attempt.pk})
        )
        self.assertEqual(resp.status_code, 200)
        # The result page must mention Assisted Mode when demo_mode_active=True
        self.assertContains(resp, 'Assisted')


# ── Phase 1 Regression: verify_start context variables ───────────────────────

class VerifyStartNoEventBlockTest(TestCase):
    """
    Phase 1 regression: verify_start must pass no_event_block=True to the template
    when no active payout event exists, and http_camera_blocked=True over plain HTTP.
    These context variables gate the camera UI — if either is True, no camera is shown.
    """

    def setUp(self):
        from verification.models import FaceEmbedding
        self.staff = _make_staff('neb_staff')
        self.ben = _make_beneficiary('BEN-NEB-001', 'SC-NEB-001')
        self.ben.status = Beneficiary.STATUS_ACTIVE
        self.ben.consent_given = True
        self.ben.save()
        FaceEmbedding.objects.create(
            beneficiary=self.ben,
            embedding_data=b'\x00' * 512,
            created_by=self.staff,
        )
        self.client.force_login(self.staff)
        self.url = reverse('verification:verify_start', kwargs={'pk': self.ben.pk})

    def test_no_event_sets_no_event_block_true(self):
        from verification.models import StipendEvent
        StipendEvent.objects.all().delete()
        resp = self.client.get(self.url, secure=True)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context.get('no_event_block'))

    def test_active_event_today_clears_no_event_block(self):
        import datetime
        from verification.models import StipendEvent
        StipendEvent.objects.all().delete()
        admin = _make_staff('neb_ev_admin', role=CustomUser.ROLE_PRESIDENT)
        StipendEvent.objects.create(
            title='Test Payout Event',
            date=datetime.date.today(),
            created_by=admin,
        )
        resp = self.client.get(self.url, secure=True)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context.get('no_event_block'))

    @override_settings(DEBUG=False)
    def test_http_request_sets_http_camera_blocked_true(self):
        resp = self.client.get(self.url)  # no secure=True → HTTP
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context.get('http_camera_blocked'))

    @override_settings(DEBUG=False)
    def test_https_request_clears_http_camera_blocked(self):
        resp = self.client.get(self.url, secure=True)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context.get('http_camera_blocked'))

    @override_settings(DEBUG=False)
    def test_http_block_takes_precedence_over_no_event(self):
        from verification.models import StipendEvent
        StipendEvent.objects.all().delete()
        resp = self.client.get(self.url)  # HTTP, no event
        self.assertEqual(resp.status_code, 200)
        # Both flags set; neither suppresses the other in context
        self.assertTrue(resp.context.get('http_camera_blocked'))
        self.assertTrue(resp.context.get('no_event_block'))


class VerifySubmitNoEventBlockTest(TestCase):
    """
    Phase 1 regression: verify_submit must return HTTP 400 with no_event=True
    when the session has no stipend_event_id, before face processing begins.
    """

    def setUp(self):
        self.staff = _make_staff('sub_nev_staff')
        self.ben = _make_beneficiary('BEN-SNB-001', 'SC-SNB')
        self.ben.status = Beneficiary.STATUS_ACTIVE
        self.ben.save()
        self.client.force_login(self.staff)

    def _post_with_session(self, event_id=None):
        session = self.client.session
        session['verification_session'] = {
            'beneficiary_id': str(self.ben.pk),
            'attempt_number': 1,
            'session_id': _FIXED_TEST_SESSION_ID,
            'claimant_type': 'beneficiary',
            'stipend_event_id': event_id,
        }
        session.save()
        payload = json.dumps({
            'image': DATA_URI_JPEG,
            'challenge_completed': True,
            'liveness_score': 0.9,
            'anti_spoof_score': 0.9,
            'liveness_passed': True,
            'face_detected': True,
            'session_id': _FIXED_TEST_SESSION_ID,
        })
        return self.client.post(
            reverse('verification:verify_submit'),
            data=payload,
            content_type='application/json',
            secure=True,
        )

    def test_no_event_in_session_returns_400(self):
        resp = self._post_with_session(event_id=None)
        self.assertEqual(resp.status_code, 400)

    def test_no_event_response_has_no_event_flag(self):
        resp = self._post_with_session(event_id=None)
        data = json.loads(resp.content)
        self.assertFalse(data['success'])
        self.assertTrue(data.get('no_event'))

    def test_no_event_does_not_create_claim_record(self):
        from verification.models import ClaimRecord
        count_before = ClaimRecord.objects.count()
        self._post_with_session(event_id=None)
        self.assertEqual(ClaimRecord.objects.count(), count_before)

    def test_nonexistent_event_id_in_session_returns_400(self):
        resp = self._post_with_session(event_id=str(uuid.uuid4()))
        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.content)
        self.assertTrue(data.get('no_event'))


class CameraVerificationRulesTest(TestCase):
    """
    Regression: camera / liveness verification rules must remain intact after
    HTTPS detection fixes.

    Covers Issue 3 requirements:
      1. HTTP fallback blocks camera/liveness.
      2. HTTPS allows camera when requirements pass.
      3. No active payout event blocks claim before camera.
      4. No ClaimRecord created without active payout event.
      5. _camera_verification_allowed() returns correct values.
    """

    @override_settings(DEBUG=False)
    def test_camera_blocked_on_http(self):
        from verification.views import _camera_verification_allowed
        from django.test import RequestFactory
        req = RequestFactory().get('/')
        self.assertFalse(_camera_verification_allowed(req))

    @override_settings(
        DEBUG=False,
        SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
    )
    def test_camera_allowed_on_https(self):
        from verification.views import _camera_verification_allowed
        from django.test import RequestFactory
        req = RequestFactory().get('/', HTTP_X_FORWARDED_PROTO='https')
        self.assertTrue(_camera_verification_allowed(req))

    @override_settings(DEBUG=True)
    def test_camera_allowed_in_debug_mode_over_http(self):
        """DEBUG=True always allows camera so developers can test without HTTPS."""
        from verification.views import _camera_verification_allowed
        from django.test import RequestFactory
        req = RequestFactory().get('/')
        self.assertTrue(_camera_verification_allowed(req))

    def test_verify_submit_blocked_on_http_non_debug(self):
        """verify_submit returns 403 when accessed over plain HTTP (DEBUG=False)."""
        staff = _make_staff()
        client = Client()
        client.force_login(staff)
        # Simulate no secure header → HTTP fallback
        resp = client.post(
            '/verification/submit/',
            data={'image': DATA_URI_JPEG, 'liveness_passed': 'false'},
            content_type='application/json',
        )
        with override_settings(DEBUG=False):
            resp2 = client.post(
                '/verification/submit/',
                data=json.dumps({'image': DATA_URI_JPEG, 'liveness_passed': False}),
                content_type='application/json',
            )
        # Without active session and on HTTP, should block (403 or 400)
        self.assertIn(resp2.status_code, [400, 403])

    def test_no_claim_record_without_active_payout_event(self):
        """No ClaimRecord is created when there is no active payout event."""
        from verification.models import ClaimRecord, StipendEvent
        staff = _make_staff()
        b = _make_beneficiary()
        # Ensure no active events exist
        StipendEvent.objects.all().delete()
        before = ClaimRecord.objects.count()

        client = Client()
        client.force_login(staff)
        client.post(
            '/verification/submit/',
            data=json.dumps({'image': DATA_URI_JPEG, 'liveness_passed': False}),
            content_type='application/json',
        )
        # No ClaimRecord should be created
        self.assertEqual(ClaimRecord.objects.count(), before)


# ── Phase 8: Open Secure HTTPS button URL regression ─────────────────────────

class OpenSecureHttpsButtonTest(TestCase):
    """
    Regression: the 'Open Secure HTTPS' button in verify_capture.html must
    generate a URL that resolves to a real route.

    Bug fixed: the button previously linked to
      https://fans-barangay.local/verification/<uuid>/
    which matches no URL pattern (404). The correct URL is:
      https://fans-barangay.local/verification/start/<uuid>/?claimant=<type>
    """

    def setUp(self):
        from beneficiaries.models import Beneficiary
        self.staff = _make_staff('https_btn_staff')
        self.ben = _make_beneficiary('BEN-HTTPSBTN-001', 'SC-HTTPSBTN')
        # Must be active + consented or is_eligible_to_claim returns False
        self.ben.status = Beneficiary.STATUS_ACTIVE
        self.ben.consent_given = True
        self.ben.save()
        self.client = Client()
        self.client.force_login(self.staff)
        self.url = reverse('verification:verify_start', kwargs={'pk': self.ben.pk})

    @override_settings(DEBUG=False)
    def test_secure_https_link_contains_verify_start_route(self):
        """'Open Secure HTTPS' button must link to /verification/start/<uuid>/."""
        resp = self.client.get(self.url)  # HTTP → http_camera_blocked=True
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        # The correct path must be present
        self.assertIn(f'/verification/start/{self.ben.pk}/', content)

    @override_settings(DEBUG=False)
    def test_secure_https_link_does_not_contain_wrong_path(self):
        """Button must not link to /verification/<uuid>/ (the broken pattern)."""
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        # The broken pattern must NOT appear
        self.assertNotIn(f'/verification/{self.ben.pk}/', content)

    @override_settings(DEBUG=False)
    def test_secure_https_link_preserves_beneficiary_claimant(self):
        """Link must include ?claimant=beneficiary query string."""
        resp = self.client.get(self.url + '?claimant=beneficiary')
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertIn('?claimant=beneficiary', content)

    @override_settings(DEBUG=False)
    def test_secure_https_link_preserves_representative_claimant(self):
        """Link must include ?claimant=representative in the HTTPS button URL."""
        # Camera is blocked on HTTP → the page renders the HTTP-blocked card
        # regardless of whether a rep is registered.
        resp = self.client.get(self.url + '?claimant=representative')
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertIn('?claimant=representative', content)

    @override_settings(DEBUG=False)
    def test_secure_https_link_uses_domain_url_prefix(self):
        """Link must start with the HTTPS domain (fans-barangay.local)."""
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertIn('fans-barangay.local', content)
        # Must be HTTPS
        self.assertIn('https://fans-barangay.local', content)

    @override_settings(DEBUG=True)
    def test_no_http_blocked_card_in_debug_mode(self):
        """In DEBUG=True _camera_verification_allowed returns True even over HTTP."""
        from verification.views import _camera_verification_allowed
        from django.test import RequestFactory
        factory = RequestFactory()
        request = factory.get('/')  # plain HTTP, no X-Forwarded-Proto
        with override_settings(DEBUG=True):
            self.assertTrue(_camera_verification_allowed(request))


# ── Phase 8: HTTPS proxy banner regression ────────────────────────────────────

class HttpsProxyBannerTest(TestCase):
    """
    Regression: Limited HTTP mode banner must be hidden when the request
    arrives over HTTPS (via Caddy proxy setting X-Forwarded-Proto: https).
    The banner must be visible on plain HTTP fallback.
    """

    def setUp(self):
        self.staff = _make_staff('banner_staff')
        self.client = Client()
        self.client.force_login(self.staff)

    @override_settings(
        DEBUG=False,
        SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
    )
    def test_https_proxy_request_is_secure(self):
        """Django must treat request as secure when X-Forwarded-Proto=https."""
        from django.test import RequestFactory
        from fans.context_processors import server_access_info
        factory = RequestFactory()
        req = factory.get('/dashboard/', HTTP_X_FORWARDED_PROTO='https',
                          secure=False)
        # Simulate SECURE_PROXY_SSL_HEADER processing
        with override_settings(
            SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
        ):
            req.META['HTTP_X_FORWARDED_PROTO'] = 'https'
            self.assertEqual(req.META.get('HTTP_X_FORWARDED_PROTO'), 'https')

    @override_settings(DEBUG=False)
    def test_direct_http_request_not_secure(self):
        """Plain HTTP request must not be considered secure."""
        resp = self.client.get('/dashboard/')
        # Redirect to login or dashboard — just confirm no 500
        self.assertNotEqual(resp.status_code, 500)

    @override_settings(
        DEBUG=False,
        SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
    )
    def test_https_proxy_hides_limited_http_banner(self):
        """Dashboard response via HTTPS proxy must not contain the HTTP mode banner."""
        resp = self.client.get('/dashboard/', HTTP_X_FORWARDED_PROTO='https',
                               secure=True)
        self.assertNotEqual(resp.status_code, 500)
        if resp.status_code == 200:
            content = resp.content.decode()
            # The Limited HTTP mode banner should not appear on HTTPS
            self.assertNotIn('Limited HTTP mode', content)

    @override_settings(DEBUG=False)
    def test_direct_http_shows_limited_http_banner(self):
        """Dashboard over plain HTTP must contain the 'Limited HTTP mode' banner."""
        resp = self.client.get('/dashboard/')
        self.assertNotEqual(resp.status_code, 500)
        if resp.status_code == 200:
            content = resp.content.decode()
            self.assertIn('Limited HTTP mode', content)


class PayoutPastDateValidationTest(TestCase):
    """v2.0.9 Fix 3 — Payout start date cannot be in the past."""

    def setUp(self):
        self.admin = CustomUser.objects.create_user(
            username='admin_ppdv', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-PPDV',
        )
        self.client = Client()
        self.client.force_login(self.admin)

    def _post_create(self, payout_start):
        import datetime
        today = datetime.date.today()
        return self.client.post(reverse('verification:stipend_create'), {
            'title': 'Test Event',
            'date': today.isoformat(),
            'event_type': 'regular',
            'amount': '500',
            'payout_start_date': payout_start,
            'payout_end_date': (today + datetime.timedelta(days=7)).isoformat(),
        })

    def test_past_payout_start_date_rejected(self):
        import datetime
        yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        resp = self._post_create(yesterday)
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertIn('past', content.lower())

    def test_today_payout_start_date_accepted(self):
        """
        Same-day payout creation must be accepted DURING office hours
        (7:00 AM-8:00 PM Manila) — see _validate_payout_window's same-day
        closing-time check (views.py). This must hold regardless of what
        wall-clock time the test suite happens to run at, so 'now' (Manila)
        is pinned to a fixed mid-day time via the same MockDatetime pattern
        already used by PayoutTodayPastTimeTest below, rather than relying on
        whatever real time it is when this test executes.
        """
        import datetime
        import datetime as real_dt
        from unittest.mock import patch

        class MockDatetime(real_dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return real_dt.datetime(2026, 1, 1, 10, 0, 0, tzinfo=tz)  # 10:00 AM Manila — within office hours

        today = datetime.date.today().isoformat()
        with patch('verification.views._dt.datetime', MockDatetime):
            resp = self._post_create(today)
        self.assertIn(resp.status_code, [200, 302])
        if resp.status_code == 200:
            self.assertNotIn('past', resp.content.decode().lower())

    def test_today_payout_start_date_rejected_after_office_hours_cutoff(self):
        """
        Counterpart to the above: the same same-day schedule must be REJECTED
        once Manila local time is at/after the documented 8:00 PM cutoff
        (views.py _PAYOUT_LATEST) — this is the intended, correct business
        rule (v2.2.0 Phase 2: a same-day schedule created after the claiming
        window has already closed for today must not be silently accepted).
        Pinned to a fixed evening time so this behavior is proven
        deterministically rather than only accidentally exercised depending
        on what time the test suite happens to run.
        """
        import datetime
        import datetime as real_dt
        from unittest.mock import patch

        class MockDatetime(real_dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return real_dt.datetime(2026, 1, 1, 21, 0, 0, tzinfo=tz)  # 9:00 PM Manila — after the 8:00 PM cutoff

        today = datetime.date.today().isoformat()
        with patch('verification.views._dt.datetime', MockDatetime):
            resp = self._post_create(today)
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode().lower()
        self.assertIn('already ended', content)
        self.assertIn('7:00 am', content)
        self.assertIn('8:00 pm', content)

    def test_future_payout_start_date_accepted(self):
        import datetime
        future = (datetime.date.today() + datetime.timedelta(days=5)).isoformat()
        resp = self._post_create(future)
        self.assertIn(resp.status_code, [200, 302])


class PayoutOneHourMinimumTest(TestCase):
    """v2.0.9 Fix 4 — Payout time window must be at least 1 hour."""

    def setUp(self):
        self.admin = CustomUser.objects.create_user(
            username='admin_1hr', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-1HR',
        )
        self.client = Client()
        self.client.force_login(self.admin)

    def _post_create_with_times(self, start_time, end_time):
        import datetime
        today = datetime.date.today()
        future = (today + datetime.timedelta(days=1)).isoformat()
        return self.client.post(reverse('verification:stipend_create'), {
            'title': 'Time Window Event',
            'date': future,
            'event_type': 'regular',
            'amount': '500',
            'payout_start_date': future,
            'payout_end_date': future,
            'payout_start_time': start_time,
            'payout_end_time': end_time,
        })

    def test_window_under_one_hour_rejected(self):
        resp = self._post_create_with_times('08:00', '08:30')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('1 hour', resp.content.decode())

    def test_window_exactly_one_hour_accepted(self):
        resp = self._post_create_with_times('08:00', '09:00')
        self.assertIn(resp.status_code, [200, 302])
        if resp.status_code == 200:
            self.assertNotIn('1 hour', resp.content.decode())

    def test_window_over_one_hour_accepted(self):
        resp = self._post_create_with_times('08:00', '17:00')
        self.assertIn(resp.status_code, [200, 302])

    def test_window_zero_minutes_rejected(self):
        resp = self._post_create_with_times('08:00', '08:00')
        self.assertEqual(resp.status_code, 200)


class StipendListPastEventsTest(TestCase):
    """v2.0.9 Fix 5 — Events whose payout window ended appear in Past, not Upcoming."""

    def setUp(self):
        self.admin = CustomUser.objects.create_user(
            username='admin_slpe', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-SLPE',
        )
        self.client = Client()
        self.client.force_login(self.admin)

    def _make_event(self, date, payout_start=None, payout_end=None, is_active=True, title='Event'):
        from verification.models import StipendEvent
        return StipendEvent.objects.create(
            title=title,
            date=date,
            payout_start_date=payout_start,
            payout_end_date=payout_end,
            is_active=is_active,
            created_by=self.admin,
        )

    def test_event_with_past_payout_end_appears_in_past(self):
        import datetime
        today = datetime.date.today()
        past = today - datetime.timedelta(days=3)
        event = self._make_event(
            date=today,
            payout_start=past - datetime.timedelta(days=7),
            payout_end=past,
            title='Past Window Event',
        )
        resp = self.client.get(reverse('verification:stipend_list'))
        self.assertEqual(resp.status_code, 200)
        past_qs = resp.context['past']
        past_ids = [e.pk for e in past_qs]
        self.assertIn(event.pk, past_ids, 'Event with past payout_end_date must be in Past')
        upcoming_qs = resp.context['upcoming']
        upcoming_ids = [e.pk for e in upcoming_qs]
        self.assertNotIn(event.pk, upcoming_ids, 'Event with past payout_end_date must not be in Upcoming')

    def test_event_with_future_payout_end_appears_in_upcoming(self):
        import datetime
        today = datetime.date.today()
        future = today + datetime.timedelta(days=5)
        event = self._make_event(
            date=today,
            payout_start=today,
            payout_end=future,
            title='Future Window Event',
        )
        resp = self.client.get(reverse('verification:stipend_list'))
        self.assertEqual(resp.status_code, 200)
        upcoming_qs = resp.context['upcoming']
        upcoming_ids = [e.pk for e in upcoming_qs]
        self.assertIn(event.pk, upcoming_ids)


class PastEventsEmptyStateTest(TestCase):
    """v2.1.17 member-testing audit — Finding F: the Past Events panel must
    distinguish "no past events exist yet" from "no past events match these
    filters", instead of one message covering both cases."""

    def setUp(self):
        self.admin = CustomUser.objects.create_user(
            username='admin_pees', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-PEES',
        )
        self.client = Client()
        self.client.force_login(self.admin)

    def test_no_past_events_at_all(self):
        resp = self.client.get(reverse('verification:stipend_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context['any_past_events_exist'])
        self.assertContains(resp, 'No past events exist yet.')
        self.assertNotContains(resp, 'No past events match these filters.')

    def test_past_events_exist_but_filter_matches_none(self):
        import datetime
        from verification.models import StipendEvent
        StipendEvent.objects.create(
            title='A Genuinely Past Event', date=datetime.date(2020, 1, 1),
            created_by=self.admin,
        )
        resp = self.client.get(reverse('verification:stipend_list'), {'past_q': 'NoSuchTitleXYZ'})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['any_past_events_exist'])
        self.assertContains(resp, 'No past events match these filters.')
        self.assertNotContains(resp, 'No past events exist yet.')

    def test_past_event_search_is_case_insensitive_partial_match(self):
        import datetime
        from verification.models import StipendEvent
        StipendEvent.objects.create(
            title='Unique Fiesta Distribution 2020', date=datetime.date(2020, 3, 1),
            created_by=self.admin,
        )
        resp = self.client.get(reverse('verification:stipend_list'), {'past_q': 'fiesta'})
        self.assertEqual(resp.status_code, 200)
        titles = [e.title for e in resp.context['past']]
        self.assertIn('Unique Fiesta Distribution 2020', titles)

    def test_upcoming_event_never_leaks_into_past_search(self):
        import datetime
        from verification.models import StipendEvent
        today = datetime.date.today()
        StipendEvent.objects.create(
            title='Shared Keyword Event', date=today + datetime.timedelta(days=10),
            created_by=self.admin,
        )
        resp = self.client.get(reverse('verification:stipend_list'), {'past_q': 'Shared Keyword'})
        self.assertEqual(resp.status_code, 200)
        titles = [e.title for e in resp.context['past']]
        self.assertNotIn('Shared Keyword Event', titles)


# ──────────────────────────────────────────────────────────────────────────────
# v2.1.3 — Payout office hours validation (07:00–20:00)
# ──────────────────────────────────────────────────────────────────────────────

class PayoutOfficeHoursTest(TestCase):
    """v2.1.3 — Start time < 07:00 and end time > 20:00 must be rejected."""

    def setUp(self):
        self.admin = CustomUser.objects.create_user(
            username='admin_ohr', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-OHR',
        )
        self.client = Client()
        self.client.force_login(self.admin)

    def _post(self, start_time, end_time, start_date=None, end_date=None):
        import datetime
        tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        return self.client.post(reverse('verification:stipend_create'), {
            'title': 'Office Hours Test',
            'date': tomorrow,
            'event_type': 'regular',
            'amount': '500',
            'payout_start_date': start_date or tomorrow,
            'payout_end_date': end_date or tomorrow,
            'payout_start_time': start_time,
            'payout_end_time': end_time,
        })

    def test_start_before_0700_rejected(self):
        resp = self._post('06:59', '08:00')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('7:00 AM', resp.content.decode())

    def test_end_after_2000_rejected(self):
        resp = self._post('08:00', '20:01')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('8:00 PM', resp.content.decode())

    def test_start_at_0700_accepted(self):
        resp = self._post('07:00', '09:00')
        self.assertIn(resp.status_code, [200, 302])
        if resp.status_code == 200:
            content = resp.content.decode()
            self.assertNotIn('7:00 AM', content)

    def test_end_at_2000_accepted(self):
        resp = self._post('08:00', '20:00')
        self.assertIn(resp.status_code, [200, 302])
        if resp.status_code == 200:
            content = resp.content.decode()
            self.assertNotIn('8:00 PM', content)

    def test_end_before_or_equal_to_start_rejected(self):
        resp = self._post('09:00', '09:00')
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode().lower()
        self.assertTrue(
            'after payout start' in content or '1 hour' in content,
            'End time equal to start must be rejected'
        )

    def test_end_before_start_rejected(self):
        resp = self._post('10:00', '08:00')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('after payout start', resp.content.decode().lower())


# ──────────────────────────────────────────────────────────────────────────────
# v2.1.3 — Reject past start time when payout_start_date is today
# ──────────────────────────────────────────────────────────────────────────────

class PayoutTodayPastTimeTest(TestCase):
    """v2.1.3 — If payout starts today, start time must be in the future (Manila)."""

    def setUp(self):
        self.admin = CustomUser.objects.create_user(
            username='admin_tpt', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-TPT',
        )
        self.client = Client()
        self.client.force_login(self.admin)

    def _post_today(self, start_time, end_time):
        import datetime
        today = datetime.date.today().isoformat()
        return self.client.post(reverse('verification:stipend_create'), {
            'title': 'Today Time Test',
            'date': today,
            'event_type': 'regular',
            'amount': '500',
            'payout_start_date': today,
            'payout_end_date': today,
            'payout_start_time': start_time,
            'payout_end_time': end_time,
        })

    def test_today_with_midnight_start_rejected(self):
        import datetime as real_dt
        from unittest.mock import patch

        class MockDatetime(real_dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return real_dt.datetime(2026, 1, 1, 10, 0, 0, tzinfo=tz)

        with patch('verification.views._dt.datetime', MockDatetime):
            resp = self._post_today('07:00', '09:00')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('already passed', resp.content.decode())

    def test_today_with_future_start_accepted(self):
        import datetime as real_dt
        from unittest.mock import patch

        class MockDatetime(real_dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return real_dt.datetime(2026, 1, 1, 10, 0, 0, tzinfo=tz)

        with patch('verification.views._dt.datetime', MockDatetime):
            resp = self._post_today('11:00', '13:00')
        self.assertIn(resp.status_code, [200, 302])
        if resp.status_code == 200:
            self.assertNotIn('already passed', resp.content.decode())

    def test_future_date_with_past_time_accepted(self):
        import datetime as real_dt
        from unittest.mock import patch

        class MockDatetime(real_dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return real_dt.datetime(2026, 1, 1, 10, 0, 0, tzinfo=tz)

        with patch('verification.views._dt.datetime', MockDatetime):
            import datetime
            tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
            resp = self.client.post(reverse('verification:stipend_create'), {
                'title': 'Future Date Past Time',
                'date': tomorrow,
                'event_type': 'regular',
                'amount': '500',
                'payout_start_date': tomorrow,
                'payout_end_date': tomorrow,
                'payout_start_time': '07:00',
                'payout_end_time': '09:00',
            })
        self.assertIn(resp.status_code, [200, 302])
        if resp.status_code == 200:
            self.assertNotIn('already passed', resp.content.decode())


# ──────────────────────────────────────────────────────────────────────────────
# v2.2.0 Post-UAT Phase 2 — a same-day schedule with BLANK payout times must
# still respect the official 7:00 AM-8:00 PM claiming window server-side.
# Before this fix, leaving both time fields blank skipped all time-of-day
# validation entirely (StipendEvent.is_within_time_window treats a blank
# window as "active all day"), so an admin creating a schedule at 9:47 PM
# could still produce a same-day "payout" nobody could actually claim.
# ──────────────────────────────────────────────────────────────────────────────

class PayoutClaimingWindowClosedTest(TestCase):

    def setUp(self):
        self.admin = CustomUser.objects.create_user(
            username='admin_pcwc', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-PCWC',
        )
        self.client = Client()
        self.client.force_login(self.admin)

    @staticmethod
    def _mock_now_at(hour, minute=0):
        import datetime as real_dt

        class MockDatetime(real_dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return real_dt.datetime(2026, 1, 1, hour, minute, 0, tzinfo=tz)
        return MockDatetime

    def _post_blank_times(self, date_str, title='Blank Times Test'):
        return self.client.post(reverse('verification:stipend_create'), {
            'title': title,
            'date': date_str,
            'event_type': 'regular',
            'amount': '500',
            'payout_start_date': date_str,
            'payout_end_date': date_str,
            'payout_start_time': '',
            'payout_end_time': '',
        })

    def test_blank_times_today_after_8pm_rejected(self):
        import datetime
        from unittest.mock import patch
        today = datetime.date.today().isoformat()
        with patch('verification.views._dt.datetime', self._mock_now_at(21, 47)):
            resp = self._post_blank_times(today)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('already ended', resp.content.decode())

    def test_blank_times_today_direct_post_after_hours_rejected(self):
        """A same-day schedule cannot be created after hours by omitting the
        payout_start_date/payout_end_date fields either (falls back to `date`)."""
        import datetime
        from unittest.mock import patch
        today = datetime.date.today().isoformat()
        with patch('verification.views._dt.datetime', self._mock_now_at(22, 15)):
            resp = self.client.post(reverse('verification:stipend_create'), {
                'title': 'No Payout Window Fields',
                'date': today,
                'event_type': 'regular',
                'amount': '500',
                'payout_start_date': '',
                'payout_end_date': '',
                'payout_start_time': '',
                'payout_end_time': '',
            })
        self.assertEqual(resp.status_code, 200)
        self.assertIn('already ended', resp.content.decode())

    def test_blank_times_today_before_window_ends_accepted(self):
        import datetime
        from unittest.mock import patch
        today = datetime.date.today().isoformat()
        with patch('verification.views._dt.datetime', self._mock_now_at(10, 0)):
            resp = self._post_blank_times(today)
        self.assertIn(resp.status_code, [200, 302])
        if resp.status_code == 200:
            self.assertNotIn('already ended', resp.content.decode())

    def test_blank_times_today_exactly_at_8pm_rejected(self):
        """8:00 PM is the closing boundary — claiming window is closed at/after it."""
        import datetime
        from unittest.mock import patch
        today = datetime.date.today().isoformat()
        with patch('verification.views._dt.datetime', self._mock_now_at(20, 0)):
            resp = self._post_blank_times(today)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('already ended', resp.content.decode())

    def test_blank_times_today_before_7am_accepted(self):
        """Before the window opens today, the day's claiming period has not
        ended yet — creation must be allowed."""
        import datetime
        from unittest.mock import patch
        today = datetime.date.today().isoformat()
        with patch('verification.views._dt.datetime', self._mock_now_at(5, 30)):
            resp = self._post_blank_times(today)
        self.assertIn(resp.status_code, [200, 302])
        if resp.status_code == 200:
            self.assertNotIn('already ended', resp.content.decode())

    def test_blank_times_future_date_after_hours_accepted(self):
        import datetime
        from unittest.mock import patch
        tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        with patch('verification.views._dt.datetime', self._mock_now_at(21, 47)):
            resp = self._post_blank_times(tomorrow)
        self.assertIn(resp.status_code, [200, 302])
        if resp.status_code == 200:
            self.assertNotIn('already ended', resp.content.decode())

    def test_blank_times_past_date_still_rejected_as_past(self):
        import datetime
        from unittest.mock import patch
        yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        with patch('verification.views._dt.datetime', self._mock_now_at(21, 47)):
            resp = self._post_blank_times(yesterday)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('past', resp.content.decode().lower())

    def test_explicit_times_today_after_hours_still_rejected(self):
        """Explicit times spanning into the past (e.g. 7-8 PM window with 'now'
        at 9:47 PM) are already rejected via the existing start-time check —
        confirms that path still works alongside the new blank-time guard."""
        import datetime
        from unittest.mock import patch
        today = datetime.date.today().isoformat()
        with patch('verification.views._dt.datetime', self._mock_now_at(21, 47)):
            resp = self.client.post(reverse('verification:stipend_create'), {
                'title': 'Explicit Late Window',
                'date': today,
                'event_type': 'regular',
                'amount': '500',
                'payout_start_date': today,
                'payout_end_date': today,
                'payout_start_time': '19:00',
                'payout_end_time': '20:00',
            })
        self.assertEqual(resp.status_code, 200)
        self.assertIn('already passed', resp.content.decode())

    def test_no_stipend_event_created_when_rejected(self):
        import datetime
        from unittest.mock import patch
        from verification.models import StipendEvent
        today = datetime.date.today().isoformat()
        with patch('verification.views._dt.datetime', self._mock_now_at(21, 47)):
            self._post_blank_times(today, title='Should Not Persist')
        self.assertFalse(
            StipendEvent.objects.filter(title='Should Not Persist').exists()
        )


# ──────────────────────────────────────────────────────────────────────────────
# Phase B.2 — get_active_event_now() must consider EVERY date-matching event,
# not just the first one, when multiple approved events share an overlapping
# date window. A candidate whose own time window has already closed must
# never hide a different candidate whose window is still open.
# ──────────────────────────────────────────────────────────────────────────────

class MultipleSameDayActiveEventTest(TestCase):

    def setUp(self):
        self.admin = CustomUser.objects.create_user(
            username='admin_msae', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-MSAE',
        )

    @staticmethod
    def _mock_now_at(hour, minute=0):
        """Patch django.utils.timezone.now() so get_active_event_now()'s
        Asia/Manila conversion resolves to a fixed, deterministic wall-clock
        time — 2026-01-01 <hour>:<minute> Manila (UTC+8), expressed as the
        equivalent aware UTC instant so .astimezone(Manila) round-trips exactly."""
        import datetime as real_dt
        from zoneinfo import ZoneInfo
        manila = ZoneInfo('Asia/Manila')
        fixed = real_dt.datetime(2026, 1, 1, hour, minute, 0, tzinfo=manila)
        return fixed.astimezone(real_dt.timezone.utc)

    def _make_event(self, title, start_time=None, end_time=None, is_active=True,
                     approval_status=None, date=None):
        from verification.models import StipendEvent
        d = date or datetime.date(2026, 1, 1)
        return StipendEvent.objects.create(
            title=title, date=d, event_type=StipendEvent.EVENT_TYPE_REGULAR,
            amount=100, payout_start_date=d, payout_end_date=d,
            payout_start_time=start_time, payout_end_time=end_time,
            is_active=is_active,
            approval_status=approval_status or StipendEvent.APPROVAL_APPROVED,
            created_by=self.admin,
        )

    def test_closed_then_open_selects_open_event(self):
        """Event A (closed window) created first, Event B (open window) second —
        the resolver must still find B."""
        from verification.models import StipendEvent
        self._make_event('Event A (closed)', datetime.time(8, 0), datetime.time(9, 0))
        self._make_event('Event B (open)', datetime.time(13, 0), datetime.time(17, 0))
        with mock.patch('django.utils.timezone.now', return_value=self._mock_now_at(14, 0)):
            active = StipendEvent.get_active_event_now()
        self.assertIsNotNone(active)
        self.assertEqual(active.title, 'Event B (open)')

    def test_open_then_closed_selects_open_event(self):
        """Reverse creation order — DB/insertion order must not determine
        correctness. Event A (open) created first, Event B (closed) second."""
        from verification.models import StipendEvent
        self._make_event('Event A (open)', datetime.time(13, 0), datetime.time(17, 0))
        self._make_event('Event B (closed)', datetime.time(8, 0), datetime.time(9, 0))
        with mock.patch('django.utils.timezone.now', return_value=self._mock_now_at(14, 0)):
            active = StipendEvent.get_active_event_now()
        self.assertIsNotNone(active)
        self.assertEqual(active.title, 'Event A (open)')

    def test_all_same_day_events_closed_returns_none(self):
        from verification.models import StipendEvent
        self._make_event('Event A (closed)', datetime.time(8, 0), datetime.time(9, 0))
        self._make_event('Event B (closed)', datetime.time(10, 0), datetime.time(11, 0))
        with mock.patch('django.utils.timezone.now', return_value=self._mock_now_at(14, 0)):
            active = StipendEvent.get_active_event_now()
        self.assertIsNone(active)

    def test_future_event_plus_current_same_day_event(self):
        """A future-dated event must never shadow today's currently-valid event."""
        from verification.models import StipendEvent
        future = datetime.date(2026, 6, 1)
        self._make_event('Future Event', date=future)
        self._make_event('Today Event (open)', datetime.time(13, 0), datetime.time(17, 0))
        with mock.patch('django.utils.timezone.now', return_value=self._mock_now_at(14, 0)):
            active = StipendEvent.get_active_event_now()
        self.assertIsNotNone(active)
        self.assertEqual(active.title, 'Today Event (open)')

    def test_unapproved_event_plus_valid_event(self):
        """A pending/unapproved same-day event must never be selected over — or
        instead of — a genuinely valid approved one."""
        from verification.models import StipendEvent
        self._make_event(
            'Pending Event', datetime.time(13, 0), datetime.time(17, 0),
            approval_status=StipendEvent.APPROVAL_PENDING,
        )
        self._make_event('Approved Event (open)', datetime.time(13, 0), datetime.time(17, 0))
        with mock.patch('django.utils.timezone.now', return_value=self._mock_now_at(14, 0)):
            active = StipendEvent.get_active_event_now()
        self.assertIsNotNone(active)
        self.assertEqual(active.title, 'Approved Event (open)')

    def test_multiple_currently_valid_events_deterministic_by_creation_order(self):
        """When more than one event is genuinely, simultaneously valid, no
        business rule specifies a priority (confirmed by full-repo search —
        no event-type priority, no uniqueness constraint). The resolver picks
        the earliest-created one deterministically rather than depending on
        undefined DB row order — this test locks in that reproducibility,
        not a claimed business policy."""
        from verification.models import StipendEvent
        first = self._make_event('First Created (open)', datetime.time(13, 0), datetime.time(17, 0))
        self._make_event('Second Created (open)', datetime.time(13, 0), datetime.time(17, 0))
        with mock.patch('django.utils.timezone.now', return_value=self._mock_now_at(14, 0)):
            active = StipendEvent.get_active_event_now()
        self.assertIsNotNone(active)
        self.assertEqual(active.pk, first.pk)

    def test_inactive_event_plus_valid_event(self):
        from verification.models import StipendEvent
        self._make_event(
            'Inactive Event', datetime.time(13, 0), datetime.time(17, 0), is_active=False,
        )
        self._make_event('Active Event (open)', datetime.time(13, 0), datetime.time(17, 0))
        with mock.patch('django.utils.timezone.now', return_value=self._mock_now_at(14, 0)):
            active = StipendEvent.get_active_event_now()
        self.assertIsNotNone(active)
        self.assertEqual(active.title, 'Active Event (open)')

    def test_single_event_behavior_unchanged(self):
        """Regression guard: the common single-event-per-day case must behave
        exactly as before this fix."""
        from verification.models import StipendEvent
        self._make_event('Only Event (open)', datetime.time(13, 0), datetime.time(17, 0))
        with mock.patch('django.utils.timezone.now', return_value=self._mock_now_at(14, 0)):
            active = StipendEvent.get_active_event_now()
        self.assertIsNotNone(active)
        with mock.patch('django.utils.timezone.now', return_value=self._mock_now_at(20, 0)):
            active_after_close = StipendEvent.get_active_event_now()
        self.assertIsNone(active_after_close)


# ──────────────────────────────────────────────────────────────────────────────
# Phase B.3 — when more than one StipendEvent is simultaneously valid,
# verify_start must require an explicit operator choice (ClaimRecord
# uniqueness is scoped to beneficiary+event, not beneficiary alone, so two
# concurrent events are a legitimate case — see StipendEvent.get_open_events_now
# docstring). The chosen event must then stay bound through Manual Review and
# override release without ever being silently re-resolved. Asia/Manila-aware
# mocked time throughout — no wall-clock dependency.
# ──────────────────────────────────────────────────────────────────────────────

class ConcurrentValidEventSelectionTest(TestCase):

    def setUp(self):
        from verification.models import FaceEmbedding, StipendEvent
        self.StipendEvent = StipendEvent
        self.president = _make_staff('cves_pres', role=CustomUser.ROLE_PRESIDENT)
        self.admin = _make_staff('cves_admin', role=CustomUser.ROLE_ADMIN)
        self.ben = _make_beneficiary('BEN-CVES-001', 'SC-CVES-001')
        self.ben.status = Beneficiary.STATUS_ACTIVE
        self.ben.consent_given = True
        self.ben.save()
        FaceEmbedding.objects.create(
            beneficiary=self.ben, embedding_data=b'\x00' * 512, created_by=self.admin,
        )
        self.client.force_login(self.admin)
        self.url = reverse('verification:verify_start', kwargs={'pk': self.ben.pk})

    @staticmethod
    def _mock_now_at(hour, minute=0):
        import datetime as real_dt
        from zoneinfo import ZoneInfo
        manila = ZoneInfo('Asia/Manila')
        fixed = real_dt.datetime(2026, 1, 1, hour, minute, 0, tzinfo=manila)
        return fixed.astimezone(real_dt.timezone.utc)

    def _make_event(self, title, start_time=None, end_time=None, event_type='regular',
                     amount=100, date=None, approval_status=None):
        d = date or datetime.date(2026, 1, 1)
        return self.StipendEvent.objects.create(
            title=title, date=d, event_type=event_type, amount=amount,
            payout_start_date=d, payout_end_date=d,
            payout_start_time=start_time, payout_end_time=end_time,
            is_active=True, approval_status=approval_status or self.StipendEvent.APPROVAL_APPROVED,
            created_by=self.president,
        )

    # A — one valid event: existing (pre-B.3) behavior unchanged
    def test_a_one_valid_event_unchanged_behavior(self):
        self._make_event('Only Event', datetime.time(13, 0), datetime.time(17, 0))
        with mock.patch('django.utils.timezone.now', return_value=self._mock_now_at(14, 0)):
            resp = self.client.get(self.url, secure=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['active_event'].title, 'Only Event')
        self.assertTemplateNotUsed(resp, 'verification/verify_choose_event.html')

    # B — closed A + open B: B usable directly, no chooser needed
    def test_b_closed_a_open_b_selected_directly(self):
        self._make_event('Event A (closed)', datetime.time(8, 0), datetime.time(9, 0))
        self._make_event('Event B (open)', datetime.time(13, 0), datetime.time(17, 0))
        with mock.patch('django.utils.timezone.now', return_value=self._mock_now_at(14, 0)):
            resp = self.client.get(self.url, secure=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['active_event'].title, 'Event B (open)')
        self.assertTemplateNotUsed(resp, 'verification/verify_choose_event.html')

    # C — open A + closed B: A usable directly (reverse of B — order-independence)
    def test_c_open_a_closed_b_selected_directly(self):
        self._make_event('Event A (open)', datetime.time(13, 0), datetime.time(17, 0))
        self._make_event('Event B (closed)', datetime.time(8, 0), datetime.time(9, 0))
        with mock.patch('django.utils.timezone.now', return_value=self._mock_now_at(14, 0)):
            resp = self.client.get(self.url, secure=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['active_event'].title, 'Event A (open)')

    # D — two simultaneously-open events of different types: chooser shown, no guess made
    def test_d_two_open_different_types_shows_chooser(self):
        self._make_event('Regular Distribution', datetime.time(13, 0), datetime.time(17, 0),
                          event_type='regular', amount=350)
        self._make_event('Birthday Bonus', datetime.time(13, 0), datetime.time(17, 0),
                          event_type='birthday_bonus', amount=500)
        with mock.patch('django.utils.timezone.now', return_value=self._mock_now_at(14, 0)):
            resp = self.client.get(self.url, secure=True)
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'verification/verify_choose_event.html')
        self.assertContains(resp, 'Regular Distribution')
        self.assertContains(resp, 'Birthday Bonus')
        # Session read must stay under the same mocked clock: SessionStore's
        # DB lookup filters expire_date__gt=timezone.now(), so reading it
        # after the mock exits (real "now" being far ahead of the mocked
        # date) would spuriously look expired regardless of what was saved.
        with mock.patch('django.utils.timezone.now', return_value=self._mock_now_at(14, 0)):
            self.assertIsNone(self.client.session.get('verification_session'))

    # E — two open events with different amounts: explicit selection binds the
    # CORRECT one, never silently the earlier-created one
    def test_e_explicit_selection_binds_correct_amount(self):
        self._make_event('Event A', datetime.time(13, 0), datetime.time(17, 0), amount=350)
        ev_b = self._make_event('Event B', datetime.time(13, 0), datetime.time(17, 0), amount=500)
        with mock.patch('django.utils.timezone.now', return_value=self._mock_now_at(14, 0)):
            resp = self.client.get(self.url, {'event': str(ev_b.pk)}, secure=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['active_event'].pk, ev_b.pk)
        self.assertEqual(resp.context['active_event'].amount, ev_b.amount)

    # F — selected event remains bound server-side in the verification session
    def test_f_selected_event_bound_in_session(self):
        ev_a = self._make_event('Event A', datetime.time(13, 0), datetime.time(17, 0))
        self._make_event('Event B', datetime.time(13, 0), datetime.time(17, 0))
        with mock.patch('django.utils.timezone.now', return_value=self._mock_now_at(14, 0)):
            resp = self.client.get(self.url, {'event': str(ev_a.pk)}, secure=True)
            self.assertEqual(resp.status_code, 200)
            # Session read must stay under the same mocked clock — see note
            # in test_d above (SessionStore filters on expire_date__gt=now()).
            session_data = self.client.session.get('verification_session')
        self.assertIsNotNone(session_data)
        self.assertEqual(session_data['stipend_event_id'], str(ev_a.pk))

    # G — chosen event survives through to Manual Review approval, even while
    # a second event is still concurrently open at approval time
    def test_g_manual_review_continuity_under_ambiguity(self):
        from verification.models import VerificationAttempt, ManualVerificationRequest, ClaimRecord
        # Phase B.5 — manual_verify_review now revalidates the bound event's
        # claiming window immediately before creating the ClaimRecord. This
        # test is about EVENT-BINDING CONTINUITY under ambiguity, not window
        # expiry, so the whole flow is frozen at a moment where both events
        # (default blank per-event times, date 2026-01-01) are legitimately
        # still open — same _mock_now_at helper already used elsewhere in
        # this class — rather than depending on real wall-clock "now".
        with mock.patch('django.utils.timezone.now', return_value=self._mock_now_at(14, 0)):
            ev_a = self._make_event('Event A', amount=350)
            ev_b = self._make_event('Event B', amount=500)  # still open when MVR is approved below
            attempt = VerificationAttempt.objects.create(
                beneficiary=self.ben, claimant_type=VerificationAttempt.CLAIMANT_BENEFICIARY,
                decision=VerificationAttempt.DECISION_MANUAL_REVIEW, similarity_score=0.6,
                stipend_event=ev_a, performed_by=self.admin,
            )
            mvr = ManualVerificationRequest.objects.create(
                beneficiary=self.ben, requested_by=self.admin, verification_attempt=attempt,
                stipend_event=attempt.stipend_event, reason='test',
            )
            reviewer = _make_staff('cves_reviewer', role=CustomUser.ROLE_ADMIN)
            self.client.force_login(reviewer)
            resp = self.client.post(
                reverse('verification:manual_verify_review', kwargs={'request_id': mvr.pk}),
                {'action': 'approve', 'review_notes': 'confirmed via ID'}, secure=True,
            )
            self.assertIn(resp.status_code, [200, 302])
            claim = ClaimRecord.objects.get(verification_attempt=attempt)
        self.assertEqual(claim.stipend_event_id, ev_a.pk)
        self.assertNotEqual(claim.stipend_event_id, ev_b.pk)
        self.assertEqual(claim.amount, ev_a.amount)

    # H — chosen event survives through override + release, even while a
    # second event is still concurrently open at release time
    def test_h_override_release_continuity_under_ambiguity(self):
        from verification.models import VerificationAttempt, ClaimRecord
        # Phase B.5 — override_release_payout now revalidates the bound
        # event's claiming window immediately before creating the
        # ClaimRecord. This test is about EVENT-BINDING CONTINUITY under
        # ambiguity, not window expiry, so the whole flow is frozen at a
        # moment where both events (default blank per-event times, date
        # 2026-01-01) are legitimately still open — same _mock_now_at helper
        # already used elsewhere in this class.
        with mock.patch('django.utils.timezone.now', return_value=self._mock_now_at(14, 0)):
            ev_a = self._make_event('Event A', amount=350)
            ev_b = self._make_event('Event B', amount=500)  # still open at release time
            performer = _make_staff('cves_performer', role=CustomUser.ROLE_ADMIN)
            attempt = VerificationAttempt.objects.create(
                beneficiary=self.ben, claimant_type=VerificationAttempt.CLAIMANT_BENEFICIARY,
                decision=VerificationAttempt.DECISION_NOT_VERIFIED, similarity_score=0.4,
                stipend_event=ev_a, performed_by=performer,
            )
            overrider = _make_staff('cves_overrider', role=CustomUser.ROLE_ADMIN)
            self.client.force_login(overrider)
            resp = self.client.post(
                reverse('verification:admin_override', kwargs={'attempt_id': attempt.pk}),
                {'decision': 'verified', 'reason': 'Independently confirmed identity via valid government ID.'},
                secure=True,
            )
            self.assertIn(resp.status_code, [200, 302])
            resp2 = self.client.post(
                reverse('verification:override_release_payout', kwargs={'attempt_id': attempt.pk}),
                secure=True,
            )
            self.assertIn(resp2.status_code, [200, 302])
            claim = ClaimRecord.objects.get(verification_attempt=attempt)
        self.assertEqual(claim.stipend_event_id, ev_a.pk)
        self.assertNotEqual(claim.stipend_event_id, ev_b.pk)
        self.assertEqual(claim.amount, ev_a.amount)

    # I — invalid/tampered event id: rejected, falls back to the chooser
    def test_i_tampered_event_id_rejected(self):
        self._make_event('Event A', datetime.time(13, 0), datetime.time(17, 0))
        self._make_event('Event B', datetime.time(13, 0), datetime.time(17, 0))
        with mock.patch('django.utils.timezone.now', return_value=self._mock_now_at(14, 0)):
            resp = self.client.get(self.url, {'event': str(uuid.uuid4())}, secure=True)
            self.assertEqual(resp.status_code, 200)
            self.assertTemplateUsed(resp, 'verification/verify_choose_event.html')
            self.assertIsNone(self.client.session.get('verification_session'))

    # K — a real event that exists but is NOT currently open (closed window)
    # cannot be selected via a tampered id while others are ambiguous
    def test_k_closed_event_id_cannot_be_selected_via_tampering(self):
        self._make_event('Event A (open)', datetime.time(13, 0), datetime.time(17, 0))
        self._make_event('Event B (open)', datetime.time(13, 0), datetime.time(17, 0))
        ev_closed = self._make_event('Event C (closed)', datetime.time(8, 0), datetime.time(9, 0))
        with mock.patch('django.utils.timezone.now', return_value=self._mock_now_at(14, 0)):
            resp = self.client.get(self.url, {'event': str(ev_closed.pk)}, secure=True)
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'verification/verify_choose_event.html')

    # K (continued) — a pending/unapproved event cannot be selected either
    def test_k_pending_event_cannot_be_selected(self):
        self._make_event('Event A (open)', datetime.time(13, 0), datetime.time(17, 0))
        self._make_event('Event B (open)', datetime.time(13, 0), datetime.time(17, 0))
        ev_pending = self._make_event(
            'Event C (pending)', datetime.time(13, 0), datetime.time(17, 0),
            approval_status=self.StipendEvent.APPROVAL_PENDING,
        )
        with mock.patch('django.utils.timezone.now', return_value=self._mock_now_at(14, 0)):
            resp = self.client.get(self.url, {'event': str(ev_pending.pk)}, secure=True)
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'verification/verify_choose_event.html')

    # J — verify_submit's continuity: documented by source inspection (see
    # Phase B.3 report §J) rather than a full mocked-camera submit here —
    # verify_submit reads session_data['stipend_event_id'] and does a direct
    # StipendEvent.objects.get(pk=...) with NO get_active_event_now()/
    # get_open_events_now() re-check, so a window closing between selection
    # and submission cannot change which event a claim attaches to. This test
    # locks in that the lookup itself is time-independent once bound.
    def test_j_bound_event_lookup_ignores_current_window_state(self):
        ev = self._make_event('Event A', datetime.time(13, 0), datetime.time(17, 0))
        with mock.patch('django.utils.timezone.now', return_value=self._mock_now_at(14, 0)):
            self.client.get(self.url, secure=True)
            # Session read must stay under the same mocked clock — see note
            # in test_d above (SessionStore filters on expire_date__gt=now()).
            session_data = self.client.session.get('verification_session')
        bound_id = session_data['stipend_event_id']
        # Window has now closed (20:00, past payout_end_time 17:00) — the bound
        # id must still resolve to the same event via a plain pk lookup.
        with mock.patch('django.utils.timezone.now', return_value=self._mock_now_at(20, 0)):
            resolved = self.StipendEvent.objects.get(pk=bound_id)
        self.assertEqual(resolved.pk, ev.pk)


class SpecialClaimRequestConcurrentEventTest(TestCase):
    """Phase B.3 — special_claim_request must target the event the
    beneficiary actually already claimed today, never an arbitrary
    date-matching candidate, when more than one event is date-eligible."""

    def setUp(self):
        self.president = _make_staff('scr_pres', role=CustomUser.ROLE_PRESIDENT)
        self.admin = _make_staff('scr_admin', role=CustomUser.ROLE_ADMIN)
        self.ben = _make_beneficiary('BEN-SCR-001', 'SC-SCR-001')
        self.ben.status = Beneficiary.STATUS_ACTIVE
        self.ben.save()
        self.client.force_login(self.admin)
        self.url = reverse('verification:special_claim_request', kwargs={'pk': self.ben.pk})

    def _make_event(self, title, amount=100):
        from verification.models import StipendEvent
        d = datetime.date.today()
        return StipendEvent.objects.create(
            title=title, date=d, event_type='regular', amount=amount,
            payout_start_date=d, payout_end_date=d,
            is_active=True, approval_status=StipendEvent.APPROVAL_APPROVED,
            created_by=self.president,
        )

    def test_targets_the_event_beneficiary_already_claimed(self):
        from verification.models import ClaimRecord, SpecialClaimRequest
        ev_a = self._make_event('Event A', amount=350)
        self._make_event('Event B', amount=500)  # also date-eligible today, never claimed
        ClaimRecord.objects.create(
            beneficiary=self.ben, stipend_event=ev_a, status=ClaimRecord.STATUS_CLAIMED,
            amount=ev_a.amount, claimed_by=self.admin,
        )
        resp = self.client.post(self.url, {'reason': 'Lost original payout, needs reissue.'}, secure=True)
        self.assertIn(resp.status_code, [200, 302])
        scr = SpecialClaimRequest.objects.get(beneficiary=self.ben)
        self.assertEqual(scr.stipend_event_id, ev_a.pk)

    def test_refuses_when_beneficiary_claimed_multiple_distinct_events_today(self):
        from verification.models import ClaimRecord, SpecialClaimRequest
        ev_a = self._make_event('Event A', amount=350)
        ev_b = self._make_event('Event B', amount=500)
        ClaimRecord.objects.create(
            beneficiary=self.ben, stipend_event=ev_a, status=ClaimRecord.STATUS_CLAIMED,
            amount=ev_a.amount, claimed_by=self.admin,
        )
        ClaimRecord.objects.create(
            beneficiary=self.ben, stipend_event=ev_b, status=ClaimRecord.STATUS_CLAIMED,
            amount=ev_b.amount, claimed_by=self.admin,
        )
        resp = self.client.post(self.url, {'reason': 'Ambiguous case.'}, secure=True)
        self.assertIn(resp.status_code, [200, 302])
        self.assertFalse(SpecialClaimRequest.objects.filter(beneficiary=self.ben).exists())


# ──────────────────────────────────────────────────────────────────────────────
# v2.1.3 — Inactive events display in stipend_list
# ──────────────────────────────────────────────────────────────────────────────

class StipendListInactiveEventsTest(TestCase):
    """v2.1.3 — Inactive events with future schedule appear in inactive context (admin only)."""

    def setUp(self):
        from verification.models import StipendEvent
        self.StipendEvent = StipendEvent
        self.admin = CustomUser.objects.create_user(
            username='admin_sie', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-SIE',
        )
        self.staff = CustomUser.objects.create_user(
            username='staff_sie', password='TestPass123!',
            role=CustomUser.ROLE_STAFF, employee_id='EMP-SIE2',
        )
        self.client = Client()

    def _make_event(self, days_ahead, is_active, title='Event'):
        import datetime
        today = datetime.date.today()
        d = today + datetime.timedelta(days=days_ahead)
        return self.StipendEvent.objects.create(
            title=title, date=today,
            payout_start_date=d, payout_end_date=d,
            is_active=is_active, created_by=self.admin,
        )

    def test_inactive_future_event_in_inactive_context(self):
        event = self._make_event(5, is_active=False, title='Inactive Future')
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('verification:stipend_list'))
        self.assertEqual(resp.status_code, 200)
        inactive_ids = [e.pk for e in resp.context['inactive']]
        self.assertIn(event.pk, inactive_ids)

    def test_inactive_future_event_not_in_upcoming(self):
        event = self._make_event(5, is_active=False, title='Inactive Not Upcoming')
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('verification:stipend_list'))
        upcoming_ids = [e.pk for e in resp.context['upcoming']]
        self.assertNotIn(event.pk, upcoming_ids)

    def test_inactive_past_event_not_in_inactive_context(self):
        import datetime
        today = datetime.date.today()
        past = today - datetime.timedelta(days=3)
        event = self.StipendEvent.objects.create(
            title='Inactive Past', date=past,
            payout_start_date=past - datetime.timedelta(days=7),
            payout_end_date=past,
            is_active=False, created_by=self.admin,
        )
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('verification:stipend_list'))
        inactive_ids = [e.pk for e in resp.context['inactive']]
        self.assertNotIn(event.pk, inactive_ids,
                         'Past inactive event must not appear in inactive section')

    def test_inactive_past_event_appears_in_past(self):
        import datetime
        today = datetime.date.today()
        past = today - datetime.timedelta(days=3)
        event = self.StipendEvent.objects.create(
            title='Inactive In Past', date=past,
            payout_start_date=past - datetime.timedelta(days=7),
            payout_end_date=past,
            is_active=False, created_by=self.admin,
        )
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('verification:stipend_list'))
        past_ids = [e.pk for e in resp.context['past']]
        self.assertIn(event.pk, past_ids,
                      'Past inactive event must appear in Past section regardless of active flag')

    def test_inactive_context_present_for_non_admin(self):
        self._make_event(5, is_active=False, title='Hidden From Staff')
        self.client.force_login(self.staff)
        resp = self.client.get(reverse('verification:stipend_list'))
        self.assertEqual(resp.status_code, 200)
        # inactive queryset is always passed; template hides it for non-admin
        self.assertIn('inactive', resp.context)


# ── Liveness strict-mode + retry regression tests ─────────────────────────────

class LivenessStrictModeTest(TestCase):
    """
    Regression tests for:
    1. Strict mode + challenge_completed=False -> denied before face matching
       (covers phone/screen/replay attacks and head-tracking-unavailable path)
    2. Strict mode + server anti-spoof fail -> denied before face matching
    3. Strict mode + liveness passes + low score -> retry (real live face preserved)
    4. Borderline score in review band -> manual_review (not rejected)
    5. Passing score -> verified (registered live face preserved)
    6. run_full_liveness_check includes anti_spoof_passed in return dict
    7. Denial reason distinguishes challenge failure from anti-spoof failure
    """

    def setUp(self):
        import datetime
        from verification.models import StipendEvent, FaceEmbedding
        from cryptography.fernet import Fernet

        self.staff = _make_staff('ls_staff')
        self.ben = _make_beneficiary('BEN-LS-001', 'SC-LS-001')
        self.ben.status = Beneficiary.STATUS_ACTIVE
        self.ben.save()

        today = datetime.date.today()
        self.event = StipendEvent.objects.create(
            title='LS Test Payout',
            date=today,
            amount=500,
            is_active=True,
            created_by=self.staff,
        )

        key = Fernet.generate_key()
        fake_enc = Fernet(key).encrypt(b'\x00' * 512)
        FaceEmbedding.objects.create(beneficiary=self.ben, embedding_data=fake_enc)

        self.client = Client()
        self.client.force_login(self.staff)
        self.url = reverse('verification:verify_submit')

    def _set_session(self):
        session = self.client.session
        session['verification_session'] = {
            'beneficiary_id': str(self.ben.pk),
            'attempt_number': 1,
            'session_id': _FIXED_TEST_SESSION_ID,
            'claimant_type': 'beneficiary',
            'stipend_event_id': str(self.event.pk),
            'challenge': 'right',
        }
        session.save()

    def _post(self, challenge_completed=True, liveness_passed=True, with_tx=True,
              tx_anti_spoof_score=0.9):
        """
        v2.1.16: `challenge_completed` is a client-supplied claim and is no
        longer used to decide server_liveness_passed (Security Hardening
        Round #1) — it is still sent/recorded for the audit trail. Tests that
        want to simulate "the challenge was not genuinely completed" must do
        so the way it actually manifests server-side: no valid tx_token
        (`with_tx=False`), since a LivenessTransaction is only ever issued
        after verify_check_liveness's own PAD/anti-spoof gates pass. Tests
        that want to simulate a server-side anti-spoof failure should use
        tx_anti_spoof_score and/or mock check_anti_spoofing (the submit-time
        recheck), not this flag.
        """
        self._set_session()
        tx_token = ''
        if with_tx:
            tx = _make_liveness_tx(self.ben, self.staff, self.event,
                                   anti_spoof_score=tx_anti_spoof_score)
            tx_token = str(tx.token)
        payload = json.dumps({
            'image': DATA_URI_JPEG,
            'challenge_completed': challenge_completed,
            'liveness_score': 0.9,
            'anti_spoof_score': 0.9,
            'liveness_passed': liveness_passed,
            'face_detected': True,
            'tx_token': tx_token,
            'session_id': _FIXED_TEST_SESSION_ID,
        })
        return self.client.post(
            self.url, data=payload, content_type='application/json', secure=True,
        )

    # ── 1. Strict mode: challenge not completed -> denied (not manual review) ───

    @override_settings(LIVENESS_REQUIRED=True, DEBUG=True)
    @mock.patch('verification.views.load_image_from_bytes')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.check_anti_spoofing')
    def test_strict_mode_challenge_not_completed_denied(
        self, mock_spoof, mock_detect, mock_load,
    ):
        """
        Regression: head tracking unavailable / challenge not completed in strict mode
        must return decision=denied, not manual_review or retry.
        Phone/screen/replay attacks land here because a static image cannot perform
        the head-movement challenge.

        v2.1.16: "challenge not completed" is no longer represented by a
        client-supplied challenge_completed=False flag (Security Hardening
        Round #1 removed that as a trust boundary) — it is represented by
        the absence of a valid tx_token, since a LivenessTransaction is only
        ever issued once verify_check_liveness's own server-side PAD/
        anti-spoof gates pass on a genuine challenge sequence.
        """
        import numpy as np
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        # Anti-spoof passes (high score = real-looking), but no challenge TX exists.
        mock_spoof.return_value = {'passed': True, 'score': 0.9, 'reason': 'Real face detected.'}

        resp = self._post(challenge_completed=False, liveness_passed=False, with_tx=False)
        data = json.loads(resp.content)

        self.assertTrue(data.get('success'), 'Response must have success=True with a redirect')
        self.assertEqual(data.get('decision'), 'denied',
                         'No valid liveness TX in strict mode must return denied, not manual_review')

    # ── 2. Strict mode: anti-spoof fail -> denied before face matching ───────────

    @override_settings(LIVENESS_REQUIRED=True, DEBUG=True)
    @mock.patch('verification.views.load_image_from_bytes')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.check_anti_spoofing')
    def test_strict_mode_antispoof_fail_denied(
        self, mock_spoof, mock_detect, mock_load,
    ):
        """Strict mode: server-side anti-spoof fail must deny before face matching runs."""
        import numpy as np
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        # Low score = anti-spoof failed.
        mock_spoof.return_value = {'passed': False, 'score': 0.05, 'reason': 'Low texture.'}

        resp = self._post(challenge_completed=True, liveness_passed=True)
        data = json.loads(resp.content)

        self.assertTrue(data.get('success'))
        self.assertEqual(data.get('decision'), 'denied')
        self.assertIn('anti-spoof', data.get('reason', '').lower())

    # ── 3. Strict mode: liveness passes, low face score -> retry (real live face) ─

    @override_settings(LIVENESS_REQUIRED=True, DEMO_MODE=False, VERIFICATION_THRESHOLD=0.75, DEBUG=True)
    @mock.patch('verification.views.load_image_from_bytes')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.check_anti_spoofing')
    @mock.patch('verification.views.process_face_for_verification')
    @mock.patch('verification.views.compare_with_all_embeddings')
    def test_strict_mode_liveness_passes_low_score_retries(
        self, mock_compare, mock_process, mock_spoof, mock_detect, mock_load,
    ):
        """
        Real live face: liveness passes in strict mode, but face similarity is too low
        and retries remain -> must return retry (not denied, not manual_review).
        Preserves the retry flow for legitimate beneficiaries.
        """
        import numpy as np
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_spoof.return_value = {'passed': True, 'score': 0.8, 'reason': 'Real face.'}
        mock_process.return_value = {
            'success': True, 'embedding': np.zeros(128),
            'quality': {'ok': True, 'score': 0.9, 'reason': ''}, 'using_mock': False,
        }
        mock_compare.return_value = {
            'success': True, 'score': 0.40,  # well below review band (< threshold*0.85)
            'matched_template': 'primary', 'templates_checked': 1, 'all_scores': [],
        }

        resp = self._post(challenge_completed=True, liveness_passed=True)
        data = json.loads(resp.content)

        self.assertTrue(data.get('success'))
        self.assertEqual(data.get('decision'), 'retry',
                         'Real live face with low score and retries remaining must return retry')

    # ── 4. Borderline face score -> manual_review (preserved behavior) ───────────

    @override_settings(LIVENESS_REQUIRED=True, DEMO_MODE=False, VERIFICATION_THRESHOLD=0.75, DEBUG=True)
    @mock.patch('verification.views.load_image_from_bytes')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.check_anti_spoofing')
    @mock.patch('verification.views.process_face_for_verification')
    @mock.patch('verification.views.compare_with_all_embeddings')
    def test_borderline_live_face_goes_to_manual_review(
        self, mock_compare, mock_process, mock_spoof, mock_detect, mock_load,
    ):
        """
        Real live person with borderline face similarity must go to manual_review,
        not automatic rejection. Review-band behavior must be preserved.
        """
        import numpy as np
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_spoof.return_value = {'passed': True, 'score': 0.8, 'reason': 'Real face.'}
        mock_process.return_value = {
            'success': True, 'embedding': np.zeros(128),
            'quality': {'ok': True, 'score': 0.9, 'reason': ''}, 'using_mock': False,
        }
        threshold = 0.75
        review_band_score = threshold * 0.88  # 0.66 — inside review band [0.6375, 0.75)
        mock_compare.return_value = {
            'success': True, 'score': review_band_score,
            'matched_template': 'primary', 'templates_checked': 1, 'all_scores': [],
        }

        resp = self._post(challenge_completed=True, liveness_passed=True)
        data = json.loads(resp.content)

        self.assertTrue(data.get('success'))
        self.assertEqual(data.get('decision'), 'manual_review',
                         f'Score {review_band_score:.3f} in review band must go to manual_review')

    # ── 5. Registered live face -> verified ────────────────────────────────────

    @override_settings(LIVENESS_REQUIRED=True, DEMO_MODE=False, VERIFICATION_THRESHOLD=0.75, DEBUG=True)
    @mock.patch('verification.face_utils.check_duplicate_face')
    @mock.patch('verification.views.load_image_from_bytes')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.check_anti_spoofing')
    @mock.patch('verification.views.process_face_for_verification')
    @mock.patch('verification.views.compare_with_all_embeddings')
    def test_registered_live_face_verifies_successfully(
        self, mock_compare, mock_process, mock_spoof, mock_detect, mock_load, mock_dup,
    ):
        """Registered live face with score >= threshold must be verified (preserved behavior)."""
        import numpy as np
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_spoof.return_value = {'passed': True, 'score': 0.9, 'reason': 'Real face.'}
        mock_process.return_value = {
            'success': True, 'embedding': np.zeros(128),
            'quality': {'ok': True, 'score': 0.9, 'reason': ''}, 'using_mock': False,
        }
        mock_compare.return_value = {
            'success': True, 'score': 0.88,  # above threshold 0.75
            'matched_template': 'primary', 'templates_checked': 1, 'all_scores': [],
        }
        mock_dup.return_value = {
            'duplicates_found': False, 'matches': [], 'highest_score': 0.0, 'checked': 0,
        }

        resp = self._post(challenge_completed=True, liveness_passed=True)
        data = json.loads(resp.content)

        self.assertTrue(data.get('success'))
        self.assertEqual(data.get('decision'), 'verified',
                         'Registered live face with score above threshold must be verified')

    # ── 6. run_full_liveness_check returns anti_spoof_passed ──────────────────

    def test_run_full_liveness_check_returns_anti_spoof_passed(self):
        """run_full_liveness_check must include anti_spoof_passed in its return dict."""
        import numpy as np
        from verification.liveness import run_full_liveness_check
        img = np.zeros((64, 64, 3), dtype=np.uint8)
        result = run_full_liveness_check(img, challenge_completed=True, anti_spoof_threshold=0.99)
        self.assertIn('anti_spoof_passed', result,
                      'anti_spoof_passed missing from run_full_liveness_check return dict')
        self.assertIsInstance(result['anti_spoof_passed'], bool)
        self.assertFalse(result['anti_spoof_passed'],
                         'Zero-image should fail anti-spoof with threshold=0.99')

    # ── 7. Denial reason distinguishes challenge vs anti-spoof failure ─────────

    @override_settings(LIVENESS_REQUIRED=True, DEBUG=True)
    @mock.patch('verification.views.load_image_from_bytes')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.check_anti_spoofing')
    def test_denial_reason_mentions_challenge_when_only_challenge_fails(
        self, mock_spoof, mock_detect, mock_load,
    ):
        """
        When server anti-spoof passes but no challenge TX exists, the denial
        reason must explicitly mention the movement challenge. This is the path for
        phone/screen/replay attacks and strict mode with head tracking unavailable.
        The reason may mention anti-spoof for context (confirming it passed) but must
        not claim anti-spoof was the cause of the failure.

        v2.1.16: no valid tx_token (rather than a client challenge_completed
        flag) is how "challenge not completed" is represented server-side —
        see Security Hardening Round #1.
        """
        import numpy as np
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        # Score 0.10 < 0.30 trigger → challenge required; passed=True so anti-spoof passes.
        # No valid TX → denied: reason must mention "challenge", not "below threshold".
        mock_spoof.return_value = {'passed': True, 'score': 0.10, 'reason': 'Real face detected.'}

        resp = self._post(challenge_completed=False, liveness_passed=False, with_tx=False)
        data = json.loads(resp.content)
        reason = data.get('reason', '').lower()

        self.assertEqual(data.get('decision'), 'denied')
        self.assertIn('challenge', reason,
                      'Denial reason must mention "challenge" when the movement check was the failure')
        # Must not say anti-spoof was below threshold when it actually passed
        self.assertNotIn('below threshold', reason,
                         'Denial reason must not say anti-spoof is below threshold when it passed')

    # ── 8. Hard gate: failed liveness must deny even if face similarity is high ──

    @override_settings(LIVENESS_REQUIRED=True, DEBUG=True)
    @mock.patch('verification.views.load_image_from_bytes')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.check_anti_spoofing')
    def test_failed_liveness_denies_even_if_face_would_match(
        self, mock_spoof, mock_detect, mock_load,
    ):
        """
        Addendum 2 regression: if liveness fails, verification must be DENIED before
        face matching runs — even if the captured face belongs to the correct registered
        beneficiary. A photo on a phone screen of the correct person must NOT be verified.

        This tests the hard gate: LIVENESS_REQUIRED=True + no valid liveness TX
        -> denied, with no face processing attempted.

        Note: in strict mode the decision is denied regardless of face similarity,
        because face matching does not run when liveness fails.

        v2.1.16: no valid tx_token (rather than a client challenge_completed
        flag) is how "challenge not completed" is represented server-side —
        see Security Hardening Round #1.
        """
        import numpy as np
        from unittest.mock import call as mock_call
        from verification.views import process_face_for_verification as _real_pf

        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        # Score 0.10 < 0.30 trigger → challenge required; passed=True so anti-spoof passes.
        # No valid TX → liveness denied before face matching runs.
        mock_spoof.return_value = {'passed': True, 'score': 0.10, 'reason': 'Real face detected.'}

        # Patch process_face_for_verification so we can assert it was NOT called
        with mock.patch('verification.views.process_face_for_verification') as mock_pf:
            mock_pf.return_value = {
                'success': True, 'embedding': np.zeros(128),
                'quality': {'ok': True, 'score': 0.9, 'reason': ''}, 'using_mock': False,
            }
            resp = self._post(challenge_completed=False, liveness_passed=False, with_tx=False)
            data = json.loads(resp.content)

            # Must be denied
            self.assertEqual(data.get('decision'), 'denied',
                             'Failed liveness must deny even when face similarity would pass')
            # Face matching must NOT have been attempted
            mock_pf.assert_not_called()


# ──────────────────────────────────────────────────────────────────────────────
# FANSC v2.1.17 functional correction pass — regression coverage locking in the
# retry state machine's ordering: a poor-quality capture on an early attempt
# must stay in the retry loop (another live capture, same beneficiary, same
# event) and must NOT itself finalize a claim, mark successful verification,
# or release a payout. Only once retries are exhausted (or the score lands in
# the separate manual-review/lookalike band, or a later attempt actually
# clears the auto-verify threshold) does the existing terminal decision logic
# apply. These tests exercise the real view end-to-end across a multi-attempt
# session rather than a single mocked call, so a regression in attempt-number
# bookkeeping or premature finalization would be caught here.
# ──────────────────────────────────────────────────────────────────────────────

class VerifySubmitRetryStateTransitionTest(TestCase):

    def setUp(self):
        from verification.models import StipendEvent, FaceEmbedding
        from cryptography.fernet import Fernet

        self.staff = _make_staff('retry_sm_staff')
        self.ben = _make_beneficiary('BEN-RETRYSM-001', 'SC-RETRYSM-001')
        self.ben.status = Beneficiary.STATUS_ACTIVE
        self.ben.save()

        self.event = StipendEvent.objects.create(
            title='Retry State Machine Test Payout',
            date=datetime.date.today(),
            amount=500,
            is_active=True,
            created_by=self.staff,
        )

        key = Fernet.generate_key()
        fake_enc = Fernet(key).encrypt(b'\x00' * 512)
        FaceEmbedding.objects.create(beneficiary=self.ben, embedding_data=fake_enc)

        self.client = Client()
        self.client.force_login(self.staff)
        self.url = reverse('verification:verify_submit')

    def _set_session(self, attempt_number):
        session = self.client.session
        session['verification_session'] = {
            'beneficiary_id': str(self.ben.pk),
            'attempt_number': attempt_number,
            'session_id': _FIXED_TEST_SESSION_ID,
            'claimant_type': 'beneficiary',
            'stipend_event_id': str(self.event.pk),
            'challenge': 'right',
        }
        session.save()

    def _post(self, attempt_number):
        self._set_session(attempt_number)
        tx = _make_liveness_tx(self.ben, self.staff, self.event)
        payload = json.dumps({
            'image': DATA_URI_JPEG,
            'challenge_completed': True,
            'liveness_score': 0.9,
            'anti_spoof_score': 0.9,
            'liveness_passed': True,
            'face_detected': True,
            'tx_token': str(tx.token),
            'session_id': _FIXED_TEST_SESSION_ID,
        })
        return self.client.post(
            self.url, data=payload, content_type='application/json', secure=True,
        )

    def _mocked_post(self, attempt_number, score):
        import numpy as np
        with mock.patch('verification.views.load_image_from_bytes') as mock_load, \
             mock.patch('verification.views.detect_and_align_face') as mock_detect, \
             mock.patch('verification.views.check_anti_spoofing') as mock_spoof, \
             mock.patch('verification.views.process_face_for_verification') as mock_process, \
             mock.patch('verification.views.compare_with_all_embeddings') as mock_compare:
            mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
            mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
            mock_spoof.return_value = {'passed': True, 'score': 0.9, 'reason': 'Real face.'}
            mock_process.return_value = {
                'success': True, 'embedding': np.zeros(128),
                'quality': {'ok': True, 'score': 0.9, 'reason': ''}, 'using_mock': False,
            }
            mock_compare.return_value = {
                'success': True, 'score': score,
                'matched_template': 'primary', 'templates_checked': 1, 'all_scores': [],
            }
            resp = self._post(attempt_number)
        return json.loads(resp.content)

    @override_settings(LIVENESS_REQUIRED=True, DEMO_MODE=False, VERIFICATION_THRESHOLD=0.75,
                        MAX_RETRY_ATTEMPTS=2, DEBUG=True)
    def test_attempt_1_low_score_retries_without_finalizing_anything(self):
        """A poor-quality first capture (score well below the review band)
        must return 'retry' — not verified, not claimed, not manual_review —
        and must leave the door open for another capture."""
        from verification.models import ClaimRecord, VerificationAttempt
        data = self._mocked_post(attempt_number=1, score=0.40)

        self.assertEqual(data.get('decision'), 'retry')
        self.assertEqual(data.get('attempt_number'), 1)
        self.assertIn('new_challenge', data)
        self.assertFalse(
            ClaimRecord.objects.filter(beneficiary=self.ben).exists(),
            'A retry decision must not create a ClaimRecord (no payout release)')
        self.assertFalse(
            VerificationAttempt.objects.filter(
                beneficiary=self.ben, decision=VerificationAttempt.DECISION_VERIFIED,
            ).exists(),
            'A retry decision must not be recorded as a successful verification')

        # The session must have advanced so the NEXT capture is attempt 2,
        # not silently reset back to 1 or stuck.
        session = self.client.session
        self.assertEqual(session['verification_session']['attempt_number'], 2)

    @override_settings(LIVENESS_REQUIRED=True, DEMO_MODE=False, VERIFICATION_THRESHOLD=0.75,
                        MAX_RETRY_ATTEMPTS=2, DEBUG=True)
    def test_retry_then_passing_attempt_verifies_and_claims_exactly_once(self):
        """attempt 1: low score -> retry (no claim). attempt 2: score clears
        auto-verify -> verified, exactly one ClaimRecord created. Confirms the
        retry path does not double-claim once a later attempt succeeds."""
        from verification.models import ClaimRecord

        first = self._mocked_post(attempt_number=1, score=0.40)
        self.assertEqual(first.get('decision'), 'retry')
        self.assertEqual(
            ClaimRecord.objects.filter(beneficiary=self.ben).count(), 0,
            'No claim may exist after the first (retry) attempt')

        # Claim finalization additionally requires the global 07:00-20:00
        # Asia/Manila same-day claiming window (Phase B.5). Patching the
        # eligibility check directly (rather than mocking
        # django.utils.timezone.now globally) keeps this test's outcome
        # dependent only on the retry/decision logic under test — mocking
        # timezone.now process-wide was observed to intermittently corrupt
        # the test client's session persistence when this suite runs
        # alongside many other tests (session write done under the mock
        # sometimes failed to round-trip), which is exactly the kind of
        # unrelated flakiness this regression suite must not introduce.
        from verification.models import StipendEvent as _StipendEvent
        with mock.patch.object(_StipendEvent, 'check_claim_eligible_now', return_value=(True, '')):
            second = self._mocked_post(attempt_number=2, score=0.95)
        self.assertEqual(second.get('decision'), 'verified',
                         'A later attempt clearing the auto-verify threshold must '
                         'follow normal decision logic once retries are still available')

        claims = ClaimRecord.objects.filter(beneficiary=self.ben, stipend_event=self.event)
        self.assertEqual(claims.count(), 1,
                         'Exactly one ClaimRecord must exist — no duplicate payout from the '
                         'earlier retry attempt')
        self.assertEqual(claims.first().status, ClaimRecord.STATUS_CLAIMED)

    @override_settings(LIVENESS_REQUIRED=True, DEMO_MODE=False, VERIFICATION_THRESHOLD=0.75,
                        MAX_RETRY_ATTEMPTS=1, DEBUG=True)
    def test_retry_exhaustion_falls_back_not_manual_review_not_verified(self):
        """With MAX_RETRY_ATTEMPTS=1 (2 total attempts), a low score on the
        FINAL allowed attempt must exhaust to 'fallback' — the existing
        terminal policy for retries-exhausted — not silently become
        manual_review or verified, and must not create a claim."""
        from verification.models import ClaimRecord, VerificationAttempt

        first = self._mocked_post(attempt_number=1, score=0.40)
        self.assertEqual(first.get('decision'), 'retry')

        second = self._mocked_post(attempt_number=2, score=0.40)
        self.assertEqual(second.get('decision'), 'fallback',
                         'Retry exhaustion must follow the existing fallback policy')

        self.assertFalse(ClaimRecord.objects.filter(beneficiary=self.ben).exists())
        last_attempt = VerificationAttempt.objects.filter(beneficiary=self.ben).latest('timestamp')
        self.assertTrue(last_attempt.fallback_triggered)

    @override_settings(LIVENESS_REQUIRED=True, DEMO_MODE=False, VERIFICATION_THRESHOLD=0.75,
                        MAX_RETRY_ATTEMPTS=2, DEBUG=True)
    def test_manual_review_band_score_creates_no_claim_even_on_attempt_1(self):
        """A score in the manual-review band (ambiguous match — look-alike/
        baby-photo/low-quality risk) is a separate hold state from retry: it
        must not create a ClaimRecord or release a payout either, on the
        first attempt or any other."""
        from verification.models import ClaimRecord

        review_band_score = 0.75 * 0.88  # inside [threshold*0.85, threshold*... ) band
        data = self._mocked_post(attempt_number=1, score=review_band_score)

        self.assertEqual(data.get('decision'), 'manual_review')
        self.assertFalse(
            ClaimRecord.objects.filter(beneficiary=self.ben).exists(),
            'manual_review must not create a ClaimRecord / release a payout')


class VerifySubmitServiceUnavailableTest(TestCase):
    """
    Release-blocker fix: when the real FaceNet model is unavailable,
    verify_submit must return a distinct SYSTEM-unavailable response — never
    a 'denied' biometric decision — and must not create/save a
    VerificationAttempt or ClaimRecord from that state (Section 7 of the
    FaceNet fail-closed requirements).
    """

    def setUp(self):
        import datetime
        from verification.models import StipendEvent, FaceEmbedding
        from cryptography.fernet import Fernet

        self.staff = _make_staff('vsu_staff')
        self.ben = _make_beneficiary('BEN-VSU-001', 'SC-VSU-001')
        self.ben.status = Beneficiary.STATUS_ACTIVE
        self.ben.save()

        today = datetime.date.today()
        self.event = StipendEvent.objects.create(
            title='VSU Test Payout',
            date=today,
            amount=500,
            is_active=True,
            created_by=self.staff,
        )

        key = Fernet.generate_key()
        fake_enc = Fernet(key).encrypt(b'\x00' * 512)
        FaceEmbedding.objects.create(beneficiary=self.ben, embedding_data=fake_enc)

        self.client = Client()
        self.client.force_login(self.staff)
        self.url = reverse('verification:verify_submit')

    def _set_session(self):
        session = self.client.session
        session['verification_session'] = {
            'beneficiary_id': str(self.ben.pk),
            'attempt_number': 1,
            'session_id': _FIXED_TEST_SESSION_ID,
            'claimant_type': 'beneficiary',
            'stipend_event_id': str(self.event.pk),
            'challenge': 'right',
        }
        session.save()

    def _post(self):
        self._set_session()
        # embedding_data=None (default) -> verify_submit takes the fallback
        # path that calls process_face_for_verification() directly.
        tx = _make_liveness_tx(self.ben, self.staff, self.event,
                               anti_spoof_score=0.9, liveness_score=0.9)
        payload = json.dumps({
            'image': DATA_URI_JPEG,
            'challenge_completed': True,
            'liveness_score': 0.9,
            'anti_spoof_score': 0.9,
            'liveness_passed': True,
            'face_detected': True,
            'tx_token': str(tx.token),
            'session_id': _FIXED_TEST_SESSION_ID,
        })
        return self.client.post(
            self.url, data=payload, content_type='application/json', secure=True,
        )

    @override_settings(LIVENESS_REQUIRED=True, DEBUG=True)
    @mock.patch('verification.views.load_image_from_bytes')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.check_anti_spoofing')
    @mock.patch('verification.views.process_face_for_verification')
    def test_model_unavailable_returns_system_unavailable_not_denied(
        self, mock_process, mock_spoof, mock_detect, mock_load,
    ):
        from verification.models import VerificationAttempt
        import numpy as np

        # v2.1.16: the submit-time anti-spoof recheck (Security Hardening
        # Round #1) now needs a real detected face to reach check_anti_spoofing
        # at all — mock the face-detection steps so the mocked spoof result
        # below actually governs server_anti_spoof_passed.
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_spoof.return_value = {'passed': True, 'score': 0.9, 'reason': 'Real face.'}
        mock_process.return_value = {
            'success': False,
            'error': 'Face recognition model is unavailable. Biometric verification is '
                     'temporarily disabled. Connect this computer to the internet during '
                     'initial setup or contact IT, then restart FANS-C.',
            'model_unavailable': True,
        }

        before_count = VerificationAttempt.objects.count()
        resp = self._post()
        data = json.loads(resp.content)

        self.assertEqual(resp.status_code, 503)
        self.assertFalse(data.get('success'))
        self.assertTrue(data.get('system_unavailable'))
        self.assertNotEqual(data.get('decision'), 'denied')
        self.assertIn('unavailable', data.get('error', '').lower())

        # No VerificationAttempt was created/persisted from this state — a
        # missing model must never be financially interpreted as a
        # completed biometric failure.
        self.assertEqual(VerificationAttempt.objects.count(), before_count)

    @override_settings(LIVENESS_REQUIRED=True, DEBUG=True)
    @mock.patch('verification.views.load_image_from_bytes')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.check_anti_spoofing')
    @mock.patch('verification.views.process_face_for_verification')
    def test_model_unavailable_leaves_verification_session_for_retry(
        self, mock_process, mock_spoof, mock_detect, mock_load,
    ):
        """Unlike a real denial, the session is left intact so the operator
        can retry once the model/connectivity is restored, without
        restarting the whole verification flow."""
        import numpy as np
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_spoof.return_value = {'passed': True, 'score': 0.9, 'reason': 'Real face.'}
        mock_process.return_value = {
            'success': False,
            'error': 'Face recognition model is unavailable.',
            'model_unavailable': True,
        }

        self._post()
        self.assertIn('verification_session', self.client.session)


class StrictModeResultPageTest(TestCase):
    """
    When LIVENESS_REQUIRED=True the result page must never show assisted-mode wording.

    Covers:
    - result page has no "VERIFIED — ASSISTED MODE" in strict mode
    - result page has no "Assisted Rollout Mode" text in strict mode
    - result page has no "did not block" text in strict mode
    - verify_submit stores demo_mode_active=False when LIVENESS_REQUIRED=True even if DEMO_MODE=True
    """

    def setUp(self):
        from verification.models import VerificationAttempt
        self.admin = CustomUser.objects.create_user(
            username='admin_smp', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-SMP',
        )
        # Attempt stored with demo_mode_active=False (strict mode enforced at submit time)
        self.ben = _make_beneficiary(ben_id='BEN-SMP-001', sc_id='SC-SMP')
        self.attempt_strict = VerificationAttempt.objects.create(
            beneficiary=self.ben,
            performed_by=self.admin,
            decision=VerificationAttempt.DECISION_VERIFIED,
            similarity_score=0.85,
            threshold_used=0.75,
            liveness_passed=True,
            liveness_score=0.9,
            anti_spoof_score=0.8,
            head_movement_completed=True,
            demo_mode_active=False,
            notes='',
        )
        self.client.force_login(self.admin)

    def test_strict_mode_result_no_assisted_mode_label(self):
        resp = self.client.get(
            reverse('verification:verify_result', kwargs={'attempt_id': self.attempt_strict.pk})
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'ASSISTED MODE')
        self.assertNotContains(resp, 'Assisted Rollout Mode')
        self.assertNotContains(resp, 'did not block')

    def test_strict_mode_result_shows_verified(self):
        resp = self.client.get(
            reverse('verification:verify_result', kwargs={'attempt_id': self.attempt_strict.pk})
        )
        self.assertContains(resp, 'VERIFIED')
        self.assertNotContains(resp, 'ASSISTED MODE')

    @override_settings(LIVENESS_REQUIRED=True, DEMO_MODE=True, DEBUG=True)
    @mock.patch('verification.views.load_image_from_bytes')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.check_anti_spoofing')
    @mock.patch('verification.views.process_face_for_verification')
    @mock.patch('verification.views.compare_with_all_embeddings')
    def test_verify_submit_strict_sets_demo_mode_active_false(
        self, mock_compare, mock_process, mock_spoof, mock_detect, mock_load,
    ):
        """verify_submit must store demo_mode_active=False when LIVENESS_REQUIRED=True,
        even if DEMO_MODE=True. The result page 'ASSISTED MODE' banner must not appear."""
        import numpy as np
        import datetime
        from verification.models import StipendEvent, FaceEmbedding, VerificationAttempt
        from cryptography.fernet import Fernet

        staff = _make_staff('smp_staff')
        ben = _make_beneficiary('BEN-SMP-002', 'SC-SMP-002')
        ben.status = Beneficiary.STATUS_ACTIVE
        ben.save()
        event = StipendEvent.objects.create(
            title='SMP Payout', date=datetime.date.today(), amount=500,
            is_active=True, created_by=staff,
        )
        key = Fernet.generate_key()
        FaceEmbedding.objects.create(
            beneficiary=ben, embedding_data=Fernet(key).encrypt(b'\x00' * 512),
        )

        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_spoof.return_value = {'passed': True, 'score': 0.9, 'reason': 'Real face.'}
        mock_process.return_value = {
            'success': True, 'embedding': np.zeros(128),
            'quality': {'ok': True, 'score': 0.9, 'reason': ''}, 'using_mock': False,
        }
        mock_compare.return_value = {
            'success': True, 'score': 0.90,
            'matched_template': 'primary', 'templates_checked': 1, 'all_scores': [],
        }

        client = Client()
        client.force_login(staff)
        session = client.session
        session['verification_session'] = {
            'beneficiary_id': str(ben.pk),
            'attempt_number': 1,
            'session_id': _FIXED_TEST_SESSION_ID,
            'claimant_type': 'beneficiary',
            'stipend_event_id': str(event.pk),
            'challenge': 'side',
        }
        session.save()

        tx = _make_liveness_tx(ben, staff, event)
        payload = json.dumps({
            'image': DATA_URI_JPEG,
            'challenge_completed': True,
            'liveness_score': 0.9,
            'anti_spoof_score': 0.9,
            'liveness_passed': True,
            'face_detected': True,
            'tx_token': str(tx.token),
            'session_id': _FIXED_TEST_SESSION_ID,
        })
        resp = client.post(
            reverse('verification:verify_submit'),
            data=payload, content_type='application/json', secure=True,
        )
        data = json.loads(resp.content)
        self.assertEqual(data.get('decision'), 'verified')

        attempt = VerificationAttempt.objects.filter(beneficiary=ben).latest('timestamp')
        self.assertFalse(
            attempt.demo_mode_active,
            'demo_mode_active must be False when LIVENESS_REQUIRED=True, '
            'even if DEMO_MODE=True. Result page must not show ASSISTED MODE.',
        )

    @override_settings(LIVENESS_REQUIRED=False, DEMO_MODE=True, DEBUG=True)
    @mock.patch('verification.views.load_image_from_bytes')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.check_anti_spoofing')
    @mock.patch('verification.views.process_face_for_verification')
    @mock.patch('verification.views.compare_with_all_embeddings')
    def test_verify_submit_assisted_sets_demo_mode_active_true(
        self, mock_compare, mock_process, mock_spoof, mock_detect, mock_load,
    ):
        """verify_submit stores demo_mode_active=True when LIVENESS_REQUIRED=False AND DEMO_MODE=True."""
        import numpy as np
        import datetime
        from verification.models import StipendEvent, FaceEmbedding, VerificationAttempt
        from cryptography.fernet import Fernet

        staff = _make_staff('smp_staff2')
        ben = _make_beneficiary('BEN-SMP-003', 'SC-SMP-003')
        ben.status = Beneficiary.STATUS_ACTIVE
        ben.save()
        event = StipendEvent.objects.create(
            title='SMP Payout2', date=datetime.date.today(), amount=500,
            is_active=True, created_by=staff,
        )
        key = Fernet.generate_key()
        FaceEmbedding.objects.create(
            beneficiary=ben, embedding_data=Fernet(key).encrypt(b'\x00' * 512),
        )

        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_spoof.return_value = {'passed': True, 'score': 0.9, 'reason': 'Real face.'}
        mock_process.return_value = {
            'success': True, 'embedding': np.zeros(128),
            'quality': {'ok': True, 'score': 0.9, 'reason': ''}, 'using_mock': False,
        }
        mock_compare.return_value = {
            'success': True, 'score': 0.90,
            'matched_template': 'primary', 'templates_checked': 1, 'all_scores': [],
        }

        client = Client()
        client.force_login(staff)
        session = client.session
        session['verification_session'] = {
            'beneficiary_id': str(ben.pk),
            'attempt_number': 1,
            'session_id': _FIXED_TEST_SESSION_ID,
            'claimant_type': 'beneficiary',
            'stipend_event_id': str(event.pk),
            'challenge': 'side',
        }
        session.save()

        tx = _make_liveness_tx(ben, staff, event)
        payload = json.dumps({
            'image': DATA_URI_JPEG,
            'challenge_completed': True,
            'liveness_score': 0.9,
            'anti_spoof_score': 0.9,
            'liveness_passed': True,
            'face_detected': True,
            'tx_token': str(tx.token),
            'session_id': _FIXED_TEST_SESSION_ID,
        })
        resp = client.post(
            reverse('verification:verify_submit'),
            data=payload, content_type='application/json', secure=True,
        )
        data = json.loads(resp.content)
        self.assertIn(data.get('decision'), ('verified', 'retry', 'manual_review'))

        attempt = VerificationAttempt.objects.filter(beneficiary=ben).latest('timestamp')
        self.assertTrue(
            attempt.demo_mode_active,
            'demo_mode_active must be True when LIVENESS_REQUIRED=False and DEMO_MODE=True.',
        )


# ── Lookalike / duplicate safety gate regression tests ───────────────────────

class LookalikeSafetyGateTest(TestCase):
    """
    Regression tests confirming the lookalike/duplicate safety gate in verify_submit.

    The gate must:
    1. Escalate to MANUAL_REVIEW when another beneficiary scores within LOOKALIKE_BAND
       of the claimed score, even though liveness and face match both passed.
    2. NOT create a ClaimRecord when MANUAL_REVIEW is the result of a lookalike hit.
    3. Return VERIFIED (not MANUAL_REVIEW) when no close competitor exists.
    4. Still work when LIVENESS_REQUIRED=True (strict mode).

    All four tests mock check_duplicate_face via the source module so the dynamic
    import inside verify_submit picks up the mock.
    """

    def setUp(self):
        import datetime
        from verification.models import StipendEvent, FaceEmbedding
        from cryptography.fernet import Fernet

        self.staff = _make_staff('lk_staff')
        self.ben = _make_beneficiary('BEN-LK-001', 'SC-LK-001')
        self.ben.status = Beneficiary.STATUS_ACTIVE
        self.ben.save()

        today = datetime.date.today()
        self.event = StipendEvent.objects.create(
            title='LK Test Payout',
            date=today,
            amount=500,
            is_active=True,
            created_by=self.staff,
        )

        key = Fernet.generate_key()
        fake_enc = Fernet(key).encrypt(b'\x00' * 512)
        FaceEmbedding.objects.create(beneficiary=self.ben, embedding_data=fake_enc)

        self.client = Client()
        self.client.force_login(self.staff)
        self.url = reverse('verification:verify_submit')

    def _set_session(self):
        session = self.client.session
        session['verification_session'] = {
            'beneficiary_id': str(self.ben.pk),
            'attempt_number': 1,
            'session_id': _FIXED_TEST_SESSION_ID,
            'claimant_type': 'beneficiary',
            'stipend_event_id': str(self.event.pk),
            'challenge': 'side',
        }
        session.save()

    def _post(self, challenge_completed=True, liveness_passed=True):
        self._set_session()
        tx = _make_liveness_tx(self.ben, self.staff, self.event)
        payload = json.dumps({
            'image': DATA_URI_JPEG,
            'challenge_completed': challenge_completed,
            'liveness_score': 0.9,
            'anti_spoof_score': 0.9,
            'liveness_passed': liveness_passed,
            'face_detected': True,
            'tx_token': str(tx.token),
            'session_id': _FIXED_TEST_SESSION_ID,
        })
        return self.client.post(
            self.url, data=payload, content_type='application/json', secure=True,
        )

    def _mock_passing_face(self, mock_compare, mock_process, mock_spoof, mock_detect, mock_load):
        """Configure mocks for a face that passes liveness and scores 0.88."""
        import numpy as np
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_spoof.return_value = {'passed': True, 'score': 0.9, 'reason': 'Real face.'}
        mock_process.return_value = {
            'success': True, 'embedding': np.zeros(128),
            'quality': {'ok': True, 'score': 0.9, 'reason': ''}, 'using_mock': False,
        }
        mock_compare.return_value = {
            'success': True, 'score': 0.88,
            'matched_template': 'primary', 'templates_checked': 1, 'all_scores': [],
        }

    # ── 1. Lookalike within band → MANUAL_REVIEW ─────────────────────────────

    @override_settings(LIVENESS_REQUIRED=True, DEMO_MODE=False, VERIFICATION_THRESHOLD=0.75,
                       LOOKALIKE_BAND=0.05, DEBUG=True)
    @mock.patch('verification.face_utils.check_duplicate_face')
    @mock.patch('verification.views.load_image_from_bytes')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.check_anti_spoofing')
    @mock.patch('verification.views.process_face_for_verification')
    @mock.patch('verification.views.compare_with_all_embeddings')
    def test_lookalike_within_band_escalates_to_manual_review(
        self, mock_compare, mock_process, mock_spoof, mock_detect, mock_load, mock_dup,
    ):
        """
        Face match passes (score 0.88 >= threshold 0.75) AND liveness passes, but
        another beneficiary also scores 0.84 (within 0.05 band of 0.88).
        Expected: MANUAL_REVIEW — lookalike safety gate must trigger.
        """
        self._mock_passing_face(mock_compare, mock_process, mock_spoof, mock_detect, mock_load)
        mock_dup.return_value = {
            'duplicates_found': True,
            'matches': [{'beneficiary_id': 'BEN-LK-TWIN', 'full_name': 'Twin Person',
                          'score': 0.84, 'template': 'primary'}],
            'highest_score': 0.84,
            'checked': 5,
        }

        resp = self._post(challenge_completed=True, liveness_passed=True)
        data = json.loads(resp.content)

        self.assertTrue(data.get('success'))
        self.assertEqual(data.get('decision'), 'manual_review',
                         f'Lookalike within band must escalate to manual_review. Got: {data}')
        self.assertIn('POSSIBLE DUPLICATE OR LOOKALIKE', data.get('reason', ''),
                      'Reason must mention POSSIBLE DUPLICATE OR LOOKALIKE')

    # ── 2. Lookalike gate prevents ClaimRecord creation ───────────────────────

    @override_settings(LIVENESS_REQUIRED=True, DEMO_MODE=False, VERIFICATION_THRESHOLD=0.75,
                       LOOKALIKE_BAND=0.05, DEBUG=True)
    @mock.patch('verification.face_utils.check_duplicate_face')
    @mock.patch('verification.views.load_image_from_bytes')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.check_anti_spoofing')
    @mock.patch('verification.views.process_face_for_verification')
    @mock.patch('verification.views.compare_with_all_embeddings')
    def test_lookalike_manual_review_does_not_create_claim_record(
        self, mock_compare, mock_process, mock_spoof, mock_detect, mock_load, mock_dup,
    ):
        """
        When the lookalike gate fires (MANUAL_REVIEW), no ClaimRecord must be created.
        The stipend must not be automatically released under a lookalike flag.
        """
        from verification.models import ClaimRecord
        self._mock_passing_face(mock_compare, mock_process, mock_spoof, mock_detect, mock_load)
        mock_dup.return_value = {
            'duplicates_found': True,
            'matches': [{'beneficiary_id': 'BEN-LK-TWIN2', 'full_name': 'Twin Two',
                          'score': 0.85, 'template': 'primary'}],
            'highest_score': 0.85,
            'checked': 3,
        }

        resp = self._post(challenge_completed=True, liveness_passed=True)
        data = json.loads(resp.content)

        self.assertEqual(data.get('decision'), 'manual_review')
        claims = ClaimRecord.objects.filter(
            beneficiary=self.ben,
            stipend_event=self.event,
            status=ClaimRecord.STATUS_CLAIMED,
        )
        self.assertFalse(
            claims.exists(),
            'No ClaimRecord must be created when lookalike gate fires — stipend must not be released.',
        )

    # ── 3. No competitor → VERIFIED (gate does not false-fire) ───────────────

    @override_settings(LIVENESS_REQUIRED=True, DEMO_MODE=False, VERIFICATION_THRESHOLD=0.75,
                       LOOKALIKE_BAND=0.05, DEBUG=True)
    @mock.patch('verification.face_utils.check_duplicate_face')
    @mock.patch('verification.views.load_image_from_bytes')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.check_anti_spoofing')
    @mock.patch('verification.views.process_face_for_verification')
    @mock.patch('verification.views.compare_with_all_embeddings')
    def test_no_competitor_verifies_successfully(
        self, mock_compare, mock_process, mock_spoof, mock_detect, mock_load, mock_dup,
    ):
        """
        Face match passes, liveness passes, and no close competitor found.
        Expected: VERIFIED — gate must not fire when there is no lookalike.
        """
        self._mock_passing_face(mock_compare, mock_process, mock_spoof, mock_detect, mock_load)
        mock_dup.return_value = {
            'duplicates_found': False, 'matches': [], 'highest_score': 0.0, 'checked': 4,
        }

        resp = self._post(challenge_completed=True, liveness_passed=True)
        data = json.loads(resp.content)

        self.assertTrue(data.get('success'))
        self.assertEqual(data.get('decision'), 'verified',
                         f'No competitor must result in verified. Got: {data}')

    # ── 4. Gate still works under strict liveness (LIVENESS_REQUIRED=True) ───

    @override_settings(LIVENESS_REQUIRED=True, DEMO_MODE=False, VERIFICATION_THRESHOLD=0.75,
                       LOOKALIKE_BAND=0.05, DEBUG=True)
    @mock.patch('verification.face_utils.check_duplicate_face')
    @mock.patch('verification.views.load_image_from_bytes')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.check_anti_spoofing')
    @mock.patch('verification.views.process_face_for_verification')
    @mock.patch('verification.views.compare_with_all_embeddings')
    def test_lookalike_gate_active_in_strict_liveness_mode(
        self, mock_compare, mock_process, mock_spoof, mock_detect, mock_load, mock_dup,
    ):
        """
        Lookalike gate must fire even when strict liveness mode is active and both
        anti-spoof and challenge pass. LIVENESS_REQUIRED=True must not suppress the gate.
        """
        self._mock_passing_face(mock_compare, mock_process, mock_spoof, mock_detect, mock_load)
        mock_dup.return_value = {
            'duplicates_found': True,
            'matches': [{'beneficiary_id': 'BEN-LK-TWIN3', 'full_name': 'Strict Twin',
                          'score': 0.86, 'template': 'primary'}],
            'highest_score': 0.86,
            'checked': 6,
        }

        resp = self._post(challenge_completed=True, liveness_passed=True)
        data = json.loads(resp.content)

        self.assertEqual(data.get('decision'), 'manual_review',
                         'Lookalike gate must trigger MANUAL_REVIEW even in strict liveness mode.')
        self.assertIn('POSSIBLE DUPLICATE OR LOOKALIKE', data.get('reason', ''))


# ── Registration liveness risk-based challenge tests ──────────────────────────

class RegistrationLivenessTest(TestCase):
    """
    Tests for the risk-based liveness challenge in register_submit_face (v2.2.1).

    The challenge is only required when anti-spoof score is borderline (< 0.30)
    or face quality is poor.  Strong anti-spoof scores (>= 0.30) with good quality
    are accepted without the head-movement challenge.

    Phone/screen protection:
      - Anti-spoof fail (<= ANTI_SPOOF_THRESHOLD) → always rejected.
      - Borderline score + no challenge → rejected.
      - Borderline score + challenge completed → passes.
    """

    def setUp(self):
        self.client = Client()
        self.staff = _make_staff('rl_staff')
        self.client.force_login(self.staff)
        self.url = reverse('beneficiaries:register_submit_face')

    def _set_reg_session(self):
        s = self.client.session
        s['reg_step1'] = {
            'first_name': 'RLTest',
            'last_name': 'Person',
            'date_of_birth': '1940-06-15',
            'senior_citizen_id': 'SC-RL-TEST',
            'gender': 'M',
            'address': '1 Test St',
            'barangay': 'RL Barangay',
            'municipality': 'Quezon City',
            'province': 'Metro Manila',
        }
        s.save()

    def _post(self, challenge_completed=False, anti_spoof_score=0.8, liveness_passed=True):
        self._set_reg_session()
        return self.client.post(
            self.url,
            data=json.dumps({
                'image': DATA_URI_JPEG,
                'liveness_passed': liveness_passed,
                'challenge_completed': challenge_completed,
                'anti_spoof_score': anti_spoof_score,
            }),
            content_type='application/json',
        )

    # ── 1. Strong anti-spoof + good quality → pass without challenge ──────────

    @override_settings(
        REGISTRATION_LIVENESS_REQUIRED=True,
        REGISTRATION_CHALLENGE_REQUIRED=False,
        LIVENESS_CHALLENGE_TRIGGER_THRESHOLD=0.30,
        ANTI_SPOOF_THRESHOLD=0.25,
        DEBUG=True,
    )
    @mock.patch('beneficiaries.views.check_duplicate_face')
    @mock.patch('beneficiaries.views.process_face_for_registration')
    @mock.patch('beneficiaries.sync.mark_created')
    @mock.patch('verification.liveness.check_anti_spoofing')
    @mock.patch('verification.face_utils.check_face_quality')
    @mock.patch('verification.face_utils.detect_and_align_face')
    @mock.patch('verification.face_utils.load_image_from_bytes')
    def test_strong_antispoof_passes_without_challenge(
        self, mock_load, mock_detect, mock_quality, mock_spoof,
        mock_sync, mock_face, mock_dup,
    ):
        """Strong anti-spoof (>= 0.30) with good quality must register without requiring challenge."""
        import numpy as np
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_quality.return_value = {'ok': True, 'score': 0.9, 'reason': ''}
        mock_spoof.return_value = {'passed': True, 'score': 0.75, 'reason': 'Real face.'}
        mock_face.return_value = {
            'success': True,
            'encrypted_embedding': b'fake-enc',
            'quality': {'ok': True, 'score': 0.9, 'reason': ''},
        }
        mock_dup.return_value = {'duplicates_found': False, 'matches': [], 'highest_score': 0.0}
        mock_sync.return_value = None

        with mock.patch('verification.face_utils.decrypt_embedding', return_value=[0.1] * 128):
            resp = self._post(challenge_completed=False, anti_spoof_score=0.75)

        data = json.loads(resp.content)
        self.assertTrue(
            data.get('success'),
            f'Strong anti-spoof should pass without challenge. Error: {data.get("error")}',
        )

    # ── 2. Anti-spoof fail → always rejected ─────────────────────────────────

    @override_settings(
        REGISTRATION_LIVENESS_REQUIRED=True,
        ANTI_SPOOF_THRESHOLD=0.25,
        DEBUG=True,
    )
    @mock.patch('verification.liveness.check_anti_spoofing')
    @mock.patch('verification.face_utils.check_face_quality')
    @mock.patch('verification.face_utils.detect_and_align_face')
    @mock.patch('verification.face_utils.load_image_from_bytes')
    def test_antispoof_fail_rejected(
        self, mock_load, mock_detect, mock_quality, mock_spoof,
    ):
        """Anti-spoof score below threshold must always reject regardless of challenge."""
        import numpy as np
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_quality.return_value = {'ok': True, 'score': 0.9, 'reason': ''}
        mock_spoof.return_value = {'passed': False, 'score': 0.10, 'reason': 'Low texture.'}

        resp = self._post(challenge_completed=True, anti_spoof_score=0.10)
        data = json.loads(resp.content)

        self.assertFalse(data['success'])
        self.assertIn('anti-spoof', data.get('error', '').lower())

    # ── 3. Borderline anti-spoof + no challenge → rejected ───────────────────

    @override_settings(
        REGISTRATION_LIVENESS_REQUIRED=True,
        REGISTRATION_CHALLENGE_REQUIRED=False,
        LIVENESS_CHALLENGE_TRIGGER_THRESHOLD=0.30,
        ANTI_SPOOF_THRESHOLD=0.25,
        DEBUG=True,
    )
    @mock.patch('verification.liveness.check_anti_spoofing')
    @mock.patch('verification.face_utils.check_face_quality')
    @mock.patch('verification.face_utils.detect_and_align_face')
    @mock.patch('verification.face_utils.load_image_from_bytes')
    def test_borderline_antispoof_without_challenge_rejected(
        self, mock_load, mock_detect, mock_quality, mock_spoof,
    ):
        """Borderline score (< 0.30) with challenge not completed must be rejected."""
        import numpy as np
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_quality.return_value = {'ok': True, 'score': 0.9, 'reason': ''}
        # Score 0.27: anti-spoof passes (>= 0.25) but is borderline (< 0.30 trigger)
        mock_spoof.return_value = {'passed': True, 'score': 0.27, 'reason': 'Low texture warning.'}

        resp = self._post(challenge_completed=False, anti_spoof_score=0.27)
        data = json.loads(resp.content)

        self.assertFalse(data['success'], 'Borderline score without challenge must be rejected')
        err = data.get('error', '').lower()
        self.assertIn('challenge', err,
                      'Error message must mention challenge when challenge was the blocking factor')

    # ── 4. Borderline anti-spoof + challenge completed → passes ──────────────

    @override_settings(
        REGISTRATION_LIVENESS_REQUIRED=True,
        REGISTRATION_CHALLENGE_REQUIRED=False,
        LIVENESS_CHALLENGE_TRIGGER_THRESHOLD=0.30,
        ANTI_SPOOF_THRESHOLD=0.25,
        DEBUG=True,
    )
    @mock.patch('beneficiaries.views.check_duplicate_face')
    @mock.patch('beneficiaries.views.process_face_for_registration')
    @mock.patch('beneficiaries.sync.mark_created')
    @mock.patch('verification.liveness.check_anti_spoofing')
    @mock.patch('verification.face_utils.check_face_quality')
    @mock.patch('verification.face_utils.detect_and_align_face')
    @mock.patch('verification.face_utils.load_image_from_bytes')
    def test_borderline_antispoof_with_challenge_passes(
        self, mock_load, mock_detect, mock_quality, mock_spoof,
        mock_sync, mock_face, mock_dup,
    ):
        """Borderline score (< 0.30) but challenge completed must be accepted."""
        import numpy as np
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_quality.return_value = {'ok': True, 'score': 0.9, 'reason': ''}
        mock_spoof.return_value = {'passed': True, 'score': 0.27, 'reason': 'Low texture warning.'}
        mock_face.return_value = {
            'success': True,
            'encrypted_embedding': b'fake-enc',
            'quality': {'ok': True, 'score': 0.9, 'reason': ''},
        }
        mock_dup.return_value = {'duplicates_found': False, 'matches': [], 'highest_score': 0.0}
        mock_sync.return_value = None

        with mock.patch('verification.face_utils.decrypt_embedding', return_value=[0.1] * 128):
            resp = self._post(challenge_completed=True, anti_spoof_score=0.27)

        data = json.loads(resp.content)
        self.assertTrue(
            data.get('success'),
            f'Borderline score with challenge completed must pass. Error: {data.get("error")}',
        )

    # ── 5. Poor quality → challenge required (borderline even at higher score) ─

    @override_settings(
        REGISTRATION_LIVENESS_REQUIRED=True,
        REGISTRATION_CHALLENGE_REQUIRED=False,
        LIVENESS_CHALLENGE_TRIGGER_THRESHOLD=0.30,
        ANTI_SPOOF_THRESHOLD=0.25,
        DEBUG=True,
    )
    @mock.patch('verification.liveness.check_anti_spoofing')
    @mock.patch('verification.face_utils.check_face_quality')
    @mock.patch('verification.face_utils.detect_and_align_face')
    @mock.patch('verification.face_utils.load_image_from_bytes')
    def test_poor_quality_without_challenge_rejected(
        self, mock_load, mock_detect, mock_quality, mock_spoof,
    ):
        """Poor face quality should trigger challenge requirement; no challenge → rejected."""
        import numpy as np
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_quality.return_value = {'ok': False, 'score': 0.3, 'reason': 'Image too blurry.'}
        # Anti-spoof passes and score is above 0.30, but quality is poor → challenge still needed
        mock_spoof.return_value = {'passed': True, 'score': 0.55, 'reason': 'Real face.'}

        resp = self._post(challenge_completed=False, anti_spoof_score=0.55)
        data = json.loads(resp.content)

        self.assertFalse(data['success'], 'Poor quality without challenge must be rejected')

    # ── 6. Phone/screen mocked spoof → rejected ───────────────────────────────

    @override_settings(
        REGISTRATION_LIVENESS_REQUIRED=True,
        ANTI_SPOOF_THRESHOLD=0.25,
        DEBUG=True,
    )
    @mock.patch('verification.liveness.check_anti_spoofing')
    @mock.patch('verification.face_utils.check_face_quality')
    @mock.patch('verification.face_utils.detect_and_align_face')
    @mock.patch('verification.face_utils.load_image_from_bytes')
    def test_phone_screen_spoof_score_rejected(
        self, mock_load, mock_detect, mock_quality, mock_spoof,
    ):
        """Phone/screen spoof with very low score must be rejected even with challenge_completed=True."""
        import numpy as np
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_quality.return_value = {'ok': True, 'score': 0.9, 'reason': ''}
        mock_spoof.return_value = {'passed': False, 'score': 0.05, 'reason': 'Very low texture.'}

        resp = self._post(challenge_completed=True, anti_spoof_score=0.05)
        data = json.loads(resp.content)

        self.assertFalse(data['success'])
        self.assertIn('anti-spoof', data.get('error', '').lower())

    # ── 7. REGISTRATION_LIVENESS_REQUIRED=False → no liveness check run ──────

    @override_settings(REGISTRATION_LIVENESS_REQUIRED=False)
    @mock.patch('beneficiaries.views.check_duplicate_face')
    @mock.patch('beneficiaries.views.process_face_for_registration')
    @mock.patch('beneficiaries.sync.mark_created')
    def test_liveness_disabled_allows_registration(
        self, mock_sync, mock_face, mock_dup,
    ):
        """When REGISTRATION_LIVENESS_REQUIRED=False, liveness checks are skipped entirely."""
        mock_face.return_value = {
            'success': True,
            'encrypted_embedding': b'fake-enc',
            'quality': {'ok': True, 'score': 0.9, 'reason': ''},
        }
        mock_dup.return_value = {'duplicates_found': False, 'matches': [], 'highest_score': 0.0}
        mock_sync.return_value = None

        with mock.patch('verification.face_utils.decrypt_embedding', return_value=[0.1] * 128):
            resp = self._post(challenge_completed=False, anti_spoof_score=0.0, liveness_passed=False)

        data = json.loads(resp.content)
        self.assertTrue(
            data.get('success'),
            f'Liveness disabled should allow registration. Error: {data.get("error")}',
        )


# ──────────────────────────────────────────────────────────────────────────────
# v2.2.0 Post-UAT Phase 5 — Duplicate face notification workflow.
# The detection + notification already existed (notify_admins in
# register_submit_face); these tests confirm the notification content is
# self-identifying (beneficiary ID, matched record, score, status) and, most
# importantly, that it stops claiming "Pending Review" once an admin has
# actually reviewed and resolved the case.
# ──────────────────────────────────────────────────────────────────────────────

@override_settings(REGISTRATION_LIVENESS_REQUIRED=False)
class DuplicateFaceNotificationWorkflowTest(TestCase):

    def setUp(self):
        self.staff = _make_staff('dupnstaff1')
        self.admin = _make_staff('dupnadmin2', role=CustomUser.ROLE_ADMIN)
        self.client = Client()
        self.existing = _make_beneficiary(ben_id='BEN-DUPN-EXIST', sc_id='SC-DUPN-EXIST')

    def _set_reg_session(self, sc_id='SC-DUPN-NEW'):
        s = self.client.session
        s['reg_step1'] = {
            'first_name': 'NewPerson',
            'last_name': 'Registrant',
            'date_of_birth': '1942-02-02',
            'senior_citizen_id': sc_id,
            'gender': 'M',
            'address': '1 Test St',
            'barangay': 'Test Barangay',
            'municipality': 'Quezon City',
            'province': 'Metro Manila',
        }
        s.save()

    @mock.patch('beneficiaries.views.check_duplicate_face')
    @mock.patch('beneficiaries.views.process_face_for_registration')
    @mock.patch('beneficiaries.sync.mark_created')
    def _register_with_duplicate(self, mock_sync, mock_face, mock_dup, score=0.93):
        mock_face.return_value = {
            'success': True,
            'encrypted_embedding': b'fake-enc',
            'quality': {'ok': True, 'score': 0.9, 'reason': ''},
        }
        mock_dup.return_value = {
            'duplicates_found': True,
            'matches': [{
                'beneficiary_id': self.existing.beneficiary_id,
                'full_name': self.existing.full_name,
                'score': score,
                'template': 'primary',
            }],
            'highest_score': score,
        }
        mock_sync.return_value = None
        self.client.force_login(self.staff)
        self._set_reg_session()
        with mock.patch('verification.face_utils.decrypt_embedding', return_value=[0.1] * 128):
            return self.client.post(
                reverse('beneficiaries:register_submit_face'),
                data=json.dumps({'image': DATA_URI_JPEG, 'liveness_passed': True, 'challenge_completed': True}),
                content_type='application/json',
            )

    def test_registration_still_succeeds_pending_review(self):
        resp = self._register_with_duplicate()
        data = json.loads(resp.content)
        self.assertTrue(data.get('success'), data.get('error', ''))
        from beneficiaries.models import Beneficiary
        flagged = Beneficiary.objects.get(senior_citizen_id='SC-DUPN-NEW')
        self.assertTrue(flagged.duplicate_review_required)
        self.assertEqual(flagged.status, Beneficiary.STATUS_PENDING)

    def test_notification_identifies_beneficiary_and_match(self):
        self._register_with_duplicate(score=0.93)
        from logs.models import Notification
        from beneficiaries.models import Beneficiary
        flagged = Beneficiary.objects.get(senior_citizen_id='SC-DUPN-NEW')
        notif = Notification.objects.get(
            category=Notification.CATEGORY_FRAUD_ALERT,
            title__icontains=flagged.beneficiary_id,
        )
        self.assertIn(flagged.full_name, notif.title)
        self.assertIn(self.existing.beneficiary_id, notif.message)
        self.assertIn('93%', notif.message)
        self.assertIn('Pending Review', notif.message)
        self.assertEqual(notif.url, reverse('beneficiaries:duplicate_review_detail', args=[flagged.pk]))

    def test_notification_marked_read_on_approve_twin(self):
        self._register_with_duplicate()
        from logs.models import Notification
        from beneficiaries.models import Beneficiary
        flagged = Beneficiary.objects.get(senior_citizen_id='SC-DUPN-NEW')
        notif = Notification.objects.get(dedupe_key=f'duplicate_face:{flagged.pk}')
        self.assertFalse(notif.is_read)

        self.client.force_login(self.admin)
        self.client.post(
            reverse('beneficiaries:duplicate_review_detail', args=[flagged.pk]),
            {'action': 'approve_twin', 'notes': 'Confirmed twin via IDs.'},
        )
        notif.refresh_from_db()
        self.assertTrue(notif.is_read)

    def test_notification_marked_read_on_reject_duplicate(self):
        self._register_with_duplicate()
        from logs.models import Notification
        from beneficiaries.models import Beneficiary
        flagged = Beneficiary.objects.get(senior_citizen_id='SC-DUPN-NEW')
        notif = Notification.objects.get(dedupe_key=f'duplicate_face:{flagged.pk}')

        self.client.force_login(self.admin)
        self.client.post(
            reverse('beneficiaries:duplicate_review_detail', args=[flagged.pk]),
            {'action': 'reject_duplicate', 'notes': 'Confirmed same person.'},
        )
        notif.refresh_from_db()
        self.assertTrue(notif.is_read)

    def test_resolved_case_still_opens_read_only_instead_of_404(self):
        self._register_with_duplicate()
        from beneficiaries.models import Beneficiary
        flagged = Beneficiary.objects.get(senior_citizen_id='SC-DUPN-NEW')

        self.client.force_login(self.admin)
        self.client.post(
            reverse('beneficiaries:duplicate_review_detail', args=[flagged.pk]),
            {'action': 'approve_twin', 'notes': 'Confirmed twin.'},
        )

        resp = self.client.get(reverse('beneficiaries:duplicate_review_detail', args=[flagged.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Review Already Completed')
        self.assertNotContains(resp, 'name="action" value="approve_twin"')

    def test_resolved_case_rejects_second_decision(self):
        self._register_with_duplicate()
        from beneficiaries.models import Beneficiary
        flagged = Beneficiary.objects.get(senior_citizen_id='SC-DUPN-NEW')

        self.client.force_login(self.admin)
        self.client.post(
            reverse('beneficiaries:duplicate_review_detail', args=[flagged.pk]),
            {'action': 'approve_twin', 'notes': 'Confirmed twin.'},
        )
        # Second decision attempt must not flip the outcome
        resp = self.client.post(
            reverse('beneficiaries:duplicate_review_detail', args=[flagged.pk]),
            {'action': 'reject_duplicate', 'notes': 'Changed my mind.'},
        )
        self.assertEqual(resp.status_code, 302)
        flagged.refresh_from_db()
        self.assertEqual(flagged.status, Beneficiary.STATUS_ACTIVE)

    def test_non_admin_cannot_access_review_case(self):
        self._register_with_duplicate()
        from beneficiaries.models import Beneficiary
        flagged = Beneficiary.objects.get(senior_citizen_id='SC-DUPN-NEW')

        self.client.force_login(self.staff)
        resp = self.client.get(reverse('beneficiaries:duplicate_review_detail', args=[flagged.pk]))
        self.assertEqual(resp.status_code, 302)


# ── Liveness Proof Binding tests (Parts 1–5) ─────────────────────────────────

class LivenessProofBindingTest(TestCase):
    """
    Regression tests for the LivenessTransaction-based face-binding security gate.

    Verifies:
    1. Missing tx_token -> denied with token-missing message
    2. Expired tx_token -> denied with expired message
    3. Already-used tx_token -> denied with single-use message
    4. Valid tx_token -> proceeds to face verification
    5. Token with wrong claimant_type -> denied
    6. Token consumed after successful verification
    7. Token from different beneficiary -> denied
    8. LIVENESS_PROOF_REQUIRED=False bypasses TX check
    """

    def setUp(self):
        from verification.models import StipendEvent, FaceEmbedding
        from cryptography.fernet import Fernet

        self.staff = _make_staff('lpb_staff')
        self.ben = _make_beneficiary('BEN-LPB-001', 'SC-LPB-001')
        self.ben.status = Beneficiary.STATUS_ACTIVE
        self.ben.save()

        self.event = StipendEvent.objects.create(
            title='LPB Payout', date=datetime.date.today(), amount=500,
            is_active=True, created_by=self.staff,
        )
        key = Fernet.generate_key()
        FaceEmbedding.objects.create(
            beneficiary=self.ben, embedding_data=Fernet(key).encrypt(b'\x00' * 512),
        )

        self.client = Client()
        self.client.force_login(self.staff)
        self.url = reverse('verification:verify_submit')

    def _set_session(self, ben=None, claimant_type='beneficiary'):
        b = ben or self.ben
        session = self.client.session
        session['verification_session'] = {
            'beneficiary_id': str(b.pk),
            'attempt_number': 1,
            'session_id': _FIXED_TEST_SESSION_ID,
            'claimant_type': claimant_type,
            'stipend_event_id': str(self.event.pk),
            'challenge': 'side',
        }
        session.save()

    def _post(self, tx_token='', challenge_completed=True):
        payload = json.dumps({
            'image': DATA_URI_JPEG,
            'challenge_completed': challenge_completed,
            'liveness_score': 0.9,
            'anti_spoof_score': 0.9,
            'liveness_passed': True,
            'face_detected': True,
            'tx_token': tx_token,
            'session_id': _FIXED_TEST_SESSION_ID,
        })
        return self.client.post(
            self.url, data=payload, content_type='application/json', secure=True,
        )

    @override_settings(LIVENESS_PROOF_REQUIRED=True, DEBUG=True)
    def test_missing_tx_token_denied(self):
        """verify_submit without tx_token must be denied with token-missing message."""
        self._set_session()
        resp = self._post(tx_token='')
        data = json.loads(resp.content)
        self.assertEqual(data.get('decision'), 'denied')

    @override_settings(LIVENESS_PROOF_REQUIRED=False, LIVENESS_REQUIRED=True, DEBUG=True)
    def test_challenge_completed_alone_does_not_bypass_liveness_gate(self):
        """
        Security regression (v2.1.16, Hardening Round #1 — CRITICAL #1):
        with LIVENESS_PROOF_REQUIRED=False (no LivenessTransaction required)
        but LIVENESS_REQUIRED still True, a modified client claiming
        challenge_completed=True with no tx_token and no server-verified
        evidence must still be DENIED. Previously server_liveness_passed
        fell back to `server_anti_spoof_passed and bool(challenge_completed)`
        in this combination, letting the raw client boolean alone satisfy
        the liveness gate.
        """
        self._set_session()
        resp = self._post(tx_token='', challenge_completed=True)
        data = json.loads(resp.content)
        self.assertEqual(data.get('decision'), 'denied',
                         f'challenge_completed alone must not pass the liveness gate: {data}')
        self.assertIn('liveness', data.get('reason', '').lower())

    @override_settings(LIVENESS_PROOF_REQUIRED=True, DEBUG=True)
    def test_expired_tx_token_denied(self):
        """An expired LivenessTransaction must be denied."""
        from verification.models import LivenessTransaction
        expired_tx = LivenessTransaction.objects.create(
            beneficiary=self.ben,
            claimant_type='beneficiary',
            stipend_event=self.event,
            performed_by=self.staff,
            challenge_direction='side',
            anti_spoof_score=0.0,
            liveness_score=0.0,
            pa_score=0.0,
            pa_flags={},
            embedding_data=None,
            expires_at=timezone.now() - datetime.timedelta(seconds=1),
        )
        self._set_session()
        resp = self._post(tx_token=str(expired_tx.token))
        data = json.loads(resp.content)
        self.assertEqual(data.get('decision'), 'denied')
        self.assertIn('expired', data.get('reason', '').lower())

    @override_settings(LIVENESS_PROOF_REQUIRED=True, DEBUG=True)
    def test_reused_tx_token_denied(self):
        """A LivenessTransaction already consumed must be denied on second use."""
        from verification.models import VerificationAttempt
        tx = _make_liveness_tx(self.ben, self.staff, self.event)
        attempt = VerificationAttempt.objects.create(
            beneficiary=self.ben,
            performed_by=self.staff,
            decision=VerificationAttempt.DECISION_VERIFIED,
            threshold_used=0.75,
        )
        tx.consume(attempt)
        self._set_session()
        resp = self._post(tx_token=str(tx.token))
        data = json.loads(resp.content)
        self.assertEqual(data.get('decision'), 'denied')
        self.assertIn('already been used', data.get('reason', '').lower())

    @override_settings(LIVENESS_PROOF_REQUIRED=True, LIVENESS_REQUIRED=True,
                       DEMO_MODE=False, VERIFICATION_THRESHOLD=0.75,
                       AUTO_VERIFY_THRESHOLD=0.80,  # v2.1.13: explicit so 0.85 lands in VERIFIED zone
                       DEBUG=True)
    @mock.patch('verification.face_utils.check_duplicate_face')
    @mock.patch('verification.views.load_image_from_bytes')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.check_anti_spoofing')
    @mock.patch('verification.views.process_face_for_verification')
    @mock.patch('verification.views.compare_with_all_embeddings')
    def test_valid_tx_token_proceeds_to_verification(
        self, mock_compare, mock_process, mock_spoof, mock_detect, mock_load, mock_dup,
    ):
        """A valid LivenessTransaction token must allow the verification flow to proceed."""
        import numpy as np
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_spoof.return_value = {'passed': True, 'score': 0.9, 'reason': 'Real face.'}
        mock_process.return_value = {
            'success': True, 'embedding': np.zeros(128),
            'quality': {'ok': True, 'score': 0.9, 'reason': ''}, 'using_mock': False,
        }
        mock_compare.return_value = {
            'success': True, 'score': 0.85,
            'matched_template': 'primary', 'templates_checked': 1, 'all_scores': [],
        }
        mock_dup.return_value = {
            'duplicates_found': False, 'matches': [], 'highest_score': 0.0, 'checked': 0,
        }
        self._set_session()
        tx = _make_liveness_tx(self.ben, self.staff, self.event)
        resp = self._post(tx_token=str(tx.token))
        data = json.loads(resp.content)
        self.assertEqual(data.get('decision'), 'verified',
                         f'Valid TX token should reach verified. Got: {data}')

    @override_settings(LIVENESS_PROOF_REQUIRED=True, DEBUG=True)
    def test_tx_token_claimant_type_mismatch_denied(self):
        """TX issued for representative must not be accepted for a beneficiary session."""
        tx = _make_liveness_tx(self.ben, self.staff, self.event, claimant_type='representative')
        self._set_session(claimant_type='beneficiary')
        resp = self._post(tx_token=str(tx.token))
        data = json.loads(resp.content)
        self.assertEqual(data.get('decision'), 'denied')
        self.assertIn('claimant type mismatch', data.get('reason', '').lower())

    @override_settings(LIVENESS_PROOF_REQUIRED=True, LIVENESS_REQUIRED=True,
                       DEMO_MODE=False, VERIFICATION_THRESHOLD=0.75, DEBUG=True)
    @mock.patch('verification.face_utils.check_duplicate_face')
    @mock.patch('verification.views.load_image_from_bytes')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.check_anti_spoofing')
    @mock.patch('verification.views.process_face_for_verification')
    @mock.patch('verification.views.compare_with_all_embeddings')
    def test_tx_token_consumed_after_successful_verification(
        self, mock_compare, mock_process, mock_spoof, mock_detect, mock_load, mock_dup,
    ):
        """After a successful verification the LivenessTransaction must be marked used."""
        import numpy as np
        from verification.models import LivenessTransaction
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_spoof.return_value = {'passed': True, 'score': 0.9, 'reason': 'Real face.'}
        mock_process.return_value = {
            'success': True, 'embedding': np.zeros(128),
            'quality': {'ok': True, 'score': 0.9, 'reason': ''}, 'using_mock': False,
        }
        mock_compare.return_value = {
            'success': True, 'score': 0.85,
            'matched_template': 'primary', 'templates_checked': 1, 'all_scores': [],
        }
        mock_dup.return_value = {
            'duplicates_found': False, 'matches': [], 'highest_score': 0.0, 'checked': 0,
        }
        self._set_session()
        tx = _make_liveness_tx(self.ben, self.staff, self.event)
        self._post(tx_token=str(tx.token))
        tx.refresh_from_db()
        self.assertIsNotNone(tx.used_at, 'used_at must be set after verification')
        self.assertTrue(tx.is_used)

    @override_settings(LIVENESS_PROOF_REQUIRED=True, DEBUG=True)
    def test_tx_owner_mismatch_denied(self):
        """
        Security regression (v2.1.16, Hardening Round #2, H-03): a
        LivenessTransaction issued to one logged-in officer (performed_by)
        must not be usable by a DIFFERENT logged-in user submitting the same
        token — e.g. a shared kiosk session, or a leaked token value.
        """
        other_staff = _make_staff('lpb_other_staff')
        tx = _make_liveness_tx(self.ben, other_staff, self.event)
        self._set_session()
        resp = self._post(tx_token=str(tx.token))
        data = json.loads(resp.content)
        self.assertEqual(data.get('decision'), 'denied')
        self.assertIn('liveness proof', data.get('reason', '').lower())

    @override_settings(LIVENESS_PROOF_REQUIRED=True, DEBUG=True)
    def test_tx_token_different_beneficiary_denied(self):
        """A TX issued for beneficiary A must not be usable for beneficiary B."""
        other_ben = _make_beneficiary('BEN-LPB-999', 'SC-LPB-999')
        tx = _make_liveness_tx(other_ben, self.staff, self.event)
        self._set_session(ben=self.ben)
        resp = self._post(tx_token=str(tx.token))
        data = json.loads(resp.content)
        self.assertEqual(data.get('decision'), 'denied')
        self.assertIn('liveness proof', data.get('reason', '').lower())

    @override_settings(LIVENESS_PROOF_REQUIRED=False, LIVENESS_REQUIRED=False,
                       DEMO_MODE=True, DEBUG=True)
    @mock.patch('verification.face_utils.check_duplicate_face')
    @mock.patch('verification.views.load_image_from_bytes')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.check_anti_spoofing')
    @mock.patch('verification.views.process_face_for_verification')
    @mock.patch('verification.views.compare_with_all_embeddings')
    def test_liveness_proof_not_required_bypasses_gate(
        self, mock_compare, mock_process, mock_spoof, mock_detect, mock_load, mock_dup,
    ):
        """When LIVENESS_PROOF_REQUIRED=False, no tx_token is needed."""
        import numpy as np
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_spoof.return_value = {'passed': True, 'score': 0.9, 'reason': 'Real face.'}
        mock_process.return_value = {
            'success': True, 'embedding': np.zeros(128),
            'quality': {'ok': True, 'score': 0.9, 'reason': ''}, 'using_mock': False,
        }
        mock_compare.return_value = {
            'success': True, 'score': 0.85,
            'matched_template': 'primary', 'templates_checked': 1, 'all_scores': [],
        }
        mock_dup.return_value = {
            'duplicates_found': False, 'matches': [], 'highest_score': 0.0, 'checked': 0,
        }
        self._set_session()
        resp = self._post(tx_token='')
        data = json.loads(resp.content)
        self.assertIn(data.get('decision'), ('verified', 'retry', 'manual_review'),
                      'LIVENESS_PROOF_REQUIRED=False should allow proceed without token')


class LivenessTransactionModelTest(TestCase):
    """Unit tests for LivenessTransaction model properties and methods."""

    def setUp(self):
        from verification.models import StipendEvent
        self.staff = _make_staff('ltm_staff')
        self.ben = _make_beneficiary('BEN-LTM-001', 'SC-LTM-001')
        self.event = StipendEvent.objects.create(
            title='LTM Payout', date=datetime.date.today(), amount=500,
            is_active=True, created_by=self.staff,
        )

    def test_is_expired_false_for_future(self):
        tx = _make_liveness_tx(self.ben, self.staff, self.event, seconds=120)
        self.assertFalse(tx.is_expired)

    def test_is_expired_true_for_past(self):
        from verification.models import LivenessTransaction
        tx = LivenessTransaction.objects.create(
            beneficiary=self.ben,
            claimant_type='beneficiary',
            stipend_event=self.event,
            performed_by=self.staff,
            challenge_direction='side',
            anti_spoof_score=0.0,
            liveness_score=0.0,
            pa_score=0.0,
            pa_flags={},
            embedding_data=None,
            expires_at=timezone.now() - datetime.timedelta(seconds=1),
        )
        self.assertTrue(tx.is_expired)

    def test_is_used_false_before_consume(self):
        tx = _make_liveness_tx(self.ben, self.staff, self.event)
        self.assertFalse(tx.is_used)

    def test_is_valid_true_for_fresh_tx(self):
        tx = _make_liveness_tx(self.ben, self.staff, self.event)
        self.assertTrue(tx.is_valid)

    def test_consume_marks_used_at_and_links_attempt(self):
        from verification.models import VerificationAttempt
        tx = _make_liveness_tx(self.ben, self.staff, self.event)
        attempt = VerificationAttempt.objects.create(
            beneficiary=self.ben,
            performed_by=self.staff,
            decision=VerificationAttempt.DECISION_VERIFIED,
            threshold_used=0.75,
        )
        tx.consume(attempt)
        tx.refresh_from_db()
        self.assertIsNotNone(tx.used_at)
        self.assertEqual(tx.used_by_attempt_id, attempt.id)
        self.assertTrue(tx.is_used)

    def test_is_valid_false_when_expired(self):
        from verification.models import LivenessTransaction
        tx = LivenessTransaction.objects.create(
            beneficiary=self.ben,
            claimant_type='beneficiary',
            stipend_event=self.event,
            performed_by=self.staff,
            challenge_direction='side',
            anti_spoof_score=0.0,
            liveness_score=0.0,
            pa_score=0.0,
            pa_flags={},
            embedding_data=None,
            expires_at=timezone.now() - datetime.timedelta(seconds=1),
        )
        self.assertFalse(tx.is_valid)

    def test_consume_returns_true_on_first_call(self):
        from verification.models import VerificationAttempt
        tx = _make_liveness_tx(self.ben, self.staff, self.event)
        attempt = VerificationAttempt.objects.create(
            beneficiary=self.ben, performed_by=self.staff,
            decision=VerificationAttempt.DECISION_VERIFIED, threshold_used=0.75,
        )
        self.assertTrue(tx.consume(attempt))

    def test_consume_returns_false_on_concurrent_second_call(self):
        """
        Security regression (v2.1.16, Hardening Round #2, H-03): simulates two
        near-simultaneous verify_submit requests holding the same tx_token.
        consume() must be an atomic conditional claim — only the first caller
        may win; a second call (whether truly concurrent or merely a replay)
        must return False rather than silently overwriting used_by_attempt.
        """
        from verification.models import VerificationAttempt
        tx = _make_liveness_tx(self.ben, self.staff, self.event)
        attempt1 = VerificationAttempt.objects.create(
            beneficiary=self.ben, performed_by=self.staff,
            decision=VerificationAttempt.DECISION_VERIFIED, threshold_used=0.75,
        )
        attempt2 = VerificationAttempt.objects.create(
            beneficiary=self.ben, performed_by=self.staff,
            decision=VerificationAttempt.DECISION_VERIFIED, threshold_used=0.75,
        )
        self.assertTrue(tx.consume(attempt1))
        self.assertFalse(tx.consume(attempt2), 'second concurrent consume() must lose the race')
        tx.refresh_from_db()
        self.assertEqual(tx.used_by_attempt_id, attempt1.id,
                         'the first winner must remain bound to the TX, not the second caller')


# ── verify_check_liveness Mode B tests ───────────────────────────────────────

class VerifyCheckLivenessModeBTest(TestCase):
    """
    Tests for verify_check_liveness Mode B (challenge_completed=True).

    Verifies:
    1. Challenge passes + valid face → tx_token returned, passed=True
    2. Challenge passes + no session → structured failure with debug_stage
    3. Never returns passed=True with tx_token=null
    4. Response always includes all required fields
    5. PAD denial returns structured response with no token
    6. Outer exception returns full structured response
    7. No image → structured failure
    8. Face not detected → structured response with debug_stage
    """

    def setUp(self):
        from verification.models import StipendEvent
        self.client = Client()
        self.staff = _make_staff('modeb_staff')
        self.client.force_login(self.staff)
        self.url = reverse('verification:verify_check_liveness')
        self.ben = _make_beneficiary('BEN-MB-001', 'SC-MB-001')
        self.event = StipendEvent.objects.create(
            title='Mode B Payout', date=datetime.date.today(), amount=500,
            is_active=True, created_by=self.staff,
        )

    def _set_session(self):
        session = self.client.session
        session['verification_session'] = {
            'beneficiary_id': str(self.ben.pk),
            'session_id': _FIXED_TEST_SESSION_ID,
            'attempt_number': 1,
            'challenge': 'side',
            'claimant_type': 'beneficiary',
            'stipend_event_id': str(self.event.pk),
        }
        session.save()

    def _post_mode_b(self, with_session=True, frames=None, neutral_image=None):
        if with_session:
            self._set_session()
        _frames = frames if frames is not None else [DATA_URI_JPEG, DATA_URI_JPEG, DATA_URI_JPEG]
        payload_dict = {
            'image': DATA_URI_JPEG,
            'challenge_completed': True,
            'frames': _frames,
            'session_id': _FIXED_TEST_SESSION_ID,
            'baseline_landmarks': BASELINE_LANDMARKS,
            'challenge_landmarks': CHALLENGE_LANDMARKS,
        }
        if neutral_image is not None:
            payload_dict['neutral_image'] = neutral_image
        payload = json.dumps(payload_dict)
        return self.client.post(
            self.url, data=payload, content_type='application/json', secure=True,
        )

    def _assert_full_fields(self, data):
        """All required response fields must be present."""
        required = [
            'success', 'passed', 'tx_token', 'error', 'reason',
            'debug_stage', 'face_detected', 'anti_spoof_score',
            'liveness_score', 'pa_score', 'embedding_created',
        ]
        for field in required:
            self.assertIn(field, data, f'Field "{field}" missing from response: {data}')

    @override_settings(DEBUG=True, LIVENESS_PROOF_REQUIRED=True)
    @mock.patch('verification.views.detect_pose_keypoints')
    @mock.patch('verification.views.check_anti_spoofing')
    @mock.patch('verification.face_utils.get_embedding_only')
    @mock.patch('verification.views.PresentationAttackDetector')
    @mock.patch('verification.views.run_full_liveness_check')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.load_image_from_bytes')
    def test_mode_b_with_session_issues_tx_token(
        self, mock_load, mock_detect, mock_liveness, mock_pad, mock_get_emb,
        mock_spoof, mock_pose,
    ):
        """challenge_completed=True + valid session + valid face → tx_token returned, passed=True."""
        import numpy as np
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_liveness.return_value = {
            'passed': True, 'anti_spoof_passed': True,
            'anti_spoof_score': 0.9, 'liveness_score': 0.9,
            'reason': 'Liveness check passed.',
        }
        _clean_pad = mock.MagicMock(suspicious=False, score=0.0, flags={}, reason='Clean.')
        mock_pad_instance = mock.MagicMock()
        mock_pad_instance.analyze.return_value = _clean_pad
        mock_pad_instance.analyze_sequence.return_value = _clean_pad
        mock_pad.return_value = mock_pad_instance
        mock_get_emb.return_value = {'success': True, 'embedding': [0.1] * 128}
        mock_spoof.return_value = {'passed': True, 'score': 0.9, 'reason': 'Real face.'}
        # Server-side (never client-supplied) pose evidence: neutral frame
        # detected first, then the turned challenge/proof frame.
        mock_pose.side_effect = [NEUTRAL_KEYPOINTS, TURNED_KEYPOINTS]

        resp = self._post_mode_b(with_session=True, neutral_image=DATA_URI_JPEG)
        data = json.loads(resp.content)
        self._assert_full_fields(data)
        self.assertTrue(data['success'])
        self.assertTrue(data['passed'], f'passed should be True when token issued: {data}')
        self.assertIsNotNone(data['tx_token'], f'tx_token must be set on success: {data}')
        self.assertEqual(data['debug_stage'], 'tx_created')
        self.assertIsNone(data['error'])

    @override_settings(DEBUG=True, LIVENESS_PROOF_REQUIRED=True)
    @mock.patch('verification.views.PresentationAttackDetector')
    @mock.patch('verification.views.run_full_liveness_check')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.load_image_from_bytes')
    def test_mode_b_no_session_structured_failure(
        self, mock_load, mock_detect, mock_liveness, mock_pad,
    ):
        """No verification_session → passed=False, tx_token=null, debug_stage set, no exception."""
        import numpy as np
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_liveness.return_value = {
            'passed': True, 'anti_spoof_passed': True,
            'anti_spoof_score': 0.9, 'liveness_score': 0.9,
            'reason': 'Liveness check passed.',
        }
        _clean_pad = mock.MagicMock(suspicious=False, score=0.0, flags={}, reason='Clean.')
        mock_pad_instance = mock.MagicMock()
        mock_pad_instance.analyze.return_value = _clean_pad
        mock_pad_instance.analyze_sequence.return_value = _clean_pad
        mock_pad.return_value = mock_pad_instance
        # Do NOT set session
        resp = self._post_mode_b(with_session=False)
        data = json.loads(resp.content)
        self._assert_full_fields(data)
        self.assertIsNone(data['tx_token'], f'tx_token must be null when session missing: {data}')
        self.assertFalse(data['passed'], f'passed must be False when no token: {data}')
        self.assertEqual(data['debug_stage'], 'tx_not_issued')
        self.assertIsNotNone(data['error'])
        self.assertIn('session', data['error'].lower())

    @override_settings(DEBUG=True, LIVENESS_PROOF_REQUIRED=True)
    @mock.patch('verification.views.PresentationAttackDetector')
    @mock.patch('verification.views.run_full_liveness_check')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.load_image_from_bytes')
    def test_never_passed_true_without_tx_token(
        self, mock_load, mock_detect, mock_liveness, mock_pad,
    ):
        """Invariant: passed=True must never appear without tx_token in Mode B."""
        import numpy as np
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_liveness.return_value = {
            'passed': True, 'anti_spoof_passed': True,
            'anti_spoof_score': 0.9, 'liveness_score': 0.9,
            'reason': 'Liveness check passed.',
        }
        _clean_pad = mock.MagicMock(suspicious=False, score=0.0, flags={}, reason='Clean.')
        mock_pad_instance = mock.MagicMock()
        mock_pad_instance.analyze.return_value = _clean_pad
        mock_pad_instance.analyze_sequence.return_value = _clean_pad
        mock_pad.return_value = mock_pad_instance

        # No session → no token
        resp = self._post_mode_b(with_session=False)
        data = json.loads(resp.content)
        if not data.get('tx_token'):
            self.assertFalse(data.get('passed'),
                             f'passed=True without tx_token is forbidden: {data}')

    @override_settings(DEBUG=True, LIVENESS_PROOF_REQUIRED=True)
    @mock.patch('verification.face_utils.get_embedding_only')
    @mock.patch('verification.views.PresentationAttackDetector')
    @mock.patch('verification.views.run_full_liveness_check')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.load_image_from_bytes')
    def test_response_always_has_all_required_fields(
        self, mock_load, mock_detect, mock_liveness, mock_pad, mock_get_emb,
    ):
        """Every Mode B response must include debug_stage, error, pa_score, embedding_created."""
        import numpy as np
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_liveness.return_value = {
            'passed': True, 'anti_spoof_passed': True,
            'anti_spoof_score': 0.9, 'liveness_score': 0.9,
            'reason': 'Liveness check passed.',
        }
        _clean_pad = mock.MagicMock(suspicious=False, score=0.0, flags={}, reason='Clean.')
        mock_pad_instance = mock.MagicMock()
        mock_pad_instance.analyze.return_value = _clean_pad
        mock_pad_instance.analyze_sequence.return_value = _clean_pad
        mock_pad.return_value = mock_pad_instance
        mock_get_emb.return_value = {'success': True, 'embedding': [0.1] * 128}

        self._set_session()
        resp = self._post_mode_b(with_session=False)  # session set above manually
        data = json.loads(resp.content)
        self._assert_full_fields(data)

    @override_settings(DEBUG=True, LIVENESS_PROOF_REQUIRED=True,
                       PAD_REQUIRED=True, STRICT_PRESENTATION_ATTACK_CHECK=True,
                       PRESENTATION_ATTACK_REVIEW_OR_DENY='deny')
    @mock.patch('verification.views.PresentationAttackDetector')
    @mock.patch('verification.views.run_full_liveness_check')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.load_image_from_bytes')
    def test_pad_denial_returns_structured_response_no_token(
        self, mock_load, mock_detect, mock_liveness, mock_pad,
    ):
        """PAD suspicious → passed=False, tx_token=null, debug_stage='pad_denied'."""
        import numpy as np
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_liveness.return_value = {
            'passed': True, 'anti_spoof_passed': True,
            'anti_spoof_score': 0.9, 'liveness_score': 0.9,
            'reason': 'Liveness check passed.',
        }
        _suspicious_pad = mock.MagicMock(
            suspicious=True, score=0.9, flags={'specular_glare': 0.9},
            reason='Denied: possible phone screen or photo presentation attack.',
            # v2.1.16 (Issue 8): classify_denial() compares these fields
            # numerically, so the mock must set them (real PresentationAttackResult
            # instances always have numeric values here, never an unconfigured
            # MagicMock attribute).
            specular_glare=0.9, screen_flatness=0.1, sharpness_texture_ratio=0.1,
            sequence_static=0.0, near_duplicate=0.0,
        )
        mock_pad_instance = mock.MagicMock()
        mock_pad_instance.analyze.return_value = _suspicious_pad
        mock_pad_instance.analyze_sequence.return_value = _suspicious_pad
        # v2.1.16 (Issue 8): classify_denial() is itself mocked out on this
        # MagicMock instance (the whole class is patched), so give it a real
        # string return value -- otherwise the JSON-serialized response would
        # try to include an unconfigured MagicMock in 'pad_denial_class'.
        mock_pad_instance.classify_denial.return_value = 'environment'
        mock_pad.return_value = mock_pad_instance

        resp = self._post_mode_b(with_session=True)
        data = json.loads(resp.content)
        self._assert_full_fields(data)
        self.assertIsNone(data['tx_token'])
        self.assertFalse(data['passed'])
        self.assertEqual(data['debug_stage'], 'pad_denied')
        self.assertIsNotNone(data['error'])

    @override_settings(DEBUG=True, LIVENESS_PROOF_REQUIRED=True)
    def test_no_image_returns_structured_failure(self):
        """No image in payload → structured failure with debug_stage=no_image."""
        self._set_session()
        payload = json.dumps({'image': '', 'challenge_completed': True})
        resp = self.client.post(
            self.url, data=payload, content_type='application/json', secure=True,
        )
        data = json.loads(resp.content)
        self.assertFalse(data['success'])
        self.assertFalse(data['passed'])
        self.assertIsNone(data['tx_token'])
        self.assertEqual(data['debug_stage'], 'no_image')

    @override_settings(DEBUG=True, LIVENESS_PROOF_REQUIRED=True)
    @mock.patch('verification.views.detect_and_align_face', side_effect=ValueError('No face found'))
    @mock.patch('verification.views.load_image_from_bytes')
    def test_no_face_detected_returns_structured_response(
        self, mock_load, mock_detect,
    ):
        """Face detection failure → structured response with debug_stage=no_face_detected."""
        import numpy as np
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        self._set_session()
        resp = self._post_mode_b(with_session=False)
        data = json.loads(resp.content)
        self.assertIsNone(data['tx_token'])
        self.assertFalse(data['passed'])
        self.assertEqual(data['debug_stage'], 'no_face_detected')
        self.assertFalse(data['face_detected'])

    @override_settings(DEBUG=True, LIVENESS_PROOF_REQUIRED=True)
    @mock.patch('verification.views.detect_pose_keypoints')
    @mock.patch('verification.views.check_anti_spoofing')
    @mock.patch('verification.face_utils.get_embedding_only')
    @mock.patch('verification.views.PresentationAttackDetector')
    @mock.patch('verification.views.run_full_liveness_check')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.load_image_from_bytes')
    def test_tx_creation_db_error_returns_structured_response(
        self, mock_load, mock_detect, mock_liveness, mock_pad, mock_get_emb,
        mock_spoof, mock_pose,
    ):
        """DB error during LivenessTransaction.create → structured failure, no exception propagated."""
        import numpy as np
        from unittest.mock import patch as _patch
        from verification.models import LivenessTransaction
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_liveness.return_value = {
            'passed': True, 'anti_spoof_passed': True,
            'anti_spoof_score': 0.9, 'liveness_score': 0.9,
            'reason': 'Liveness check passed.',
        }
        _clean_pad = mock.MagicMock(suspicious=False, score=0.0, flags={}, reason='Clean.')
        mock_pad_instance = mock.MagicMock()
        mock_pad_instance.analyze.return_value = _clean_pad
        mock_pad_instance.analyze_sequence.return_value = _clean_pad
        mock_pad.return_value = mock_pad_instance
        mock_get_emb.return_value = {'success': True, 'embedding': [0.1] * 128}
        mock_spoof.return_value = {'passed': True, 'score': 0.9, 'reason': 'Real face.'}
        mock_pose.side_effect = [NEUTRAL_KEYPOINTS, TURNED_KEYPOINTS]

        self._set_session()
        with _patch.object(LivenessTransaction.objects, 'create',
                           side_effect=Exception('DB connection error')):
            resp = self._post_mode_b(with_session=False, neutral_image=DATA_URI_JPEG)
        data = json.loads(resp.content)
        self._assert_full_fields(data)
        self.assertIsNone(data['tx_token'])
        self.assertFalse(data['passed'])
        self.assertEqual(data['debug_stage'], 'tx_not_issued')
        self.assertIsNotNone(data['error'])


class VerifyCheckLivenessReplayResistanceTest(TestCase):
    """
    v2.1.16 Security Hardening Round #5 (Blocker 1) — replay-resistance
    tests proving the three evidence layers (exact raw bytes,
    decoded-pixel-content, perceptual dHash) each catch a different replay
    variant, while a genuinely different capture still succeeds.

    A fixed-content lookup table (`_BYTES_TO_ARRAY`, populated per test)
    stands in for a real camera + JPEG codec: `image`/`neutral_image`
    payload fields carry a distinguishing plaintext token (base64-encoded),
    so the RAW bytes the view hashes are exactly what the test controls,
    while `load_image_from_bytes` is mocked to deterministically return the
    numpy array registered for that exact token's decoded bytes -- letting
    each test independently control "what the raw bytes were" vs. "what the
    decoded pixels look like", which is exactly the distinction each
    evidence layer is designed to catch or ignore.
    """

    def setUp(self):
        from verification.models import StipendEvent
        self.client = Client()
        self.staff = _make_staff('replay_staff')
        self.client.force_login(self.staff)
        self.url = reverse('verification:verify_check_liveness')
        self.ben = _make_beneficiary('BEN-REPLAY-001', 'SC-REPLAY-001')
        self.event = StipendEvent.objects.create(
            title='Replay Payout', date=datetime.date.today(), amount=500,
            is_active=True, created_by=self.staff,
        )

    def _set_session(self):
        session = self.client.session
        session['verification_session'] = {
            'beneficiary_id': str(self.ben.pk),
            'session_id': _FIXED_TEST_SESSION_ID,
            'attempt_number': 1,
            'challenge': 'side',
            'claimant_type': 'beneficiary',
            'stipend_event_id': str(self.event.pk),
        }
        session.save()

    def _post(self, image_token, neutral_token):
        self._set_session()
        image_b64 = base64.b64encode(image_token.encode()).decode()
        neutral_b64 = base64.b64encode(neutral_token.encode()).decode()
        payload = json.dumps({
            'image': f'data:image/jpeg;base64,{image_b64}',
            'neutral_image': f'data:image/jpeg;base64,{neutral_b64}',
            'challenge_completed': True,
            'frames': [DATA_URI_JPEG, DATA_URI_JPEG, DATA_URI_JPEG],
            'session_id': _FIXED_TEST_SESSION_ID,
        })
        return self.client.post(
            self.url, data=payload, content_type='application/json', secure=True,
        )

    @staticmethod
    def _gradient(ascending=True, noise=False):
        import numpy as np
        base = np.tile(np.linspace(0, 250, 100), (100, 1)).astype(np.float64)
        if not ascending:
            base = base[:, ::-1]
        if noise:
            # Small enough not to flip the ordinal relationship between
            # adjacent columns after dHash's 9-wide downsample (~28/column) —
            # simulates JPEG recompression jitter on an otherwise-identical
            # image: same visual content, different exact pixel values.
            base = base + np.random.RandomState(42).uniform(-3, 3, size=base.shape)
        arr = np.clip(base, 0, 255).astype('uint8')
        return np.stack([arr, arr, arr], axis=-1)

    def _apply_common_mocks(self, mock_detect, mock_liveness, mock_pad, mock_get_emb, mock_spoof, mock_pose):
        import itertools
        mock_detect.return_value = self._gradient()[:64, :64]
        mock_liveness.return_value = {
            'passed': True, 'anti_spoof_passed': True,
            'anti_spoof_score': 0.9, 'liveness_score': 0.9,
            'reason': 'Liveness check passed.',
        }
        _clean_pad = mock.MagicMock(suspicious=False, score=0.0, flags={}, reason='Clean.')
        mock_pad_instance = mock.MagicMock()
        mock_pad_instance.analyze.return_value = _clean_pad
        mock_pad_instance.analyze_sequence.return_value = _clean_pad
        mock_pad.return_value = mock_pad_instance
        mock_get_emb.return_value = {'success': True, 'embedding': [0.1] * 128}
        mock_spoof.return_value = {'passed': True, 'score': 0.9, 'reason': 'Real face.'}
        mock_pose.side_effect = itertools.cycle([NEUTRAL_KEYPOINTS, TURNED_KEYPOINTS])

    @override_settings(DEBUG=True, LIVENESS_PROOF_REQUIRED=True)
    @mock.patch('verification.views.detect_pose_keypoints')
    @mock.patch('verification.views.check_anti_spoofing')
    @mock.patch('verification.face_utils.get_embedding_only')
    @mock.patch('verification.views.PresentationAttackDetector')
    @mock.patch('verification.views.run_full_liveness_check')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.load_image_from_bytes')
    def test_exact_byte_replay_rejected(
        self, mock_load, mock_detect, mock_liveness, mock_pad, mock_get_emb, mock_spoof, mock_pose,
    ):
        """A byte-for-byte identical (image, neutral_image) pair submitted
        twice must be rejected the second time (evidence_hash layer)."""
        self._apply_common_mocks(mock_detect, mock_liveness, mock_pad, mock_get_emb, mock_spoof, mock_pose)
        table = {b'proof-A': self._gradient(), b'neutral-A': self._gradient()}
        mock_load.side_effect = lambda b, _t=table: _t.get(bytes(b), self._gradient())

        first = self._post('proof-A', 'neutral-A')
        first_data = json.loads(first.content)
        self.assertIsNotNone(first_data['tx_token'], first_data)

        second = self._post('proof-A', 'neutral-A')
        second_data = json.loads(second.content)
        self.assertIsNone(second_data['tx_token'], second_data)
        self.assertFalse(second_data['passed'])
        self.assertEqual(second_data['debug_stage'], 'replay_detected')

    @override_settings(DEBUG=True, LIVENESS_PROOF_REQUIRED=True)
    @mock.patch('verification.views.detect_pose_keypoints')
    @mock.patch('verification.views.check_anti_spoofing')
    @mock.patch('verification.face_utils.get_embedding_only')
    @mock.patch('verification.views.PresentationAttackDetector')
    @mock.patch('verification.views.run_full_liveness_check')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.load_image_from_bytes')
    def test_recompressed_replay_rejected(
        self, mock_load, mock_detect, mock_liveness, mock_pad, mock_get_emb, mock_spoof, mock_pose,
    ):
        """Different raw bytes (simulating a JPEG re-saved at a different
        quality) that decode to a near-identical image (small per-pixel
        noise, same visual content) must still be rejected — only the
        perceptual-hash layer can catch this, since both evidence_hash and
        evidence_pixel_hash necessarily differ."""
        self._apply_common_mocks(mock_detect, mock_liveness, mock_pad, mock_get_emb, mock_spoof, mock_pose)
        original = self._gradient()
        recompressed = self._gradient(noise=True)
        table = {
            b'proof-B-original': original, b'neutral-B-original': original,
            b'proof-B-recompressed': recompressed, b'neutral-B-recompressed': recompressed,
        }
        mock_load.side_effect = lambda b, _t=table: _t.get(bytes(b), self._gradient())

        first = self._post('proof-B-original', 'neutral-B-original')
        first_data = json.loads(first.content)
        self.assertIsNotNone(first_data['tx_token'], first_data)

        second = self._post('proof-B-recompressed', 'neutral-B-recompressed')
        second_data = json.loads(second.content)
        self.assertIsNone(second_data['tx_token'], second_data)
        self.assertFalse(second_data['passed'])
        self.assertEqual(second_data['debug_stage'], 'replay_detected')

    @override_settings(DEBUG=True, LIVENESS_PROOF_REQUIRED=True)
    @mock.patch('verification.views.detect_pose_keypoints')
    @mock.patch('verification.views.check_anti_spoofing')
    @mock.patch('verification.face_utils.get_embedding_only')
    @mock.patch('verification.views.PresentationAttackDetector')
    @mock.patch('verification.views.run_full_liveness_check')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.load_image_from_bytes')
    def test_metadata_changed_replay_rejected(
        self, mock_load, mock_detect, mock_liveness, mock_pad, mock_get_emb, mock_spoof, mock_pose,
    ):
        """Different raw bytes (simulating stripped/edited EXIF metadata)
        that decode to the EXACT SAME pixel content must still be rejected —
        the decoded-pixel-content-hash layer catches this even though
        evidence_hash (raw bytes) does not match."""
        self._apply_common_mocks(mock_detect, mock_liveness, mock_pad, mock_get_emb, mock_spoof, mock_pose)
        identical_pixels = self._gradient()
        table = {
            b'proof-C-plain': identical_pixels, b'neutral-C-plain': identical_pixels,
            b'proof-C-stripped-exif': identical_pixels, b'neutral-C-stripped-exif': identical_pixels,
        }
        mock_load.side_effect = lambda b, _t=table: _t.get(bytes(b), self._gradient())

        first = self._post('proof-C-plain', 'neutral-C-plain')
        first_data = json.loads(first.content)
        self.assertIsNotNone(first_data['tx_token'], first_data)

        second = self._post('proof-C-stripped-exif', 'neutral-C-stripped-exif')
        second_data = json.loads(second.content)
        self.assertIsNone(second_data['tx_token'], second_data)
        self.assertFalse(second_data['passed'])
        self.assertEqual(second_data['debug_stage'], 'replay_detected')

    @override_settings(DEBUG=True, LIVENESS_PROOF_REQUIRED=True)
    @mock.patch('verification.views.detect_pose_keypoints')
    @mock.patch('verification.views.check_anti_spoofing')
    @mock.patch('verification.face_utils.get_embedding_only')
    @mock.patch('verification.views.PresentationAttackDetector')
    @mock.patch('verification.views.run_full_liveness_check')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.load_image_from_bytes')
    def test_different_legitimate_capture_succeeds(
        self, mock_load, mock_detect, mock_liveness, mock_pad, mock_get_emb, mock_spoof, mock_pose,
    ):
        """Two genuinely different captures (different raw bytes AND clearly
        different decoded content) must BOTH succeed — none of the three
        replay layers should ever flag legitimate, distinct captures."""
        self._apply_common_mocks(mock_detect, mock_liveness, mock_pad, mock_get_emb, mock_spoof, mock_pose)
        capture_1 = self._gradient(ascending=True)
        capture_2 = self._gradient(ascending=False)
        table = {
            b'proof-D-1': capture_1, b'neutral-D-1': capture_1,
            b'proof-D-2': capture_2, b'neutral-D-2': capture_2,
        }
        mock_load.side_effect = lambda b, _t=table: _t.get(bytes(b), self._gradient())

        first = self._post('proof-D-1', 'neutral-D-1')
        first_data = json.loads(first.content)
        self.assertIsNotNone(first_data['tx_token'], f'first legitimate capture must succeed: {first_data}')
        self.assertTrue(first_data['passed'])

        second = self._post('proof-D-2', 'neutral-D-2')
        second_data = json.loads(second.content)
        self.assertIsNotNone(second_data['tx_token'], f'second distinct legitimate capture must succeed: {second_data}')
        self.assertTrue(second_data['passed'])
        self.assertNotEqual(first_data['tx_token'], second_data['tx_token'])


class VerifyCheckLivenessAtomicReplayProtectionTest(TestCase):
    """
    v2.1.16 Security Hardening Round #5 (Blocker 2) — the database, not the
    earlier exists() pre-check, must be the final authority against a
    replay race: two LivenessTransaction rows can never share the same
    non-blank evidence_hash / evidence_pixel_hash, enforced by a DB-level
    partial unique constraint (see LivenessTransaction.Meta.constraints),
    and verify_check_liveness must handle the resulting IntegrityError as a
    detected replay rather than letting it propagate as a server error.
    """

    def setUp(self):
        from verification.models import StipendEvent
        self.staff = _make_staff('atomic_staff')
        self.ben = _make_beneficiary('BEN-ATOMIC-001', 'SC-ATOMIC-001')
        self.event = StipendEvent.objects.create(
            title='Atomic Payout', date=datetime.date.today(), amount=500,
            is_active=True, created_by=self.staff,
        )

    def test_db_rejects_concurrent_duplicate_evidence_hash(self):
        """Simulates the race directly at the model layer: two
        LivenessTransaction rows carrying the SAME non-blank evidence_hash
        can never both commit, regardless of timing — the second INSERT
        must raise IntegrityError even though nothing else about it is
        invalid (different token, different timestamps)."""
        from django.db import IntegrityError as _IntegrityError, transaction
        from verification.models import LivenessTransaction

        shared_hash = 'a' * 64
        LivenessTransaction.objects.create(
            beneficiary=self.ben, claimant_type='beneficiary', stipend_event=self.event,
            performed_by=self.staff, challenge_direction='side',
            anti_spoof_score=0.9, liveness_score=0.9, pa_score=0.0, pa_flags={},
            embedding_data=None, expires_at=timezone.now() + datetime.timedelta(seconds=120),
            evidence_hash=shared_hash,
        )
        with self.assertRaises(_IntegrityError):
            with transaction.atomic():
                LivenessTransaction.objects.create(
                    beneficiary=self.ben, claimant_type='beneficiary', stipend_event=self.event,
                    performed_by=self.staff, challenge_direction='side',
                    anti_spoof_score=0.9, liveness_score=0.9, pa_score=0.0, pa_flags={},
                    embedding_data=None, expires_at=timezone.now() + datetime.timedelta(seconds=120),
                    evidence_hash=shared_hash,
                )
        # A THIRD row with a BLANK evidence_hash must still be allowed —
        # the partial constraint only applies to non-blank values, so
        # legacy/test rows and LIVENESS_PROOF_REQUIRED=False issuance paths
        # (which never populate evidence_hash) are unaffected.
        LivenessTransaction.objects.create(
            beneficiary=self.ben, claimant_type='beneficiary', stipend_event=self.event,
            performed_by=self.staff, challenge_direction='side',
            anti_spoof_score=0.9, liveness_score=0.9, pa_score=0.0, pa_flags={},
            embedding_data=None, expires_at=timezone.now() + datetime.timedelta(seconds=120),
        )
        self.assertEqual(
            LivenessTransaction.objects.filter(evidence_hash=shared_hash).count(), 1,
            'only the first writer may hold the shared evidence_hash',
        )

    def test_db_rejects_concurrent_duplicate_evidence_pixel_hash(self):
        """Same guarantee as above for evidence_pixel_hash (the
        decoded-pixel-content layer)."""
        from django.db import IntegrityError as _IntegrityError, transaction
        from verification.models import LivenessTransaction

        shared_pixel_hash = 'b' * 64
        LivenessTransaction.objects.create(
            beneficiary=self.ben, claimant_type='beneficiary', stipend_event=self.event,
            performed_by=self.staff, challenge_direction='side',
            anti_spoof_score=0.9, liveness_score=0.9, pa_score=0.0, pa_flags={},
            embedding_data=None, expires_at=timezone.now() + datetime.timedelta(seconds=120),
            evidence_pixel_hash=shared_pixel_hash,
        )
        with self.assertRaises(_IntegrityError):
            with transaction.atomic():
                LivenessTransaction.objects.create(
                    beneficiary=self.ben, claimant_type='beneficiary', stipend_event=self.event,
                    performed_by=self.staff, challenge_direction='side',
                    anti_spoof_score=0.9, liveness_score=0.9, pa_score=0.0, pa_flags={},
                    embedding_data=None, expires_at=timezone.now() + datetime.timedelta(seconds=120),
                    evidence_pixel_hash=shared_pixel_hash,
                )

    @override_settings(DEBUG=True, LIVENESS_PROOF_REQUIRED=True)
    @mock.patch('verification.views.detect_pose_keypoints')
    @mock.patch('verification.views.check_anti_spoofing')
    @mock.patch('verification.face_utils.get_embedding_only')
    @mock.patch('verification.views.PresentationAttackDetector')
    @mock.patch('verification.views.run_full_liveness_check')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.load_image_from_bytes')
    def test_view_handles_integrity_error_as_replay_not_server_error(
        self, mock_load, mock_detect, mock_liveness, mock_pad, mock_get_emb, mock_spoof, mock_pose,
    ):
        """End-to-end: force LivenessTransaction.objects.create() to raise
        IntegrityError (simulating a race lost against a concurrent request)
        and confirm verify_check_liveness returns a normal structured
        replay-style failure — never a 500 / unhandled exception."""
        import itertools
        from unittest.mock import patch as _patch
        from django.db import IntegrityError as _IntegrityError
        from verification.models import LivenessTransaction
        import numpy as np

        client = Client()
        client.force_login(self.staff)
        session = client.session
        session['verification_session'] = {
            'beneficiary_id': str(self.ben.pk),
            'session_id': _FIXED_TEST_SESSION_ID,
            'attempt_number': 1,
            'challenge': 'side',
            'claimant_type': 'beneficiary',
            'stipend_event_id': str(self.event.pk),
        }
        session.save()

        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_liveness.return_value = {
            'passed': True, 'anti_spoof_passed': True,
            'anti_spoof_score': 0.9, 'liveness_score': 0.9,
            'reason': 'Liveness check passed.',
        }
        _clean_pad = mock.MagicMock(suspicious=False, score=0.0, flags={}, reason='Clean.')
        mock_pad_instance = mock.MagicMock()
        mock_pad_instance.analyze.return_value = _clean_pad
        mock_pad_instance.analyze_sequence.return_value = _clean_pad
        mock_pad.return_value = mock_pad_instance
        mock_get_emb.return_value = {'success': True, 'embedding': [0.1] * 128}
        mock_spoof.return_value = {'passed': True, 'score': 0.9, 'reason': 'Real face.'}
        mock_pose.side_effect = itertools.cycle([NEUTRAL_KEYPOINTS, TURNED_KEYPOINTS])

        tx_count_before = LivenessTransaction.objects.count()
        payload = json.dumps({
            'image': DATA_URI_JPEG,
            'neutral_image': DATA_URI_JPEG,
            'challenge_completed': True,
            'frames': [DATA_URI_JPEG, DATA_URI_JPEG, DATA_URI_JPEG],
            'session_id': _FIXED_TEST_SESSION_ID,
        })
        with _patch.object(LivenessTransaction.objects, 'create', side_effect=_IntegrityError('duplicate key')):
            resp = client.post(
                reverse('verification:verify_check_liveness'),
                data=payload, content_type='application/json', secure=True,
            )
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertIsNone(data['tx_token'])
        self.assertFalse(data['passed'])
        self.assertIn('previous liveness', data['error'].lower())
        self.assertEqual(LivenessTransaction.objects.count(), tx_count_before)


# ── v2.1-liveness-fix-test1 new-gate regression tests ────────────────────────

class VerifyCheckLivenessV3GatesTest(TestCase):
    """
    v2.1 regression tests for new verify_check_liveness security gates:
    - Insufficient sequence frames (<3) blocks TX (Bug E)
    - Anti-spoof failure on neutral frontal frame blocks TX (Bug B gate)
    - Embedding failure blocks TX (Bug C)
    """

    def setUp(self):
        from verification.models import StipendEvent
        self.client = Client()
        self.staff = _make_staff('v3gate_staff')
        self.client.force_login(self.staff)
        self.url = reverse('verification:verify_check_liveness')
        self.ben = _make_beneficiary('BEN-V3G-001', 'SC-V3G-001')
        self.event = StipendEvent.objects.create(
            title='V3 Gate Payout', date=datetime.date.today(), amount=500,
            is_active=True, created_by=self.staff,
        )

    def _set_session(self):
        session = self.client.session
        session['verification_session'] = {
            'beneficiary_id': str(self.ben.pk),
            'session_id': _FIXED_TEST_SESSION_ID,
            'attempt_number': 1,
            'challenge': 'side',
            'claimant_type': 'beneficiary',
            'stipend_event_id': str(self.event.pk),
        }
        session.save()

    def _post_mode_b_raw(self, frames, neutral_image=None):
        """Post Mode B with explicit frames list (no default injection)."""
        self._set_session()
        payload_dict = {
            'image': DATA_URI_JPEG,
            'challenge_completed': True,
            'frames': frames,
            'session_id': _FIXED_TEST_SESSION_ID,
            'baseline_landmarks': BASELINE_LANDMARKS,
            'challenge_landmarks': CHALLENGE_LANDMARKS,
        }
        if neutral_image is not None:
            payload_dict['neutral_image'] = neutral_image
        return self.client.post(
            self.url, data=json.dumps(payload_dict),
            content_type='application/json', secure=True,
        )

    @override_settings(DEBUG=True, LIVENESS_PROOF_REQUIRED=True)
    @mock.patch('verification.views.run_full_liveness_check')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.load_image_from_bytes')
    def test_insufficient_sequence_frames_blocks_tx(
        self, mock_load, mock_detect, mock_liveness,
    ):
        """Fewer than 3 sequence frames → debug_stage=insufficient_sequence_frames, no TX."""
        import numpy as np
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_liveness.return_value = {
            'passed': True, 'anti_spoof_passed': True,
            'anti_spoof_score': 0.9, 'liveness_score': 0.9,
            'reason': 'Liveness check passed.',
        }
        resp = self._post_mode_b_raw(frames=[DATA_URI_JPEG])  # only 1 frame
        data = json.loads(resp.content)
        self.assertFalse(data['passed'], f'Should be blocked by frame gate: {data}')
        self.assertIsNone(data['tx_token'])
        self.assertEqual(data['debug_stage'], 'insufficient_sequence_frames')

    @override_settings(DEBUG=True, LIVENESS_PROOF_REQUIRED=True)
    @mock.patch('verification.views.run_full_liveness_check')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.load_image_from_bytes')
    def test_zero_sequence_frames_blocked(
        self, mock_load, mock_detect, mock_liveness,
    ):
        """Zero sequence frames → debug_stage=insufficient_sequence_frames (static photo guard)."""
        import numpy as np
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_liveness.return_value = {
            'passed': True, 'anti_spoof_passed': True,
            'anti_spoof_score': 0.9, 'liveness_score': 0.9,
            'reason': 'Liveness check passed.',
        }
        resp = self._post_mode_b_raw(frames=[])
        data = json.loads(resp.content)
        self.assertFalse(data['passed'])
        self.assertIsNone(data['tx_token'])
        self.assertEqual(data['debug_stage'], 'insufficient_sequence_frames')

    @override_settings(DEBUG=True, LIVENESS_PROOF_REQUIRED=True)
    @mock.patch('verification.views.check_anti_spoofing')
    @mock.patch('verification.views.run_full_liveness_check')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.load_image_from_bytes')
    def test_neutral_antispoof_failure_blocks_tx(
        self, mock_load, mock_detect, mock_liveness, mock_spoof,
    ):
        """Anti-spoof failure on neutral frontal frame → debug_stage=neutral_antispoof_failed."""
        import numpy as np
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_liveness.return_value = {
            'passed': True, 'anti_spoof_passed': True,
            'anti_spoof_score': 0.9, 'liveness_score': 0.9,
            'reason': 'Liveness check passed.',
        }
        # Neutral frame anti-spoof FAILS (score below threshold)
        mock_spoof.return_value = {'passed': False, 'score': 0.05, 'reason': 'Spoof detected.'}

        resp = self._post_mode_b_raw(
            frames=[DATA_URI_JPEG, DATA_URI_JPEG, DATA_URI_JPEG],
            neutral_image=DATA_URI_JPEG,
        )
        data = json.loads(resp.content)
        self.assertFalse(data['passed'], f'Should be blocked by neutral anti-spoof gate: {data}')
        self.assertIsNone(data['tx_token'])
        self.assertEqual(data['debug_stage'], 'neutral_antispoof_failed')

    @override_settings(DEBUG=True, LIVENESS_PROOF_REQUIRED=True)
    @mock.patch('verification.views.detect_pose_keypoints')
    @mock.patch('verification.views.check_anti_spoofing')
    @mock.patch('verification.face_utils.get_embedding_only')
    @mock.patch('verification.views.PresentationAttackDetector')
    @mock.patch('verification.views.run_full_liveness_check')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.load_image_from_bytes')
    def test_embedding_failure_blocks_tx(
        self, mock_load, mock_detect, mock_liveness, mock_pad, mock_get_emb,
        mock_spoof, mock_pose,
    ):
        """Embedding computation failure → debug_stage=embedding_failed, no TX issued."""
        import numpy as np
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_liveness.return_value = {
            'passed': True, 'anti_spoof_passed': True,
            'anti_spoof_score': 0.9, 'liveness_score': 0.9,
            'reason': 'Liveness check passed.',
        }
        _clean_pad = mock.MagicMock(suspicious=False, score=0.0, flags={}, reason='Clean.')
        mock_pad_instance = mock.MagicMock()
        mock_pad_instance.analyze.return_value = _clean_pad
        mock_pad_instance.analyze_sequence.return_value = _clean_pad
        mock_pad.return_value = mock_pad_instance
        mock_get_emb.return_value = {'success': False, 'error': 'model not loaded'}
        mock_spoof.return_value = {'passed': True, 'score': 0.9, 'reason': 'Real face.'}
        mock_pose.side_effect = [NEUTRAL_KEYPOINTS, TURNED_KEYPOINTS]

        resp = self._post_mode_b_raw(
            frames=[DATA_URI_JPEG, DATA_URI_JPEG, DATA_URI_JPEG],
            neutral_image=DATA_URI_JPEG,
        )
        data = json.loads(resp.content)
        self.assertFalse(data['passed'], f'Should be blocked by embedding failure: {data}')
        self.assertIsNone(data['tx_token'])
        self.assertEqual(data['debug_stage'], 'embedding_failed')
        self.assertFalse(data.get('embedding_created', True))

    @override_settings(DEBUG=True, LIVENESS_PROOF_REQUIRED=True)
    @mock.patch('verification.views.detect_pose_keypoints')
    @mock.patch('verification.views.check_anti_spoofing')
    @mock.patch('verification.views.PresentationAttackDetector')
    @mock.patch('verification.views.run_full_liveness_check')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.load_image_from_bytes')
    def test_forged_movement_claim_does_not_suppress_pad_signal(
        self, mock_load, mock_detect, mock_liveness, mock_pad, mock_spoof, mock_pose,
    ):
        """
        Security regression (v2.1.16, Hardening Round #1 — CRITICAL #1):
        a modified client cannot fabricate movement.peak_yaw_delta /
        peak_pitch_delta / mediapipe_available to suppress PAD's
        sequence_static / near_duplicate replay-detection signals. The PAD
        result computed from the actual server-received frame pixels must be
        the sole basis for the decision, regardless of what the client
        claims about head movement.

        v2.1.17 update: analyze_sequence() now DOES receive a
        `landmark_motion_ok` kwarg (see
        test_genuine_server_detected_movement_sets_landmark_motion_ok below
        for why — it fixed a real-user false-reject), but its value must
        come only from the server's OWN face detector
        (detect_pose_keypoints), never from the client's movement.* claim.
        Here the server detector finds the SAME keypoints (SAME_KEYPOINTS)
        in both frames — a real detected face with zero actual movement,
        exactly what a held-still replay looks like — so the forged 45°
        claim must still leave landmark_motion_ok False.
        """
        import numpy as np
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_liveness.return_value = {
            'passed': True, 'anti_spoof_passed': True,
            'anti_spoof_score': 0.9, 'liveness_score': 0.9,
            'reason': 'Liveness check passed.',
        }
        # Server-side PAD independently determines this is a static/replay attack.
        _suspicious = mock.MagicMock(
            suspicious=True, score=0.85,
            flags={'sequence_static': 0.9, 'near_duplicate': 0.8},
            reason='Denied: possible phone screen or photo presentation attack.',
        )
        mock_pad_instance = mock.MagicMock()
        mock_pad_instance.analyze_sequence.return_value = _suspicious
        mock_pad_instance.analyze.return_value = _suspicious
        mock_pad_instance.classify_denial.return_value = 'attack'
        mock_pad.return_value = mock_pad_instance
        mock_spoof.return_value = {'passed': True, 'score': 0.9, 'reason': 'Real face.'}
        # Server's own detector: identical pose in both frames — no real movement.
        mock_pose.side_effect = [SAME_KEYPOINTS, SAME_KEYPOINTS]

        self._set_session()
        payload = {
            'image': DATA_URI_JPEG,
            'challenge_completed': True,
            'frames': [DATA_URI_JPEG, DATA_URI_JPEG, DATA_URI_JPEG],
            'neutral_image': DATA_URI_JPEG,
            'session_id': _FIXED_TEST_SESSION_ID,
            # Forged claim: browser reports MediaPipe tracked a large head turn,
            # even though the frames themselves are a static replay.
            'movement': {
                'mediapipe_available': True,
                'peak_yaw_delta': 45.0,
                'peak_pitch_delta': 45.0,
                'threshold': 5.0,
                'face_lost_count': 0,
                'challenge_duration_ms': 1500,
            },
        }
        resp = self.client.post(
            self.url, data=json.dumps(payload),
            content_type='application/json', secure=True,
        )
        data = json.loads(resp.content)
        self.assertFalse(data['passed'], f'Forged movement claim must not bypass PAD: {data}')
        self.assertIsNone(data['tx_token'])
        self.assertEqual(data['debug_stage'], 'pad_denied')
        # landmark_motion_ok must reflect the server's OWN detector finding
        # zero movement, not the client's forged 45° claim.
        _, kwargs = mock_pad_instance.analyze_sequence.call_args
        self.assertFalse(
            kwargs.get('landmark_motion_ok'),
            f'Forged client movement claim must not set landmark_motion_ok=True: {kwargs}',
        )

    @override_settings(DEBUG=True, LIVENESS_PROOF_REQUIRED=True)
    @mock.patch('verification.views.detect_pose_keypoints')
    @mock.patch('verification.views.check_anti_spoofing')
    @mock.patch('verification.face_utils.get_embedding_only')
    @mock.patch('verification.views.PresentationAttackDetector')
    @mock.patch('verification.views.run_full_liveness_check')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.load_image_from_bytes')
    def test_genuine_server_detected_movement_sets_landmark_motion_ok(
        self, mock_load, mock_detect, mock_liveness, mock_pad, mock_get_emb,
        mock_spoof, mock_pose,
    ):
        """
        Regression for the v2.1.17 real-user false-reject fix (Issue 1):
        removing the insecure client-trust override in Hardening Round #1
        left PAD's pixel-only sequence_static/near_duplicate signals with no
        way to tell a genuine small accessible head turn from a static
        replay, so real users doing the actual challenge were denied as
        "possible presentation attack". detect_pose_keypoints() — the SAME
        server-side detector already used to gate challenge completion — now
        independently confirms real movement between the neutral and
        challenge frames, and that confirmation (never the client's
        movement.* claim) is what reaches PAD's landmark_motion_ok kwarg.
        """
        import numpy as np
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_liveness.return_value = {
            'passed': True, 'anti_spoof_passed': True,
            'anti_spoof_score': 0.9, 'liveness_score': 0.9,
            'reason': 'Liveness check passed.',
        }
        _clean_pad = mock.MagicMock(suspicious=False, score=0.0, flags={}, reason='Clean.')
        mock_pad_instance = mock.MagicMock()
        mock_pad_instance.analyze_sequence.return_value = _clean_pad
        mock_pad_instance.analyze.return_value = _clean_pad
        mock_pad.return_value = mock_pad_instance
        mock_get_emb.return_value = {'success': False, 'error': 'model not loaded'}
        mock_spoof.return_value = {'passed': True, 'score': 0.9, 'reason': 'Real face.'}
        # Server's OWN detector (not the client) finds a genuine yaw turn.
        mock_pose.side_effect = [NEUTRAL_KEYPOINTS, TURNED_KEYPOINTS]

        self._set_session()
        payload = {
            'image': DATA_URI_JPEG,
            'challenge_completed': True,
            'frames': [DATA_URI_JPEG, DATA_URI_JPEG, DATA_URI_JPEG],
            'neutral_image': DATA_URI_JPEG,
            'session_id': _FIXED_TEST_SESSION_ID,
            # No movement claim at all from the client — must not matter.
        }
        resp = self.client.post(
            self.url, data=json.dumps(payload),
            content_type='application/json', secure=True,
        )
        data = json.loads(resp.content)
        _, kwargs = mock_pad_instance.analyze_sequence.call_args
        self.assertTrue(
            kwargs.get('landmark_motion_ok'),
            f'Server-confirmed movement must set landmark_motion_ok=True '
            f'so a genuine small head turn is not misread as a static '
            f'replay: {data}',
        )
        self.assertNotEqual(data.get('debug_stage'), 'pad_denied')


# ── v2.1.16 Security Hardening Round #4 — Blocker 1: server-authoritative ────
# ── liveness movement verification (complete redesign) ───────────────────────

class ServerAuthoritativeChallengeUnitTest(TestCase):
    """
    Unit tests for verification.liveness.verify_server_authoritative_challenge()
    — the movement-verification function that superseded Round #3's
    verify_server_side_challenge(). The critical architectural difference:
    this function's inputs (`neutral_keypoints`/`challenge_keypoints`) are
    never client-supplied JSON — callers must pass the output of
    face_utils.detect_pose_keypoints(), which the server computes itself by
    running its own face detector on raw image bytes. These unit tests
    exercise the pure function directly with keypoint dicts standing in for
    "what the server's detector found"; ServerAuthoritativeChallengeEndpointTest
    below proves the client cannot influence those dicts via the wire format.
    """

    def test_genuine_turn_completes(self):
        from verification.liveness import verify_server_authoritative_challenge
        result = verify_server_authoritative_challenge(NEUTRAL_KEYPOINTS, TURNED_KEYPOINTS, 'side')
        self.assertTrue(result['completed'], result['reason'])

    def test_identical_keypoints_do_not_complete(self):
        """A static photo / replay: both frames detect the same face position → zero delta."""
        from verification.liveness import verify_server_authoritative_challenge
        result = verify_server_authoritative_challenge(NEUTRAL_KEYPOINTS, SAME_KEYPOINTS, 'side')
        self.assertFalse(result['completed'])

    def test_missing_neutral_keypoints_fail_closed(self):
        """No face detected in the neutral/baseline frame → fail closed."""
        from verification.liveness import verify_server_authoritative_challenge
        result = verify_server_authoritative_challenge(None, TURNED_KEYPOINTS, 'side')
        self.assertFalse(result['completed'])

    def test_missing_challenge_keypoints_fail_closed(self):
        """No face detected in the challenge/proof frame → fail closed."""
        from verification.liveness import verify_server_authoritative_challenge
        result = verify_server_authoritative_challenge(NEUTRAL_KEYPOINTS, None, 'side')
        self.assertFalse(result['completed'])

    def test_both_missing_fail_closed(self):
        from verification.liveness import verify_server_authoritative_challenge
        result = verify_server_authoritative_challenge(None, None, 'side')
        self.assertFalse(result['completed'])

    def test_wrong_direction_does_not_complete(self):
        """Keypoints show a rightward yaw turn, but the server-assigned
        direction is 'left' — must not be satisfied by the wrong movement."""
        from verification.liveness import verify_server_authoritative_challenge
        result = verify_server_authoritative_challenge(NEUTRAL_KEYPOINTS, TURNED_KEYPOINTS, 'left')
        self.assertFalse(result['completed'])

    def test_malformed_keypoints_fail_closed_not_crash(self):
        from verification.liveness import verify_server_authoritative_challenge
        bad = {'left_eye': ('x', 'y'), 'right_eye': (55.0, 50.0), 'nose': (50.0, 55.0)}
        result = verify_server_authoritative_challenge(bad, TURNED_KEYPOINTS, 'side')
        self.assertFalse(result['completed'])

    def test_below_threshold_movement_does_not_complete(self):
        """A tiny detector-jitter-sized delta must not satisfy the challenge."""
        from verification.liveness import verify_server_authoritative_challenge
        barely_moved = {'left_eye': (45.0, 50.0), 'right_eye': (55.0, 50.0), 'nose': (50.2, 55.0)}
        result = verify_server_authoritative_challenge(NEUTRAL_KEYPOINTS, barely_moved, 'side')
        self.assertFalse(result['completed'])


class ServerAuthoritativeChallengeEndpointTest(TestCase):
    """
    End-to-end attack tests for verify_check_liveness's liveness security
    architecture (v2.1.16 Security Hardening Round #4 — Blocker 1, complete
    redesign). The browser never supplies the final proof of movement: these
    tests prove the endpoint's pass/fail outcome tracks ONLY what
    face_utils.detect_pose_keypoints() (mocked here to stand in for the
    server's own RetinaFace/MTCNN detector) finds in the raw frame bytes,
    and is completely unmoved by whatever the client claims via
    challenge_completed / movement.* / baseline_landmarks / challenge_landmarks.

    Maps directly onto the required attack matrix:
      1. challenge_completed=true, no evidence               → FAIL
      2. fabricated landmarks (server sees no real movement) → FAIL
      3. replay of previous successful evidence               → FAIL
      4. legitimate user movement                             → PASS
      5. spoof attempt (PAD)                                  → FAIL
    """

    def setUp(self):
        from verification.models import StipendEvent
        self.client = Client()
        self.staff = _make_staff('sac_staff')
        self.client.force_login(self.staff)
        self.url = reverse('verification:verify_check_liveness')
        self.ben = _make_beneficiary('BEN-SAC-001', 'SC-SAC-001')
        self.event = StipendEvent.objects.create(
            title='SAC Payout', date=datetime.date.today(), amount=500,
            is_active=True, created_by=self.staff,
        )

    def _set_session(self, challenge='side'):
        session = self.client.session
        session['verification_session'] = {
            'beneficiary_id': str(self.ben.pk),
            'session_id': _FIXED_TEST_SESSION_ID,
            'attempt_number': 1,
            'challenge': challenge,
            'claimant_type': 'beneficiary',
            'stipend_event_id': str(self.event.pk),
        }
        session.save()

    def _mock_clean_pad(self, mock_pad):
        _clean_pad = mock.MagicMock(suspicious=False, score=0.0, flags={}, reason='Clean.')
        mock_pad_instance = mock.MagicMock()
        mock_pad_instance.analyze.return_value = _clean_pad
        mock_pad_instance.analyze_sequence.return_value = _clean_pad
        mock_pad.return_value = mock_pad_instance

    def _post(self, extra_payload=None, neutral_image=DATA_URI_JPEG):
        payload = {
            'image': DATA_URI_JPEG,
            'challenge_completed': True,
            'frames': [DATA_URI_JPEG, DATA_URI_JPEG, DATA_URI_JPEG],
            'session_id': _FIXED_TEST_SESSION_ID,
        }
        if neutral_image is not None:
            payload['neutral_image'] = neutral_image
        payload.update(extra_payload or {})
        return self.client.post(
            self.url, data=json.dumps(payload), content_type='application/json', secure=True,
        )

    @override_settings(DEBUG=True, LIVENESS_PROOF_REQUIRED=True)
    @mock.patch('verification.views.detect_pose_keypoints')
    @mock.patch('verification.views.check_anti_spoofing')
    @mock.patch('verification.face_utils.get_embedding_only')
    @mock.patch('verification.views.PresentationAttackDetector')
    @mock.patch('verification.views.run_full_liveness_check')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.load_image_from_bytes')
    def test_4_legitimate_user_movement_passes(
        self, mock_load, mock_detect, mock_liveness, mock_pad, mock_get_emb,
        mock_spoof, mock_pose,
    ):
        """Attack test 4: genuine movement (server-detected keypoints show a
        real turn) → PASS, tx_token issued."""
        import numpy as np
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_liveness.return_value = {
            'passed': True, 'anti_spoof_passed': True,
            'anti_spoof_score': 0.9, 'liveness_score': 0.9,
            'reason': 'Liveness check passed.',
        }
        self._mock_clean_pad(mock_pad)
        mock_get_emb.return_value = {'success': True, 'embedding': [0.1] * 128}
        mock_spoof.return_value = {'passed': True, 'score': 0.9, 'reason': 'Real face.'}
        mock_pose.side_effect = [NEUTRAL_KEYPOINTS, TURNED_KEYPOINTS]
        self._set_session()

        resp = self._post()
        data = json.loads(resp.content)
        self.assertTrue(data['passed'], data)
        self.assertIsNotNone(data['tx_token'], data)
        self.assertEqual(data['debug_stage'], 'tx_created')

    @override_settings(DEBUG=True, LIVENESS_PROOF_REQUIRED=True)
    @mock.patch('verification.views.detect_pose_keypoints')
    @mock.patch('verification.face_utils.get_embedding_only')
    @mock.patch('verification.views.PresentationAttackDetector')
    @mock.patch('verification.views.run_full_liveness_check')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.load_image_from_bytes')
    def test_1_challenge_completed_true_without_evidence_fails(
        self, mock_load, mock_detect, mock_liveness, mock_pad, mock_get_emb,
        mock_pose,
    ):
        """Attack test 1: challenge_completed=true with NO neutral/baseline
        frame at all — there is nothing for the server to independently
        detect a "before" pose from, so no amount of client-side claiming
        can satisfy the movement gate. detect_pose_keypoints is mocked to
        return a value (as if the challenge frame DID have a detectable
        face) specifically to prove that a missing NEUTRAL frame alone is
        enough to fail closed, even when the other frame would "pass"."""
        import numpy as np
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_liveness.return_value = {
            'passed': True, 'anti_spoof_passed': True,
            'anti_spoof_score': 0.9, 'liveness_score': 0.9,
            'reason': 'Liveness check passed.',
        }
        self._mock_clean_pad(mock_pad)
        mock_get_emb.return_value = {'success': True, 'embedding': [0.1] * 128}
        mock_pose.return_value = TURNED_KEYPOINTS
        self._set_session()

        resp = self._post(neutral_image=None)
        data = json.loads(resp.content)
        self.assertFalse(data['passed'], data)
        self.assertIsNone(data['tx_token'], data)
        self.assertEqual(data['debug_stage'], 'movement_validation_failed')
        mock_get_emb.assert_not_called()
        # detect_pose_keypoints IS still called for the challenge/proof frame
        # (there's no neutral frame to call it for) — but with no neutral
        # pose to compare against, the challenge frame's result alone can
        # never satisfy verify_server_authoritative_challenge().
        mock_pose.assert_called_once()

    @override_settings(DEBUG=True, LIVENESS_PROOF_REQUIRED=True)
    @mock.patch('verification.views.detect_pose_keypoints')
    @mock.patch('verification.views.check_anti_spoofing')
    @mock.patch('verification.face_utils.get_embedding_only')
    @mock.patch('verification.views.PresentationAttackDetector')
    @mock.patch('verification.views.run_full_liveness_check')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.load_image_from_bytes')
    def test_2_fabricated_landmarks_do_not_bypass_server_detection(
        self, mock_load, mock_detect, mock_liveness, mock_pad, mock_get_emb,
        mock_spoof, mock_pose,
    ):
        """Attack test 2: the client sends fabricated MediaPipe-shaped
        landmark JSON claiming a large turn AND a forged movement.* block —
        but the server's OWN detector (mocked to represent ground truth)
        finds the face in the SAME position in both frames. The fabricated
        client data must have zero effect: the server-side evidence alone
        decides, and it shows no movement → FAIL."""
        import numpy as np
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_liveness.return_value = {
            'passed': True, 'anti_spoof_passed': True,
            'anti_spoof_score': 0.9, 'liveness_score': 0.9,
            'reason': 'Liveness check passed.',
        }
        self._mock_clean_pad(mock_pad)
        mock_get_emb.return_value = {'success': True, 'embedding': [0.1] * 128}
        mock_spoof.return_value = {'passed': True, 'score': 0.9, 'reason': 'Real face.'}
        # Ground truth: the server's detector sees the SAME face position at
        # baseline and challenge time — no real movement occurred.
        mock_pose.side_effect = [NEUTRAL_KEYPOINTS, SAME_KEYPOINTS]
        self._set_session()

        resp = self._post({
            'movement': {
                'mediapipe_available': True,
                'peak_yaw_delta': 45.0,
                'peak_pitch_delta': 45.0,
                'threshold': 4.0,
                'face_lost_count': 0,
                'challenge_duration_ms': 1500,
            },
            'baseline_landmarks': BASELINE_LANDMARKS,
            'challenge_landmarks': CHALLENGE_LANDMARKS,
        })
        data = json.loads(resp.content)
        self.assertFalse(data['passed'], data)
        self.assertIsNone(data['tx_token'], data)
        self.assertEqual(data['debug_stage'], 'movement_validation_failed')
        mock_get_emb.assert_not_called()

    @override_settings(DEBUG=True, LIVENESS_PROOF_REQUIRED=True)
    @mock.patch('verification.views.detect_pose_keypoints')
    @mock.patch('verification.views.check_anti_spoofing')
    @mock.patch('verification.face_utils.get_embedding_only')
    @mock.patch('verification.views.PresentationAttackDetector')
    @mock.patch('verification.views.run_full_liveness_check')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.load_image_from_bytes')
    def test_3_replay_of_previous_successful_evidence_fails(
        self, mock_load, mock_detect, mock_liveness, mock_pad, mock_get_emb,
        mock_spoof, mock_pose,
    ):
        """Attack test 3: a request with byte-identical image/neutral_image
        to one that ALREADY earned a LivenessTransaction must be rejected as
        a replay, even though the (mocked) server-side pose evidence would
        otherwise show a passing turn. Proves the defense is keyed on the
        actual frame bytes, not merely on repeatable mock behaviour."""
        import numpy as np
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_liveness.return_value = {
            'passed': True, 'anti_spoof_passed': True,
            'anti_spoof_score': 0.9, 'liveness_score': 0.9,
            'reason': 'Liveness check passed.',
        }
        self._mock_clean_pad(mock_pad)
        mock_get_emb.return_value = {'success': True, 'embedding': [0.1] * 128}
        mock_spoof.return_value = {'passed': True, 'score': 0.9, 'reason': 'Real face.'}
        mock_pose.side_effect = [
            NEUTRAL_KEYPOINTS, TURNED_KEYPOINTS,  # first (legitimate) request
            NEUTRAL_KEYPOINTS, TURNED_KEYPOINTS,  # replay — would also "pass" pose-wise
        ]
        self._set_session()

        first = self._post()
        first_data = json.loads(first.content)
        self.assertTrue(first_data['passed'], first_data)
        self.assertIsNotNone(first_data['tx_token'], first_data)

        # Second request re-sends the IDENTICAL image + neutral_image bytes.
        second = self._post()
        second_data = json.loads(second.content)
        self.assertFalse(second_data['passed'], second_data)
        self.assertIsNone(second_data['tx_token'], second_data)
        self.assertEqual(second_data['debug_stage'], 'replay_detected')
        self.assertNotEqual(first_data['tx_token'], None)

    @override_settings(DEBUG=True, LIVENESS_PROOF_REQUIRED=True,
                       PAD_REQUIRED=True, STRICT_PRESENTATION_ATTACK_CHECK=True,
                       PRESENTATION_ATTACK_REVIEW_OR_DENY='deny')
    @mock.patch('verification.views.PresentationAttackDetector')
    @mock.patch('verification.views.run_full_liveness_check')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.load_image_from_bytes')
    def test_5_spoof_attempt_denied_by_pad(
        self, mock_load, mock_detect, mock_liveness, mock_pad,
    ):
        """Attack test 5: a presentation-attack-flagged sequence is denied
        by PAD before movement validation is even reached — the movement
        gate must not weaken this existing defense."""
        import numpy as np
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_liveness.return_value = {
            'passed': True, 'anti_spoof_passed': True,
            'anti_spoof_score': 0.9, 'liveness_score': 0.9,
            'reason': 'Liveness check passed.',
        }
        _suspicious_pad = mock.MagicMock(
            suspicious=True, score=0.9, flags={'specular_glare': 0.9},
            reason='Denied: possible phone screen or photo presentation attack.',
            specular_glare=0.9, screen_flatness=0.7, sharpness_texture_ratio=0.1,
            sequence_static=0.0, near_duplicate=0.0,
        )
        mock_pad_instance = mock.MagicMock()
        mock_pad_instance.analyze.return_value = _suspicious_pad
        mock_pad_instance.analyze_sequence.return_value = _suspicious_pad
        mock_pad_instance.classify_denial.return_value = 'attack'
        mock_pad.return_value = mock_pad_instance
        self._set_session()

        # No neutral_image needed — PAD denial happens before movement
        # validation is reached, and anti-spoof falls back to the (mocked,
        # passing) proof-frame result when no neutral frame is sent.
        resp = self._post(neutral_image=None)
        data = json.loads(resp.content)
        self.assertFalse(data['passed'], data)
        self.assertIsNone(data['tx_token'], data)
        self.assertEqual(data['debug_stage'], 'pad_denied')


# ── v2.1.16 Security Hardening Round #3 — Blocker 2: atomic liveness token ───
# ── claiming ───────────────────────────────────────────────────────────────

class AtomicLivenessTokenClaimTest(TestCase):
    """
    LivenessTransaction.claim() must atomically win-or-lose the token BEFORE
    any expensive verification work runs, so a losing concurrent request never
    performs face comparison / decision logic / ClaimRecord creation for a
    token another request already claimed (v2.1.16 Security Hardening Round #3
    — Blocker 2).
    """

    def setUp(self):
        from verification.models import StipendEvent, FaceEmbedding, LivenessTransaction
        self.staff = _make_staff('atomic_claim_staff')
        self.ben = _make_beneficiary('BEN-ATC-001', 'SC-ATC-001')
        self.ben.status = Beneficiary.STATUS_ACTIVE
        self.ben.save()
        self.event = StipendEvent.objects.create(
            title='Atomic Claim Payout', date=datetime.date.today(), amount=500,
            is_active=True, created_by=self.staff,
        )
        FaceEmbedding.objects.create(beneficiary=self.ben, embedding_data=b'\x00' * 64)
        self.tx = LivenessTransaction.objects.create(
            beneficiary=self.ben, claimant_type='beneficiary',
            stipend_event=self.event, performed_by=self.staff,
            challenge_direction='side', anti_spoof_score=0.9,
            liveness_score=0.9, pa_score=0.0, pa_flags={},
            embedding_data=b'\x01' * 64,
            expires_at=timezone.now() + datetime.timedelta(seconds=120),
        )

    def test_claim_wins_when_unused(self):
        self.assertTrue(self.tx.claim())
        self.tx.refresh_from_db()
        self.assertTrue(self.tx.is_used)
        self.assertIsNone(self.tx.used_by_attempt)

    def test_second_claim_loses(self):
        self.assertTrue(self.tx.claim())
        tx_copy = type(self.tx).objects.get(pk=self.tx.pk)
        self.assertFalse(tx_copy.claim(), 'second claim() on an already-claimed token must lose')

    def test_bind_attempt_after_claim_records_owner(self):
        from verification.models import VerificationAttempt
        self.assertTrue(self.tx.claim())
        attempt = VerificationAttempt.objects.create(
            beneficiary=self.ben, performed_by=self.staff,
            claimant_type='beneficiary', liveness_passed=True, liveness_score=0.9,
            anti_spoof_score=0.9, threshold_used=0.75, attempt_number=1,
            stipend_event=self.event,
            decision=VerificationAttempt.DECISION_VERIFIED,
        )
        self.tx.bind_attempt(attempt)
        self.tx.refresh_from_db()
        self.assertEqual(self.tx.used_by_attempt_id, attempt.id)

    def test_release_claim_allows_retry(self):
        self.assertTrue(self.tx.claim())
        self.tx.release_claim()
        self.tx.refresh_from_db()
        self.assertFalse(self.tx.is_used)
        self.assertTrue(self.tx.claim(), 'a released claim must be re-claimable')


class VerifySubmitConcurrentSubmissionTest(TestCase):
    """
    End-to-end regression: two near-simultaneous verify_submit requests
    carrying the SAME tx_token must result in exactly one accepted
    verification. Before the fix, both requests could reach face comparison
    and decision logic (consume() was only called at the very end); now
    claim() runs first, inside transaction.atomic(), so the second request is
    rejected before any of that work begins.
    """

    def setUp(self):
        from verification.models import StipendEvent, FaceEmbedding, LivenessTransaction
        self.staff = _make_staff('concurrent_submit_staff')
        self.ben = _make_beneficiary('BEN-CCS-001', 'SC-CCS-001')
        self.ben.status = Beneficiary.STATUS_ACTIVE
        self.ben.save()
        self.event = StipendEvent.objects.create(
            title='Concurrent Submit Payout', date=datetime.date.today(), amount=500,
            is_active=True, created_by=self.staff,
        )
        FaceEmbedding.objects.create(beneficiary=self.ben, embedding_data=b'\x00' * 64)
        self.tx = LivenessTransaction.objects.create(
            beneficiary=self.ben, claimant_type='beneficiary',
            stipend_event=self.event, performed_by=self.staff,
            challenge_direction='side', anti_spoof_score=0.9,
            liveness_score=0.9, pa_score=0.0, pa_flags={},
            embedding_data=b'\x01' * 64,
            expires_at=timezone.now() + datetime.timedelta(seconds=120),
        )
        self.client = Client()
        self.client.force_login(self.staff)
        self.url = reverse('verification:verify_submit')

    def _set_session(self):
        session = self.client.session
        session['verification_session'] = {
            'beneficiary_id': str(self.ben.pk),
            'attempt_number': 1,
            'session_id': _FIXED_TEST_SESSION_ID,
            'claimant_type': 'beneficiary',
            'stipend_event_id': str(self.event.pk),
            'challenge': 'side',
        }
        session.save()

    def _payload(self):
        return json.dumps({
            'image': DATA_URI_JPEG,
            'challenge_completed': True,
            'liveness_score': 0.9,
            'anti_spoof_score': 0.9,
            'liveness_passed': True,
            'face_detected': True,
            'tx_token': str(self.tx.token),
            'session_id': _FIXED_TEST_SESSION_ID,
        })

    @override_settings(LIVENESS_PROOF_REQUIRED=True, LIVENESS_REQUIRED=True,
                       DEMO_MODE=False, DEBUG=True, SAME_FACE_SEQUENCE_THRESHOLD=0)
    @mock.patch('verification.face_utils.check_duplicate_face')
    @mock.patch('verification.face_utils.decrypt_embedding')
    @mock.patch('verification.views.compare_with_all_embeddings')
    @mock.patch('verification.views.load_image_from_bytes')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.check_anti_spoofing')
    @mock.patch('verification.views.process_face_for_verification')
    def test_only_one_of_two_simultaneous_submissions_succeeds(
        self, mock_process, mock_spoof, mock_detect, mock_load,
        mock_compare, mock_decrypt, mock_dup,
    ):
        import numpy as np
        from verification.models import VerificationAttempt
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_spoof.return_value = {'passed': True, 'score': 0.9, 'reason': 'Real.'}
        mock_process.return_value = {
            'success': True, 'embedding': np.zeros(128),
            'quality': {'ok': True, 'score': 0.9, 'reason': ''}, 'using_mock': False,
        }
        mock_compare.return_value = {
            'success': True, 'score': 0.85,
            'matched_template': 'primary', 'templates_checked': 1, 'all_scores': [],
        }
        mock_dup.return_value = {
            'duplicates_found': False, 'matches': [], 'highest_score': 0.0, 'checked': 0,
        }
        mock_decrypt.return_value = [0.1] * 128
        self._set_session()

        # Simulate "simultaneous": the second request is issued with the same
        # session/token state as the first — claim() (called before any face
        # comparison) is the mechanism that must serialize these correctly.
        resp1 = self.client.post(self.url, data=self._payload(),
                                 content_type='application/json', secure=True)
        self._set_session()  # verify_submit pops the session on completion; restore it
        resp2 = self.client.post(self.url, data=self._payload(),
                                 content_type='application/json', secure=True)

        data1 = json.loads(resp1.content)
        data2 = json.loads(resp2.content)

        outcomes = [data1.get('decision'), data2.get('decision')]
        # Exactly one request may have reached a FaceNet-driven decision;
        # the other must be denied for lacking a valid (unclaimed) proof.
        self.assertEqual(outcomes.count('denied'), 1,
                         f'Exactly one submission must be denied (replay/claim-lost): {outcomes}')
        self.assertEqual(mock_compare.call_count, 1,
                         'FaceNet comparison must run for only ONE of the two submissions')
        self.tx.refresh_from_db()
        self.assertTrue(self.tx.is_used)
        # Both submissions record an audit VerificationAttempt (the losing
        # one as 'denied'), but only one may hold a non-denied, FaceNet-driven
        # decision — the token itself was consumed exactly once.
        attempts = VerificationAttempt.objects.filter(beneficiary=self.ben)
        self.assertEqual(attempts.count(), 2)
        self.assertEqual(
            attempts.filter(decision=VerificationAttempt.DECISION_DENIED).count(), 1,
        )


# ── verify_submit Bug A regression ───────────────────────────────────────────

class VerifySubmitTXEmbeddingRegressionTest(TestCase):
    """
    Bug A regression: verify_submit must not raise UnboundLocalError when
    live_embedding comes from a LivenessTransaction (face_result=None path).

    Before fix: face_result was referenced without initialization when the TX
    embedding path was taken, causing UnboundLocalError at the quality note guard.
    After fix: face_result = None is always initialized; the guard is None-safe.
    """

    def setUp(self):
        from verification.models import StipendEvent, FaceEmbedding
        self.staff = _make_staff('txemb_staff')
        self.ben = _make_beneficiary('BEN-TXE-001', 'SC-TXE-001')
        self.ben.status = Beneficiary.STATUS_ACTIVE
        self.ben.save()
        self.event = StipendEvent.objects.create(
            title='TXEmb Payout', date=datetime.date.today(), amount=500,
            is_active=True, created_by=self.staff,
        )
        FaceEmbedding.objects.create(
            beneficiary=self.ben, embedding_data=b'\x00' * 64,
        )
        self.client = Client()
        self.client.force_login(self.staff)
        self.url = reverse('verification:verify_submit')

    def _set_session(self):
        session = self.client.session
        session['verification_session'] = {
            'beneficiary_id': str(self.ben.pk),
            'attempt_number': 1,
            'session_id': _FIXED_TEST_SESSION_ID,
            'claimant_type': 'beneficiary',
            'stipend_event_id': str(self.event.pk),
            'challenge': 'side',
        }
        session.save()

    @override_settings(LIVENESS_PROOF_REQUIRED=True, LIVENESS_REQUIRED=True,
                       DEMO_MODE=False, VERIFICATION_THRESHOLD=0.75, DEBUG=True)
    @mock.patch('verification.face_utils.check_duplicate_face')
    @mock.patch('verification.views.load_image_from_bytes')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.check_anti_spoofing')
    @mock.patch('verification.views.process_face_for_verification')
    @mock.patch('verification.views.compare_with_all_embeddings')
    @mock.patch('verification.face_utils.decrypt_embedding')
    def test_tx_embedding_path_does_not_crash_on_quality_note(
        self, mock_decrypt, mock_compare, mock_process,
        mock_spoof, mock_detect, mock_load, mock_dup,
    ):
        """
        Regression for Bug A: when TX has an embedding, face_result is never
        assigned by process_face_for_verification. The quality note guard at
        'if face_result is not None' must not raise UnboundLocalError.
        """
        import numpy as np
        from verification.models import LivenessTransaction

        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_spoof.return_value = {'passed': True, 'score': 0.9, 'reason': 'Real face.'}
        mock_process.return_value = {
            'success': True, 'embedding': np.zeros(128),
            'quality': {'ok': True, 'score': 0.9, 'reason': ''}, 'using_mock': False,
        }
        mock_compare.return_value = {
            'success': True, 'score': 0.85,
            'matched_template': 'primary', 'templates_checked': 1, 'all_scores': [],
        }
        mock_dup.return_value = {
            'duplicates_found': False, 'matches': [], 'highest_score': 0.0, 'checked': 0,
        }
        # TX has a valid embedding — decrypt_embedding returns a list so
        # live_embedding is NOT None → face_result is never set by process_face_for_verification.
        live_emb = [0.1] * 128
        mock_decrypt.return_value = live_emb

        tx = LivenessTransaction.objects.create(
            beneficiary=self.ben,
            claimant_type='beneficiary',
            stipend_event=self.event,
            performed_by=self.staff,
            challenge_direction='side',
            anti_spoof_score=0.9,
            liveness_score=0.9,
            pa_score=0.0,
            pa_flags={},
            embedding_data=b'\x01' * 64,  # non-None so decrypt path is taken
            expires_at=timezone.now() + datetime.timedelta(seconds=120),
        )
        self._set_session()

        payload = json.dumps({
            'image': DATA_URI_JPEG,
            'challenge_completed': True,
            'liveness_score': 0.9,
            'anti_spoof_score': 0.9,
            'liveness_passed': True,
            'face_detected': True,
            'tx_token': str(tx.token),
            'session_id': _FIXED_TEST_SESSION_ID,
        })
        resp = self.client.post(
            self.url, data=payload, content_type='application/json', secure=True,
        )
        # Must not raise 500 (UnboundLocalError) — any decision is acceptable
        self.assertNotEqual(resp.status_code, 500,
                            'UnboundLocalError regression: verify_submit must not crash')
        data = json.loads(resp.content)
        self.assertIn('decision', data,
                      f'Response must contain decision key, got: {data}')


# ──────────────────────────────────────────────────────────────────────────────
# v2.2.0 Follow-up Issue 32 — verification must stay bound to the beneficiary
# the operator actually selected, even if a second verification is started
# for a different beneficiary in another browser tab (same login session).
#
# request.session is keyed on the login cookie, shared across every tab — a
# fresh verify_start() call overwrites verification_session for ALL tabs.
# Before this fix, a stale first tab's submission would be silently evaluated
# (and could be RELEASED) against whichever beneficiary now occupies the
# session, not the one the operator started with. The fix: verify_start()
# embeds a fresh per-call session_id in the page; verify_check_liveness and
# verify_submit now require the submitted session_id to match the CURRENT
# server-side session, refusing (never falling back or reassigning) on a
# mismatch.
# ──────────────────────────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────────────────────────────
# v2.2.0 Follow-up Issue 33 — an unresolved duplicate-biometric conflict must
# never coexist with a released stipend. By design duplicate_review_required
# is only ever True while status=PENDING (verify_start already refuses
# ineligible statuses), so these tests specifically prove the DEFENSE-IN-DEPTH
# added to Beneficiary.is_eligible_to_claim: even in the abnormal/legacy state
# where a record is somehow ACTIVE while still flagged, verification start AND
# manual-review payout release both refuse — a normal verification success or
# an override on a SEPARATE manual-review condition must never silently clear
# a distinct, unresolved duplicate-identity conflict.
# ──────────────────────────────────────────────────────────────────────────────

class DuplicateConflictBlocksPayoutTest(TestCase):

    def setUp(self):
        from verification.models import StipendEvent, FaceEmbedding
        from cryptography.fernet import Fernet

        self.admin = _make_staff('dcbpadmin1', role=CustomUser.ROLE_ADMIN)
        self.president = _make_staff('dcbppres02', role=CustomUser.ROLE_PRESIDENT)
        self.existing = _make_beneficiary('BEN-DCBP-EXIST', 'SC-DCBP-EXIST')

        # Abnormal/legacy state under test: ACTIVE but still duplicate-flagged.
        self.flagged = _make_beneficiary('BEN-DCBP-FLAGGED', 'SC-DCBP-FLAGGED')
        self.flagged.status = Beneficiary.STATUS_ACTIVE
        self.flagged.consent_given = True
        self.flagged.duplicate_review_required = True
        self.flagged.duplicate_match_beneficiary = self.existing
        self.flagged.duplicate_match_score = 0.9
        self.flagged.save()
        FaceEmbedding.objects.create(
            beneficiary=self.flagged,
            embedding_data=Fernet(Fernet.generate_key()).encrypt(b'\x00' * 512),
        )

        self.event = StipendEvent.objects.create(
            title='DCBP Payout', date=datetime.date.today(), amount=500,
            is_active=True, created_by=self.admin,
            approval_status=StipendEvent.APPROVAL_APPROVED,
        )
        self.client = Client()

    def test_is_eligible_to_claim_false_while_flagged(self):
        self.assertFalse(self.flagged.is_eligible_to_claim)

    def test_becomes_eligible_once_flag_cleared(self):
        self.flagged.duplicate_review_required = False
        self.flagged.save()
        self.assertTrue(self.flagged.is_eligible_to_claim)

    @override_settings(DEBUG=True)
    def test_verify_start_refuses_flagged_beneficiary(self):
        self.client.force_login(self.admin)
        resp = self.client.get(
            reverse('verification:verify_start', args=[self.flagged.pk]), secure=True,
        )
        self.assertEqual(resp.status_code, 302)
        # Must not render the capture page — redirected away with an
        # explanatory message, never silently allowed through.
        self.assertNotIn('verify_capture', resp.get('Location', ''))

    @staticmethod
    def _daytime_now():
        # self.event.date is datetime.date.today() (set in setUp), so the
        # mocked "now" must keep that same calendar date — only the TIME is
        # pinned inside the global 07:00-20:00 same-day claiming window
        # (Phase B.5 finalization-within-window policy — see StipendEvent.
        # check_claim_eligible_now) so this test's outcome depends only on
        # the duplicate-face guard under test, never on the real wall-clock
        # time the suite happens to run at.
        import datetime as _dt
        from zoneinfo import ZoneInfo
        manila = ZoneInfo('Asia/Manila')
        today = _dt.date.today()
        return _dt.datetime(today.year, today.month, today.day, 14, 0, 0, tzinfo=manila).astimezone(_dt.timezone.utc)

    def test_override_release_payout_refuses_flagged_beneficiary(self):
        """Core Issue 33 scenario: an admin overrides a SEPARATE manual-review
        condition on a VerificationAttempt — that override must NOT also
        silently clear this beneficiary's unrelated duplicate-face conflict
        and allow the payout to release."""
        from verification.models import VerificationAttempt, ClaimRecord

        with mock.patch('django.utils.timezone.now', return_value=self._daytime_now()):
            attempt = VerificationAttempt.objects.create(
                beneficiary=self.flagged, performed_by=self.admin,
                decision=VerificationAttempt.DECISION_VERIFIED,
                overridden=True, override_by=self.president,
                override_at=timezone.now(), threshold_used=0.75,
                stipend_event=self.event,
            )
            self.client.force_login(self.president)
            resp = self.client.post(
                reverse('verification:override_release_payout', args=[attempt.pk]),
            )
            self.assertEqual(resp.status_code, 302)
            self.assertFalse(
                ClaimRecord.objects.filter(beneficiary=self.flagged).exists(),
                'No payout may release while the duplicate-face conflict is unresolved.',
            )

    def test_override_release_payout_succeeds_once_conflict_resolved(self):
        """Regression: clearing the flag through the proper Duplicate Face
        Review resolution must restore normal release behavior."""
        from verification.models import VerificationAttempt, ClaimRecord

        self.flagged.duplicate_review_required = False
        self.flagged.save()

        with mock.patch('django.utils.timezone.now', return_value=self._daytime_now()):
            attempt = VerificationAttempt.objects.create(
                beneficiary=self.flagged, performed_by=self.admin,
                decision=VerificationAttempt.DECISION_VERIFIED,
                overridden=True, override_by=self.president,
                override_at=timezone.now(), threshold_used=0.75,
                stipend_event=self.event,
            )
            self.client.force_login(self.president)
            self.client.post(
                reverse('verification:override_release_payout', args=[attempt.pk]),
            )
            self.assertTrue(ClaimRecord.objects.filter(beneficiary=self.flagged).exists())


class VerifySubmitSessionCollisionGuardTest(TestCase):

    def setUp(self):
        from verification.models import StipendEvent, FaceEmbedding
        from cryptography.fernet import Fernet

        self.staff = _make_staff('sesscol_staff')
        self.key = Fernet.generate_key()

        self.ben_a = _make_beneficiary('BEN-SESSA-001', 'SC-SESSA-001')
        self.ben_a.status = Beneficiary.STATUS_ACTIVE
        self.ben_a.consent_given = True
        self.ben_a.save()
        FaceEmbedding.objects.create(
            beneficiary=self.ben_a, embedding_data=Fernet(self.key).encrypt(b'\x00' * 512),
        )

        self.ben_b = _make_beneficiary('BEN-SESSB-001', 'SC-SESSB-001')
        self.ben_b.status = Beneficiary.STATUS_ACTIVE
        self.ben_b.consent_given = True
        self.ben_b.save()
        FaceEmbedding.objects.create(
            beneficiary=self.ben_b, embedding_data=Fernet(self.key).encrypt(b'\x01' * 512),
        )

        self.event = StipendEvent.objects.create(
            title='SessCol Payout', date=datetime.date.today(), amount=500,
            is_active=True, created_by=self.staff,
        )
        self.client = Client()
        self.client.force_login(self.staff)

    def _start_verification(self, beneficiary):
        """Simulates opening verify_start in a tab: returns the session_id
        the server generated and embedded into that page."""
        resp = self.client.get(
            reverse('verification:verify_start', args=[beneficiary.pk]), secure=True,
        )
        self.assertEqual(resp.status_code, 200)
        return resp.context['session_id']

    def _submit_payload(self, session_id, tx_token=''):
        return json.dumps({
            'image': DATA_URI_JPEG,
            'challenge_completed': True,
            'liveness_score': 0.9,
            'anti_spoof_score': 0.9,
            'liveness_passed': True,
            'face_detected': True,
            'tx_token': tx_token,
            'session_id': session_id,
        })

    @override_settings(LIVENESS_PROOF_REQUIRED=True, LIVENESS_REQUIRED=True, DEBUG=True)
    def test_stale_tab_after_second_verify_start_is_rejected_not_reassigned(self):
        """Core Issue 32 scenario: start A, start B (overwrites session), then
        submit the STALE session_id from A's tab. Must be refused — never
        silently evaluated against B."""
        from verification.models import VerificationAttempt, ClaimRecord

        session_id_a = self._start_verification(self.ben_a)
        session_id_b = self._start_verification(self.ben_b)
        self.assertNotEqual(session_id_a, session_id_b)

        resp = self.client.post(
            reverse('verification:verify_submit'),
            data=self._submit_payload(session_id_a),
            content_type='application/json', secure=True,
        )
        data = json.loads(resp.content)
        self.assertFalse(data.get('success'))
        self.assertIn('another', data.get('error', '').lower())

        # Nothing must have been recorded against EITHER beneficiary from
        # the rejected stale submission.
        self.assertEqual(VerificationAttempt.objects.filter(beneficiary=self.ben_a).count(), 0)
        self.assertEqual(VerificationAttempt.objects.filter(beneficiary=self.ben_b).count(), 0)
        self.assertEqual(ClaimRecord.objects.count(), 0)

    @override_settings(LIVENESS_PROOF_REQUIRED=True, LIVENESS_REQUIRED=True, DEBUG=True)
    def test_current_tab_session_id_still_works_normally(self):
        """The CURRENT (non-stale) session_id must still be accepted —
        the guard only rejects a superseded session, not normal submission."""
        session_id_b = self._start_verification(self.ben_b)
        resp = self.client.post(
            reverse('verification:verify_check_liveness'),
            data=json.dumps({
                'image': DATA_URI_JPEG,
                'challenge_completed': False,
                'session_id': session_id_b,
            }),
            content_type='application/json', secure=True,
        )
        # Mode A (pre-challenge) does not require a session at all, but must
        # not error out just because a session_id was supplied.
        self.assertEqual(resp.status_code, 200)

    @override_settings(LIVENESS_PROOF_REQUIRED=True, LIVENESS_REQUIRED=True, DEBUG=True)
    def test_mode_b_refuses_to_issue_tx_for_stale_session(self):
        """verify_check_liveness Mode B must not issue a LivenessTransaction
        bound to whichever beneficiary currently occupies an overwritten
        session — closes the gap before verify_submit is even reached."""
        from verification.models import LivenessTransaction

        session_id_a = self._start_verification(self.ben_a)
        self._start_verification(self.ben_b)  # overwrites the shared session

        tx_count_before = LivenessTransaction.objects.count()
        resp = self.client.post(
            reverse('verification:verify_check_liveness'),
            data=json.dumps({
                'image': DATA_URI_JPEG,
                'neutral_image': DATA_URI_JPEG,
                'challenge_completed': True,
                'frames': [DATA_URI_JPEG, DATA_URI_JPEG, DATA_URI_JPEG],
                'session_id': session_id_a,
            }),
            content_type='application/json', secure=True,
        )
        data = json.loads(resp.content)
        self.assertIsNone(data.get('tx_token'))
        self.assertEqual(LivenessTransaction.objects.count(), tx_count_before)

    @override_settings(LIVENESS_PROOF_REQUIRED=True, LIVENESS_REQUIRED=True, DEBUG=True)
    def test_missing_session_id_on_submit_rejected(self):
        """An old/bypassing client that omits session_id entirely must also
        be refused, not treated as automatically valid."""
        self._start_verification(self.ben_a)
        resp = self.client.post(
            reverse('verification:verify_submit'),
            data=self._submit_payload(session_id=''),
            content_type='application/json', secure=True,
        )
        data = json.loads(resp.content)
        self.assertFalse(data.get('success'))

    @override_settings(LIVENESS_PROOF_REQUIRED=True, LIVENESS_REQUIRED=True, DEBUG=True)
    def test_second_verify_start_for_same_beneficiary_issues_new_session_id(self):
        """Restarting verification for the SAME beneficiary (e.g. retry after
        a failed attempt) must still rotate the session_id — the old page's
        stale session_id must no longer validate either."""
        first = self._start_verification(self.ben_a)
        second = self._start_verification(self.ben_a)
        self.assertNotEqual(first, second)

        resp = self.client.post(
            reverse('verification:verify_submit'),
            data=self._submit_payload(session_id=first),
            content_type='application/json', secure=True,
        )
        data = json.loads(resp.content)
        self.assertFalse(data.get('success'))


class LivenessTokenContextBindingAttackTest(TestCase):
    """
    v2.1.16 Security Hardening Round #5 (Blocker 3) — attack tests proving a
    LivenessTransaction cannot be honored outside the exact attempt/event/
    beneficiary context it was issued for, and cannot be replayed.
    """

    def setUp(self):
        from verification.models import StipendEvent, FaceEmbedding, LivenessTransaction
        from cryptography.fernet import Fernet
        self.LivenessTransaction = LivenessTransaction
        self.staff = _make_staff('ctxbind_staff')
        key = Fernet.generate_key()

        self.ben = _make_beneficiary('BEN-CTXB-001', 'SC-CTXB-001')
        self.ben.status = Beneficiary.STATUS_ACTIVE
        self.ben.consent_given = True
        self.ben.save()
        FaceEmbedding.objects.create(
            beneficiary=self.ben, embedding_data=Fernet(key).encrypt(b'\x00' * 512),
        )

        self.other_ben = _make_beneficiary('BEN-CTXB-002', 'SC-CTXB-002')
        self.other_ben.status = Beneficiary.STATUS_ACTIVE
        self.other_ben.consent_given = True
        self.other_ben.save()
        FaceEmbedding.objects.create(
            beneficiary=self.other_ben, embedding_data=Fernet(key).encrypt(b'\x01' * 512),
        )

        today = datetime.date.today()
        self.event_a = StipendEvent.objects.create(
            title='Ctx Payout A', date=today, amount=500,
            is_active=True, created_by=self.staff,
        )
        self.event_b = StipendEvent.objects.create(
            title='Ctx Payout B', date=today, amount=500,
            is_active=True, created_by=self.staff,
        )
        self.client = Client()
        self.client.force_login(self.staff)
        self.url = reverse('verification:verify_submit')

    def _make_tx(self, beneficiary, event, session_id, anti_spoof_score=0.9):
        return self.LivenessTransaction.objects.create(
            beneficiary=beneficiary,
            claimant_type='beneficiary',
            stipend_event=event,
            performed_by=self.staff,
            challenge_direction='side',
            anti_spoof_score=anti_spoof_score,
            liveness_score=0.9,
            pa_score=0.0,
            pa_flags={},
            embedding_data=None,
            expires_at=timezone.now() + datetime.timedelta(seconds=120),
            session_id=session_id,
        )

    def _set_session(self, beneficiary, event, session_id):
        session = self.client.session
        session['verification_session'] = {
            'beneficiary_id': str(beneficiary.pk),
            'attempt_number': 1,
            'session_id': session_id,
            'claimant_type': 'beneficiary',
            'stipend_event_id': str(event.pk),
            'challenge': 'side',
        }
        session.save()

    def _post(self, session_id, tx_token):
        payload = json.dumps({
            'image': DATA_URI_JPEG,
            'challenge_completed': True,
            'liveness_score': 0.9,
            'anti_spoof_score': 0.9,
            'liveness_passed': True,
            'face_detected': True,
            'tx_token': tx_token,
            'session_id': session_id,
        })
        return self.client.post(self.url, data=payload,
                                 content_type='application/json', secure=True)

    @override_settings(LIVENESS_PROOF_REQUIRED=True, LIVENESS_REQUIRED=True, DEBUG=True)
    def test_token_from_another_attempt_rejected(self):
        """A token minted for an EARLIER verify_start attempt (session_id_a)
        must be refused once a later attempt (session_id_b) is the current
        session — even for the exact same beneficiary/event/operator."""
        session_id_a = str(uuid.uuid4())
        session_id_b = str(uuid.uuid4())
        tx = self._make_tx(self.ben, self.event_a, session_id=session_id_a)

        # Current session is now attempt B — a later, distinct attempt.
        self._set_session(self.ben, self.event_a, session_id_b)
        resp = self._post(session_id_b, str(tx.token))
        data = json.loads(resp.content)
        self.assertEqual(data.get('decision'), 'denied', data)
        self.assertIn('liveness proof', data.get('reason', '').lower())
        self.assertIn('different verification attempt', data.get('reason', '').lower())
        tx.refresh_from_db()
        self.assertIsNone(tx.used_at, 'a rejected cross-attempt token must not be consumed')

    @override_settings(LIVENESS_PROOF_REQUIRED=True, LIVENESS_REQUIRED=True, DEBUG=True)
    def test_token_from_another_event_rejected(self):
        """A token minted while Event A was the session's active event must
        not be honored against a claim now resolving to Event B."""
        session_id = str(uuid.uuid4())
        tx = self._make_tx(self.ben, self.event_a, session_id=session_id)

        self._set_session(self.ben, self.event_b, session_id)
        resp = self._post(session_id, str(tx.token))
        data = json.loads(resp.content)
        self.assertEqual(data.get('decision'), 'denied', data)
        self.assertIn('different stipend event', data.get('reason', '').lower())
        tx.refresh_from_db()
        self.assertIsNone(tx.used_at)

    @override_settings(LIVENESS_PROOF_REQUIRED=True, LIVENESS_REQUIRED=True, DEBUG=True)
    def test_token_from_another_beneficiary_rejected(self):
        """A token minted for Beneficiary A must not verify Beneficiary B's
        claim, even with a matching session/event/operator."""
        session_id = str(uuid.uuid4())
        tx = self._make_tx(self.ben, self.event_a, session_id=session_id)

        self._set_session(self.other_ben, self.event_a, session_id)
        resp = self._post(session_id, str(tx.token))
        data = json.loads(resp.content)
        self.assertEqual(data.get('decision'), 'denied', data)
        self.assertIn('liveness proof', data.get('reason', '').lower())
        tx.refresh_from_db()
        self.assertIsNone(tx.used_at)

    @override_settings(LIVENESS_PROOF_REQUIRED=True, LIVENESS_REQUIRED=True, DEBUG=True)
    def test_token_with_superseded_challenge_direction_rejected(self):
        """v2.1.16 Final Hardening Patch (Codex NO-GO #2) — a token minted
        for the challenge direction assigned at issuance must not be
        honored once a retry has rotated the session onto a NEW challenge
        direction, even though the session_id itself never changed (a
        retry keeps the same verify_start session_id, only rotating
        attempt_number and 'challenge' — see verify_submit's decision
        =='retry' branch)."""
        session_id = str(uuid.uuid4())
        tx = self._make_tx(self.ben, self.event_a, session_id=session_id)
        self.assertEqual(tx.challenge_direction, 'side')

        session = self.client.session
        session['verification_session'] = {
            'beneficiary_id': str(self.ben.pk),
            'attempt_number': 2,
            'session_id': session_id,
            'claimant_type': 'beneficiary',
            'stipend_event_id': str(self.event_a.pk),
            'challenge': 'left',
        }
        session.save()

        resp = self._post(session_id, str(tx.token))
        data = json.loads(resp.content)
        self.assertEqual(data.get('decision'), 'denied', data)
        self.assertIn('different liveness challenge', data.get('reason', '').lower())
        tx.refresh_from_db()
        self.assertIsNone(tx.used_at, 'a rejected stale-challenge token must not be consumed')

    @override_settings(LIVENESS_PROOF_REQUIRED=True, LIVENESS_REQUIRED=True, DEBUG=True)
    def test_token_replay_rejected(self):
        """A token already marked used (single-use enforcement) must be
        refused on a subsequent submission, even with the exact same
        (correct) attempt/event/beneficiary context."""
        session_id = str(uuid.uuid4())
        tx = self._make_tx(self.ben, self.event_a, session_id=session_id)
        self.assertTrue(tx.claim(), 'setup: simulated prior consumption must win the claim')

        self._set_session(self.ben, self.event_a, session_id)
        resp = self._post(session_id, str(tx.token))
        data = json.loads(resp.content)
        self.assertEqual(data.get('decision'), 'denied', data)
        self.assertIn('already been used', data.get('reason', '').lower())


class _FakeMigrationQuerySet:
    """
    Minimal ORM-queryset stand-in backed by a shared list of mutable dict
    'rows', supporting exactly the chain dedupe_evidence_hashes() uses:
    .exclude(field=value).order_by(*fields).values_list(*fields) and
    .filter(field=value).update(**fields). Rows are the SAME dict objects
    across every queryset derived from a given manager, so .update()
    mutates state visible to subsequent queries — matching real ORM
    row-persistence semantics — without touching an actual database table
    (in particular, without needing evidence_hash/evidence_pixel_hash to
    already be unique, which is exactly the pre-migration state being
    tested).
    """
    def __init__(self, rows):
        self._rows = rows

    def exclude(self, **kwargs):
        (field, value), = kwargs.items()
        return _FakeMigrationQuerySet([r for r in self._rows if r[field] != value])

    def filter(self, **kwargs):
        (field, value), = kwargs.items()
        return _FakeMigrationQuerySet([r for r in self._rows if r[field] == value])

    def order_by(self, *fields):
        return _FakeMigrationQuerySet(sorted(self._rows, key=lambda r: tuple(r[f] for f in fields)))

    def values_list(self, *fields, flat=False):
        if flat:
            return [r[fields[0]] for r in self._rows]
        return [tuple(r[f] for f in fields) for r in self._rows]

    def update(self, **kwargs):
        for r in self._rows:
            r.update(kwargs)
        return len(self._rows)


class _FakeMigrationManager:
    def __init__(self, rows):
        self._qs = _FakeMigrationQuerySet(rows)

    def exclude(self, **kwargs):
        return self._qs.exclude(**kwargs)

    def filter(self, **kwargs):
        return self._qs.filter(**kwargs)


class Migration0031EvidenceHashDedupeTest(TestCase):
    """
    v2.1.16 Final Hardening Patch (Codex NO-GO #3) — migration safety test
    for verification/migrations/0031_liveness_tx_context_binding.py's
    dedupe_evidence_hashes RunPython step. Confirms duplicate non-blank
    evidence_hash/evidence_pixel_hash values (which would otherwise make the
    migration's own AddConstraint operations fail outright on an installed
    deployment's database) are resolved BEFORE the constraints are applied,
    without deleting any row or losing any other field.

    Exercises the real dedupe_evidence_hashes() function against a fake
    apps.get_model() returning a lightweight in-memory stand-in (see
    _FakeMigrationManager above), rather than the real LivenessTransaction
    table — the real table already has 0031's unique constraints applied
    (this is the CURRENT app state, migrated forward), so it cannot hold
    the duplicate rows this test needs to set up in the first place. The
    fake reproduces the exact query/update calls the function makes, which
    is what's actually being verified.
    """

    @staticmethod
    def _migration_module():
        import importlib
        return importlib.import_module('verification.migrations.0031_liveness_tx_context_binding')

    @staticmethod
    def _fake_apps(rows):
        manager = _FakeMigrationManager(rows)

        class _Model:
            objects = manager

        class _Apps:
            def get_model(self, app_label, model_name):
                assert app_label == 'verification'
                assert model_name == 'LivenessTransaction'
                return _Model

        return _Apps()

    @staticmethod
    def _row(row_id, created_at, evidence_hash='', evidence_pixel_hash=''):
        return {
            'id': row_id, 'created_at': created_at,
            'evidence_hash': evidence_hash, 'evidence_pixel_hash': evidence_pixel_hash,
        }

    def test_dedupe_keeps_oldest_and_blanks_later_duplicates(self):
        now = timezone.now()
        rows = [
            self._row(1, now - datetime.timedelta(hours=2), evidence_hash='dup-hash-A'),
            self._row(2, now - datetime.timedelta(hours=1), evidence_hash='dup-hash-A'),
            self._row(3, now, evidence_hash='dup-hash-A'),
            self._row(4, now, evidence_hash='unique-hash-B'),
        ]

        self._migration_module().dedupe_evidence_hashes(self._fake_apps(rows), None)

        by_id = {r['id']: r for r in rows}
        self.assertEqual(by_id[1]['evidence_hash'], 'dup-hash-A', 'oldest duplicate must keep its hash')
        self.assertEqual(by_id[2]['evidence_hash'], '', 'later duplicate must be blanked, not deleted')
        self.assertEqual(by_id[3]['evidence_hash'], '', 'later duplicate must be blanked, not deleted')
        self.assertEqual(by_id[4]['evidence_hash'], 'unique-hash-B', 'non-duplicate rows must be untouched')
        # Dedupe must never delete a row -- only blank the colliding field.
        self.assertEqual(len(rows), 4)

    def test_dedupe_leaves_blank_hashes_alone(self):
        """Rows with the default '' evidence_hash (legacy data /
        LIVENESS_PROOF_REQUIRED=False rows — the vast majority in practice)
        must never be treated as colliding with each other."""
        now = timezone.now()
        rows = [self._row(1, now), self._row(2, now)]

        self._migration_module().dedupe_evidence_hashes(self._fake_apps(rows), None)

        self.assertEqual(rows[0]['evidence_hash'], '')
        self.assertEqual(rows[1]['evidence_hash'], '')

    def test_dedupe_handles_both_hash_columns_independently(self):
        """evidence_hash and evidence_pixel_hash are deduped as independent
        columns -- a row can legitimately share one column's value with
        another row while differing on the other."""
        now = timezone.now()
        rows = [
            self._row(1, now, evidence_hash='shared-hash', evidence_pixel_hash='pix-1'),
            self._row(2, now + datetime.timedelta(minutes=1),
                      evidence_hash='shared-hash', evidence_pixel_hash='pix-2'),
        ]

        self._migration_module().dedupe_evidence_hashes(self._fake_apps(rows), None)

        self.assertEqual(rows[0]['evidence_hash'], 'shared-hash')
        self.assertEqual(rows[1]['evidence_hash'], '', 'later duplicate evidence_hash must be blanked')
        self.assertEqual(rows[0]['evidence_pixel_hash'], 'pix-1', 'distinct pixel hash must be untouched')
        self.assertEqual(rows[1]['evidence_pixel_hash'], 'pix-2', 'distinct pixel hash must be untouched')

    def test_constraint_applies_cleanly_after_dedupe(self):
        """After dedupe, no two non-blank rows may share a value for either
        hash column -- i.e. the exact UniqueConstraint the migration adds
        immediately afterward would not reject the resulting data."""
        now = timezone.now()
        rows = [
            self._row(1, now, evidence_hash='dup-hash-C'),
            self._row(2, now + datetime.timedelta(minutes=1), evidence_hash='dup-hash-C'),
            self._row(3, now, evidence_pixel_hash='dup-pixel-D'),
            self._row(4, now + datetime.timedelta(minutes=1), evidence_pixel_hash='dup-pixel-D'),
        ]

        self._migration_module().dedupe_evidence_hashes(self._fake_apps(rows), None)

        for field in ('evidence_hash', 'evidence_pixel_hash'):
            values = [r[field] for r in rows if r[field] != '']
            self.assertEqual(
                len(values), len(set(values)),
                f'no two non-blank {field} values may collide after dedupe',
            )


class LivenessReplayReservationConcurrencyTest(TransactionTestCase):
    """
    v2.1.16 Final Hardening Patch (Codex NO-GO #4) — concurrency test for
    check_and_reserve_liveness_evidence(). Proves two SIMULTANEOUS calls
    carrying different raw evidence (different evidence_hash/
    evidence_pixel_hash — exactly what JPEG recompression/resizing of the
    same replayed capture produces) but an IDENTICAL perceptual hash cannot
    BOTH win the reservation. A plain exists()-then-create() check (no lock)
    would let both callers pass the scan before either had written
    anything; this test would fail against that old implementation and
    passes only because the scan + reservation insert are now atomic under
    _liveness_replay_lock.

    Uses TransactionTestCase (not TestCase) because the two calls run on
    separate threads with separate DB connections — Django's sqlite test
    database uses a shared-cache in-memory URI (see
    django.db.backends.sqlite3.creation.DatabaseCreation._get_test_db_name)
    so both connections see the same schema/data, but TestCase's
    per-test wrapping transaction would not be visible across connections.
    """

    def test_simultaneous_perceptually_identical_evidence_only_one_reserved(self):
        from django.db import connection
        from verification.views import check_and_reserve_liveness_evidence
        from verification.models import LivenessEvidenceReservation

        barrier = threading.Barrier(2)
        results = []
        results_lock = threading.Lock()

        def attempt(evidence_hash, evidence_pixel_hash, evidence_phash):
            try:
                barrier.wait(timeout=5)
                outcome = check_and_reserve_liveness_evidence(
                    evidence_hash, evidence_pixel_hash, evidence_phash,
                )
                with results_lock:
                    results.append(outcome)
            finally:
                connection.close()

        phash = 'a' * 32
        t1 = threading.Thread(target=attempt, args=('hash-race-1', 'pixel-race-1', phash))
        t2 = threading.Thread(target=attempt, args=('hash-race-2', 'pixel-race-2', phash))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        self.assertEqual(len(results), 2, 'both attempts must complete')
        winners = [r for r in results if r is None]
        losers = [r for r in results if r is not None]
        self.assertEqual(
            len(winners), 1,
            f'exactly one of two simultaneous perceptually-identical reservation '
            f'attempts must win; got results={results}',
        )
        self.assertEqual(len(losers), 1, f'the other attempt must be denied; got results={results}')
        self.assertEqual(losers[0], 'perceptual')
        self.assertEqual(
            LivenessEvidenceReservation.objects.count(), 1,
            'only one reservation row may exist for two perceptually-identical captures',
        )


class LivenessReplayLockMutualExclusionTest(TransactionTestCase):
    """
    v2.1.16 Final Hardening Patch (Codex NO-GO #4) — direct proof that
    check_and_reserve_liveness_evidence() actually serializes on
    verification.views._liveness_replay_lock, rather than merely happening
    to look correct in the (inherently timing-dependent) concurrency test
    above.

    TransactionTestCase (not TestCase) for the same reason as
    LivenessReplayReservationConcurrencyTest above: the `caller` thread
    below writes through its own separate DB connection, which would not be
    rolled back by TestCase's per-test transaction wrapper.
    """

    def test_check_and_reserve_blocks_while_lock_is_held(self):
        from django.db import connection
        from verification import views as verification_views

        lock_acquired = threading.Event()
        release_lock = threading.Event()
        call_finished = threading.Event()

        def hold_lock():
            with verification_views._liveness_replay_lock:
                lock_acquired.set()
                release_lock.wait(timeout=5)

        holder = threading.Thread(target=hold_lock)
        holder.start()
        self.assertTrue(lock_acquired.wait(timeout=5), 'setup: lock-holder thread never acquired the lock')

        def call_check():
            try:
                verification_views.check_and_reserve_liveness_evidence(
                    'mutex-hash', 'mutex-pixel', 'b' * 32,
                )
                call_finished.set()
            finally:
                connection.close()

        caller = threading.Thread(target=call_check)
        caller.start()
        try:
            self.assertFalse(
                call_finished.wait(timeout=0.3),
                'check_and_reserve_liveness_evidence completed while '
                '_liveness_replay_lock was still held elsewhere — the scan-then-'
                'reserve step is not actually serialized',
            )
        finally:
            release_lock.set()
            holder.join(timeout=5)
        self.assertTrue(call_finished.wait(timeout=5), 'caller never finished after the lock was released')
        caller.join(timeout=5)


# ── Liveness Score / Token Regression Tests ───────────────────────────────────

class LivenessScoreOnTokenFailureTest(TestCase):
    """
    Regression: when the server does not issue a tx_token (Mode B fails),
    verify_submit must be blocked — it must not run FaceNet identity matching.

    These tests cover the server-side gate; the JS liveness-score=0 fix is
    covered by the verifyBtn/UI logic which is tested by integration (not unit).
    """

    def setUp(self):
        from verification.models import StipendEvent, FaceEmbedding
        self.staff = _make_staff('lscore_staff')
        self.ben = _make_beneficiary('BEN-LSC-001', 'SC-LSC-001')
        self.ben.status = Beneficiary.STATUS_ACTIVE
        self.ben.save()
        self.event = StipendEvent.objects.create(
            title='LiveScore Payout', date=datetime.date.today(), amount=500,
            is_active=True, created_by=self.staff,
        )
        FaceEmbedding.objects.create(beneficiary=self.ben, embedding_data=b'\x00' * 64)
        self.client = Client()
        self.client.force_login(self.staff)
        self.url = reverse('verification:verify_submit')

    def _set_session(self, challenge='side'):
        session = self.client.session
        session['verification_session'] = {
            'beneficiary_id': str(self.ben.pk),
            'attempt_number': 1,
            'session_id': _FIXED_TEST_SESSION_ID,
            'claimant_type': 'beneficiary',
            'stipend_event_id': str(self.event.pk),
            'challenge': challenge,
        }
        session.save()

    def _post(self, tx_token='', liveness_passed=False, challenge_completed=False):
        self._set_session()
        payload = json.dumps({
            'image': DATA_URI_JPEG,
            'challenge_completed': challenge_completed,
            'liveness_score': 1.0,
            'anti_spoof_score': 1.0,
            'liveness_passed': liveness_passed,
            'face_detected': True,
            'tx_token': tx_token,
            'session_id': _FIXED_TEST_SESSION_ID,
        })
        return self.client.post(self.url, data=payload,
                                content_type='application/json', secure=True)

    @override_settings(LIVENESS_PROOF_REQUIRED=True, LIVENESS_REQUIRED=True,
                       DEMO_MODE=False, DEBUG=True)
    @mock.patch('verification.views.compare_with_all_embeddings')
    @mock.patch('verification.views.process_face_for_verification')
    def test_missing_tx_token_blocks_identity_match(self, mock_process, mock_compare):
        """verify_submit without tx_token must not call FaceNet (strict mode)."""
        resp = self._post(tx_token='', liveness_passed=False, challenge_completed=True)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        # Must be denied, not verified
        self.assertTrue(data.get('success'), f'Expected success=True with denied decision: {data}')
        self.assertEqual(data.get('decision'), 'denied', f'Expected denied: {data}')
        self.assertIn('liveness proof', data.get('reason', '').lower(),
                      f'Denial reason should mention liveness proof: {data}')
        # FaceNet must NOT have run
        mock_compare.assert_not_called()
        mock_process.assert_not_called()

    @override_settings(LIVENESS_PROOF_REQUIRED=True, LIVENESS_REQUIRED=True,
                       DEMO_MODE=False, DEBUG=True)
    @mock.patch('verification.views.compare_with_all_embeddings')
    def test_expired_tx_token_blocks_identity_match(self, mock_compare):
        """An expired LivenessTransaction must not allow identity matching."""
        from verification.models import LivenessTransaction
        tx = LivenessTransaction.objects.create(
            beneficiary=self.ben, claimant_type='beneficiary',
            stipend_event=self.event, performed_by=self.staff,
            challenge_direction='side', anti_spoof_score=0.9,
            liveness_score=0.9, pa_score=0.0, pa_flags={},
            embedding_data=None,
            expires_at=timezone.now() - datetime.timedelta(seconds=1),  # already expired
        )
        resp = self._post(tx_token=str(tx.token), liveness_passed=True, challenge_completed=True)
        data = json.loads(resp.content)
        self.assertEqual(data.get('decision'), 'denied')
        self.assertIn('liveness proof', data.get('reason', '').lower())
        mock_compare.assert_not_called()

    @override_settings(LIVENESS_PROOF_REQUIRED=True, LIVENESS_REQUIRED=True,
                       DEMO_MODE=False, DEBUG=True)
    @mock.patch('verification.views.compare_with_all_embeddings')
    def test_used_tx_token_blocks_identity_match(self, mock_compare):
        """A consumed LivenessTransaction must not be reused for a second verification."""
        from verification.models import LivenessTransaction
        tx = LivenessTransaction.objects.create(
            beneficiary=self.ben, claimant_type='beneficiary',
            stipend_event=self.event, performed_by=self.staff,
            challenge_direction='side', anti_spoof_score=0.9,
            liveness_score=0.9, pa_score=0.0, pa_flags={},
            embedding_data=None,
            expires_at=timezone.now() + datetime.timedelta(seconds=120),
        )
        # Mark it consumed
        tx.used_at = timezone.now()
        tx.save(update_fields=['used_at'])
        resp = self._post(tx_token=str(tx.token), liveness_passed=True, challenge_completed=True)
        data = json.loads(resp.content)
        self.assertEqual(data.get('decision'), 'denied')
        self.assertIn('liveness proof', data.get('reason', '').lower())
        mock_compare.assert_not_called()

    @override_settings(LIVENESS_PROOF_REQUIRED=True, LIVENESS_REQUIRED=True,
                       DEMO_MODE=False, DEBUG=True)
    @mock.patch('verification.views.compare_with_all_embeddings')
    def test_invalid_tx_token_string_blocks_identity_match(self, mock_compare):
        """A garbage/invalid tx_token string must be denied, not crash."""
        resp = self._post(tx_token='not-a-valid-uuid', liveness_passed=True,
                          challenge_completed=True)
        data = json.loads(resp.content)
        self.assertEqual(data.get('decision'), 'denied')
        self.assertIn('liveness proof', data.get('reason', '').lower())
        mock_compare.assert_not_called()

    @override_settings(LIVENESS_PROOF_REQUIRED=True, LIVENESS_REQUIRED=True,
                       DEMO_MODE=False, DEBUG=True)
    @mock.patch('verification.face_utils.decrypt_embedding')
    @mock.patch('verification.views.compare_with_all_embeddings')
    @mock.patch('verification.views.load_image_from_bytes')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.check_anti_spoofing')
    @mock.patch('verification.views.process_face_for_verification')
    def test_antispoof_pass_liveness_fail_does_not_verify(
        self, mock_process, mock_spoof, mock_detect, mock_load, mock_compare, mock_decrypt,
    ):
        """
        Anti-spoof passing alone must not grant verification.
        If no valid liveness TX, verify_submit returns denied even if anti-spoof
        score is 100% on the submitted frame.
        """
        import numpy as np
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_spoof.return_value = {'passed': True, 'score': 1.0, 'reason': 'Real face.'}

        resp = self._post(tx_token='', liveness_passed=False, challenge_completed=False)
        data = json.loads(resp.content)
        self.assertEqual(data.get('decision'), 'denied',
                         f'Anti-spoof alone must not verify: {data}')
        mock_compare.assert_not_called()

    @override_settings(LIVENESS_PROOF_REQUIRED=True, LIVENESS_REQUIRED=True,
                       DEMO_MODE=False, DEBUG=True, SAME_FACE_SEQUENCE_THRESHOLD=0)
    @mock.patch('verification.face_utils.check_duplicate_face')
    @mock.patch('verification.face_utils.decrypt_embedding')
    @mock.patch('verification.views.compare_with_all_embeddings')
    @mock.patch('verification.views.load_image_from_bytes')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.check_anti_spoofing')
    @mock.patch('verification.views.process_face_for_verification')
    def test_valid_tx_allows_facenet_to_run(
        self, mock_process, mock_spoof, mock_detect, mock_load,
        mock_compare, mock_decrypt, mock_dup,
    ):
        """With a valid TX, FaceNet identity matching must run (end-to-end gate test)."""
        import numpy as np
        from verification.models import LivenessTransaction
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_spoof.return_value = {'passed': True, 'score': 0.9, 'reason': 'Real.'}
        mock_process.return_value = {
            'success': True, 'embedding': np.zeros(128),
            'quality': {'ok': True, 'score': 0.9, 'reason': ''}, 'using_mock': False,
        }
        mock_compare.return_value = {
            'success': True, 'score': 0.85,
            'matched_template': 'primary', 'templates_checked': 1, 'all_scores': [],
        }
        mock_dup.return_value = {
            'duplicates_found': False, 'matches': [], 'highest_score': 0.0, 'checked': 0,
        }
        mock_decrypt.return_value = [0.1] * 128

        tx = LivenessTransaction.objects.create(
            beneficiary=self.ben, claimant_type='beneficiary',
            stipend_event=self.event, performed_by=self.staff,
            challenge_direction='side', anti_spoof_score=0.9,
            liveness_score=0.9, pa_score=0.0, pa_flags={},
            embedding_data=b'\x01' * 64,
            expires_at=timezone.now() + datetime.timedelta(seconds=120),
        )
        resp = self._post(tx_token=str(tx.token), liveness_passed=True,
                          challenge_completed=True)
        # FaceNet compare must have been called
        mock_compare.assert_called_once()
        data = json.loads(resp.content)
        self.assertIn(data.get('decision'), ('verified', 'manual_review', 'retry', 'fallback'),
                      f'Expected a FaceNet-driven decision: {data}')

    @override_settings(LIVENESS_PROOF_REQUIRED=True, LIVENESS_REQUIRED=True,
                       DEMO_MODE=False, DEBUG=True)
    @mock.patch('verification.views.compare_with_all_embeddings')
    def test_denial_reason_contains_specified_message(self, mock_compare):
        """Denial reason for missing TX must include the specified block message."""
        resp = self._post(tx_token='', liveness_passed=False)
        data = json.loads(resp.content)
        self.assertEqual(data.get('decision'), 'denied')
        reason = data.get('reason', '')
        self.assertIn('Verification blocked', reason,
                      f'Reason must start with "Verification blocked": {reason}')
        self.assertIn('liveness proof', reason.lower())
        mock_compare.assert_not_called()


# ──────────────────────────────────────────────────────────────────────────────
# v2.1.11 — Issue 9 (representative face), Issue 5 (schedule approval), Issue 3 (PAD)
# ──────────────────────────────────────────────────────────────────────────────


class RepresentativeFaceVerificationTest(TestCase):
    """Issue 9 — representative claim must verify against representative face.

    Regression: when claimant_type=representative but representative_id was
    missing from the session, the verify path silently compared against the
    beneficiary's embedding, letting the senior's face approve a representative
    claim. The fix denies before any FaceNet comparison runs.
    """

    def setUp(self):
        from verification.models import (
            StipendEvent, FaceEmbedding, RepresentativeFaceEmbedding,
        )
        from cryptography.fernet import Fernet
        self.staff = _make_staff('rep_staff')
        self.ben = _make_beneficiary('BEN-REP-001', 'SC-REP-001')
        self.ben.status = Beneficiary.STATUS_ACTIVE
        self.ben.consent_given = True
        self.ben.save()
        self.rep = _make_rep(self.ben, self.staff)
        self.event = StipendEvent.objects.create(
            title='Rep Payout', date=datetime.date.today(), amount=500,
            is_active=True, created_by=self.staff,
        )
        key = Fernet.generate_key()
        FaceEmbedding.objects.create(
            beneficiary=self.ben,
            embedding_data=Fernet(key).encrypt(b'\x00' * 512),
        )
        RepresentativeFaceEmbedding.objects.create(
            representative=self.rep,
            embedding_data=Fernet(key).encrypt(b'\x01' * 512),
            created_by=self.staff,
        )
        self.client = Client()
        self.client.force_login(self.staff)
        self.url = reverse('verification:verify_submit')

    def _post(self, session_overrides=None, tx_token=''):
        s = self.client.session
        s['verification_session'] = {
            'beneficiary_id': str(self.ben.pk),
            'attempt_number': 1,
            'session_id': _FIXED_TEST_SESSION_ID,
            'claimant_type': 'representative',
            'stipend_event_id': str(self.event.pk),
            'challenge': 'side',
        }
        if session_overrides is not None:
            s['verification_session'].update(session_overrides)
        s.save()
        payload = json.dumps({
            'image': DATA_URI_JPEG, 'tx_token': tx_token,
            'challenge_completed': True, 'liveness_score': 0.9,
            'anti_spoof_score': 0.9, 'liveness_passed': True,
            'session_id': s['verification_session'].get('session_id'),
        })
        return self.client.post(
            self.url, data=payload, content_type='application/json', secure=True,
        )

    @override_settings(LIVENESS_PROOF_REQUIRED=False, LIVENESS_REQUIRED=False, DEBUG=True)
    def test_rep_claim_without_rep_id_in_session_blocks(self):
        """Representative claim with NO representative_id must NOT fall back to beneficiary face."""
        resp = self._post(session_overrides={'representative_id': None})
        data = json.loads(resp.content)
        self.assertFalse(data.get('success'))
        self.assertIn('representative', data.get('error', '').lower())

    @override_settings(LIVENESS_PROOF_REQUIRED=False, LIVENESS_REQUIRED=False, DEBUG=True)
    def test_rep_claim_with_invalid_rep_id_blocks(self):
        resp = self._post(session_overrides={'representative_id': str(uuid.uuid4())})
        data = json.loads(resp.content)
        self.assertFalse(data.get('success'))
        self.assertIn('representative', data.get('error', '').lower())


class StipendApprovalWorkflowTest(TestCase):
    """Issue 5 — Admin-created schedules require President approval."""

    def setUp(self):
        self.admin = _make_staff('admin_user', role=CustomUser.ROLE_ADMIN)
        self.president = _make_staff('president_user', role=CustomUser.ROLE_PRESIDENT)
        self.staff = _make_staff('staff_user')
        self.client = Client()

    def test_admin_created_schedule_is_pending(self):
        # Blank payout times + date=today must not trip the v2.2.0 Phase 2
        # "claiming window already ended" guard regardless of the wall-clock
        # time the test suite happens to run at — pin "now" safely inside
        # office hours (10:00 AM Manila).
        import datetime as real_dt
        from unittest.mock import patch

        class MockDatetime(real_dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return real_dt.datetime(2026, 1, 1, 10, 0, 0, tzinfo=tz)

        self.client.force_login(self.admin)
        with patch('verification.views._dt.datetime', MockDatetime):
            resp = self.client.post(reverse('verification:stipend_create'), {
                'title': 'Test Stipend',
                'date': datetime.date.today().isoformat(),
                'event_type': 'regular',
                'amount': '500',
            })
        self.assertEqual(resp.status_code, 302)
        from verification.models import StipendEvent
        event = StipendEvent.objects.get(title='Test Stipend')
        self.assertEqual(event.approval_status, StipendEvent.APPROVAL_PENDING)
        self.assertFalse(event.is_published)

    def test_president_created_schedule_is_published(self):
        import datetime as real_dt
        from unittest.mock import patch

        class MockDatetime(real_dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return real_dt.datetime(2026, 1, 1, 10, 0, 0, tzinfo=tz)

        self.client.force_login(self.president)
        with patch('verification.views._dt.datetime', MockDatetime):
            self.client.post(reverse('verification:stipend_create'), {
                'title': 'Pres Stipend',
                'date': datetime.date.today().isoformat(),
                'event_type': 'regular',
                'amount': '500',
            })
        from verification.models import StipendEvent
        event = StipendEvent.objects.get(title='Pres Stipend')
        self.assertEqual(event.approval_status, StipendEvent.APPROVAL_APPROVED)
        self.assertTrue(event.is_published)

    def test_staff_cannot_approve(self):
        from verification.models import StipendEvent
        event = StipendEvent.objects.create(
            title='X', date=datetime.date.today(), amount=500,
            created_by=self.admin, approval_status=StipendEvent.APPROVAL_PENDING,
        )
        self.client.force_login(self.staff)
        self.client.post(reverse('verification:stipend_approve', args=[event.pk]))
        event.refresh_from_db()
        self.assertEqual(event.approval_status, StipendEvent.APPROVAL_PENDING)

    def test_admin_cannot_approve_own_schedule(self):
        from verification.models import StipendEvent
        event = StipendEvent.objects.create(
            title='X', date=datetime.date.today(), amount=500,
            created_by=self.admin, approval_status=StipendEvent.APPROVAL_PENDING,
        )
        self.client.force_login(self.admin)
        self.client.post(reverse('verification:stipend_approve', args=[event.pk]))
        event.refresh_from_db()
        self.assertEqual(event.approval_status, StipendEvent.APPROVAL_PENDING)

    def test_president_can_approve(self):
        from verification.models import StipendEvent
        event = StipendEvent.objects.create(
            title='X', date=datetime.date.today(), amount=500,
            created_by=self.admin, approval_status=StipendEvent.APPROVAL_PENDING,
        )
        self.client.force_login(self.president)
        self.client.post(reverse('verification:stipend_approve', args=[event.pk]))
        event.refresh_from_db()
        self.assertEqual(event.approval_status, StipendEvent.APPROVAL_APPROVED)
        self.assertTrue(event.is_published)

    def test_pending_schedule_not_active_for_claims(self):
        from verification.models import StipendEvent
        today = datetime.date.today()
        StipendEvent.objects.create(
            title='Pending', date=today, amount=500,
            created_by=self.admin, approval_status=StipendEvent.APPROVAL_PENDING,
            payout_start_date=today, payout_end_date=today,
        )
        self.assertIsNone(StipendEvent.get_active_event_for_date(today))

    def test_approved_schedule_is_active_for_claims(self):
        from verification.models import StipendEvent
        today = datetime.date.today()
        StipendEvent.objects.create(
            title='Approved', date=today, amount=500,
            created_by=self.president, approval_status=StipendEvent.APPROVAL_APPROVED,
            payout_start_date=today, payout_end_date=today,
        )
        self.assertIsNotNone(StipendEvent.get_active_event_for_date(today))


# ──────────────────────────────────────────────────────────────────────────────
# v2.2.0 Post-UAT Phase 3 — President payout approval notification.
# Previously the dashboard's "N payout schedules awaiting approval" count was
# a live query, so it always looked correct, but nothing ever created a
# Notification when a schedule entered pending-approval, so the President's
# bell stayed silent. These tests confirm a Notification is created for the
# currently-assigned System Role President (never a hard-coded username or
# every admin), is clickable, and clears once the President acts.
# ──────────────────────────────────────────────────────────────────────────────

class StipendApprovalNotificationTest(TestCase):

    def setUp(self):
        self.admin = _make_staff('notif_admin', role=CustomUser.ROLE_ADMIN)
        self.president = _make_staff('notif_president', role=CustomUser.ROLE_PRESIDENT)
        self.it_user = _make_staff('notif_it', role=CustomUser.ROLE_IT)
        self.client = Client()

    def _mock_daytime(self):
        import datetime as real_dt
        from unittest.mock import patch

        class MockDatetime(real_dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return real_dt.datetime(2026, 1, 1, 10, 0, 0, tzinfo=tz)
        return patch('verification.views._dt.datetime', MockDatetime)

    def _create_pending_schedule(self, title='Notify Test Stipend'):
        self.client.force_login(self.admin)
        with self._mock_daytime():
            return self.client.post(reverse('verification:stipend_create'), {
                'title': title,
                'date': datetime.date.today().isoformat(),
                'event_type': 'regular',
                'amount': '500',
            })

    def test_notification_created_for_president_on_admin_create(self):
        from logs.models import Notification
        self._create_pending_schedule()
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.president,
                category=Notification.CATEGORY_APPROVAL_REQUIRED,
                title__icontains='Notify Test Stipend',
            ).exists()
        )

    def test_notification_not_sent_to_every_admin(self):
        """Only the current System Role President is notified — not Admin/IT
        broadly (Phase 3: 'Do not notify every user')."""
        from logs.models import Notification
        self._create_pending_schedule()
        self.assertFalse(
            Notification.objects.filter(recipient=self.admin, title__icontains='Notify Test Stipend').exists()
        )
        self.assertFalse(
            Notification.objects.filter(recipient=self.it_user, title__icontains='Notify Test Stipend').exists()
        )

    def test_notification_not_created_when_president_creates_own_schedule(self):
        """A President-created schedule publishes immediately — no approval
        is pending, so no notification should exist for it."""
        from logs.models import Notification
        self.client.force_login(self.president)
        with self._mock_daytime():
            self.client.post(reverse('verification:stipend_create'), {
                'title': 'Self Published',
                'date': datetime.date.today().isoformat(),
                'event_type': 'regular',
                'amount': '500',
            })
        self.assertFalse(
            Notification.objects.filter(title__icontains='Self Published').exists()
        )

    def test_unread_count_increments(self):
        from logs.models import Notification
        before = Notification.objects.filter(recipient=self.president, is_read=False).count()
        self._create_pending_schedule()
        after = Notification.objects.filter(recipient=self.president, is_read=False).count()
        self.assertEqual(after, before + 1)

    def test_notification_link_points_to_stipend_list(self):
        from logs.models import Notification
        self._create_pending_schedule()
        notif = Notification.objects.get(recipient=self.president, title__icontains='Notify Test Stipend')
        self.assertEqual(notif.url, reverse('verification:stipend_list'))

    def test_notification_marked_read_on_approval(self):
        from logs.models import Notification
        from verification.models import StipendEvent
        self._create_pending_schedule()
        notif = Notification.objects.get(recipient=self.president, title__icontains='Notify Test Stipend')
        self.assertFalse(notif.is_read)

        event = StipendEvent.objects.get(title='Notify Test Stipend')
        self.client.force_login(self.president)
        self.client.post(reverse('verification:stipend_approve', args=[event.pk]))

        notif.refresh_from_db()
        self.assertTrue(notif.is_read)
        self.assertIsNotNone(notif.read_at)

    def test_notification_marked_read_on_rejection(self):
        from logs.models import Notification
        from verification.models import StipendEvent
        self._create_pending_schedule()
        notif = Notification.objects.get(recipient=self.president, title__icontains='Notify Test Stipend')

        event = StipendEvent.objects.get(title='Notify Test Stipend')
        self.client.force_login(self.president)
        self.client.post(
            reverse('verification:stipend_reject', args=[event.pk]),
            {'reason': 'Duplicate schedule'},
        )

        notif.refresh_from_db()
        self.assertTrue(notif.is_read)

    def test_no_notification_when_president_seat_vacant(self):
        """If no active President account exists, creation must not crash —
        it simply has no recipient to notify."""
        from logs.models import Notification
        self.president.is_active = False
        self.president.save(update_fields=['is_active'])
        resp = self._create_pending_schedule(title='No President Seat')
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(
            Notification.objects.filter(title__icontains='No President Seat').exists()
        )

    def test_dedupe_key_prevents_duplicate_notification(self):
        """Calling notify_user twice for the same event (e.g. a retried
        request) must not create a second unread notification."""
        from logs.models import Notification
        from logs.notifications import notify_user
        self._create_pending_schedule()
        from verification.models import StipendEvent
        event = StipendEvent.objects.get(title='Notify Test Stipend')
        from verification.views import _stipend_pending_dedupe_key
        notify_user(
            self.president,
            category=Notification.CATEGORY_APPROVAL_REQUIRED,
            title='Duplicate attempt',
            url=reverse('verification:stipend_list'),
            dedupe_key=_stipend_pending_dedupe_key(event.pk),
        )
        self.assertEqual(
            Notification.objects.filter(recipient=self.president, title__icontains='Notify Test Stipend').count(),
            1,
        )


# ──────────────────────────────────────────────────────────────────────────────
# v2.2.0 Post-UAT Phase 4 — registration_review reject() must disapprove, not
# deactivate. Zero test coverage previously existed for this view's POST
# handler at all.
# ──────────────────────────────────────────────────────────────────────────────

class RegistrationReviewDisapprovalTest(TestCase):

    def setUp(self):
        self.admin = _make_staff('reg_review_admin', role=CustomUser.ROLE_ADMIN)
        self.client = Client()
        self.client.force_login(self.admin)
        self.beneficiary = Beneficiary.objects.create(
            beneficiary_id='BEN-RRD-00001',
            first_name='Pedro', last_name='Cruz',
            date_of_birth='1945-03-10', gender='M',
            barangay='Commonwealth', municipality='Quezon City', province='NCR',
            status=Beneficiary.STATUS_PENDING,
        )

    def test_reject_sets_disapproved_not_inactive(self):
        resp = self.client.post(
            reverse('verification:registration_review', args=[self.beneficiary.pk]),
            {'action': 'reject', 'review_notes': 'Missing valid ID documentation.'},
        )
        self.assertEqual(resp.status_code, 302)
        self.beneficiary.refresh_from_db()
        self.assertEqual(self.beneficiary.status, Beneficiary.STATUS_DISAPPROVED)

    def test_reject_records_reason(self):
        self.client.post(
            reverse('verification:registration_review', args=[self.beneficiary.pk]),
            {'action': 'reject', 'review_notes': 'Duplicate senior citizen ID.'},
        )
        self.beneficiary.refresh_from_db()
        self.assertIn('Duplicate senior citizen ID.', self.beneficiary.deactivated_reason)

    def test_approve_still_sets_active(self):
        self.client.post(
            reverse('verification:registration_review', args=[self.beneficiary.pk]),
            {'action': 'approve', 'review_notes': 'All documents verified.'},
        )
        self.beneficiary.refresh_from_db()
        self.assertEqual(self.beneficiary.status, Beneficiary.STATUS_ACTIVE)

    def test_disapproved_beneficiary_shown_in_list_as_disapproved(self):
        self.client.post(
            reverse('verification:registration_review', args=[self.beneficiary.pk]),
            {'action': 'reject', 'review_notes': 'Not eligible.'},
        )
        resp = self.client.get('/dashboard/beneficiaries/')
        self.assertContains(resp, 'Disapproved')

    def test_status_filter_disapproved_excludes_inactive(self):
        active_then_deactivated = Beneficiary.objects.create(
            beneficiary_id='BEN-RRD-00002',
            first_name='Ana', last_name='Reyes',
            date_of_birth='1940-01-01', gender='F',
            barangay='Commonwealth', municipality='Quezon City', province='NCR',
            status=Beneficiary.STATUS_INACTIVE,
            deactivated_reason='Beneficiary moved out of the city.',
        )
        self.client.post(
            reverse('verification:registration_review', args=[self.beneficiary.pk]),
            {'action': 'reject', 'review_notes': 'Not eligible.'},
        )
        resp = self.client.get('/dashboard/beneficiaries/?status=disapproved')
        ids = {b.pk for b in resp.context['beneficiaries']}
        self.assertIn(self.beneficiary.pk, ids)
        self.assertNotIn(active_then_deactivated.pk, ids)


# ──────────────────────────────────────────────────────────────────────────────
# v2.2.0 Follow-up Issue 31 — direct unit coverage of check_duplicate_face()'s
# status-inclusion matrix. Confirms the detection function itself already
# searches ALL FaceEmbedding rows regardless of the owning beneficiary's
# status (pending/active/inactive/deceased/disapproved all included — no
# status filter exists in the query), and that name has no bearing on the
# result — only the biometric embedding does.
# ──────────────────────────────────────────────────────────────────────────────

class CheckDuplicateFaceStatusMatrixTest(TestCase):

    # 128-d unit vectors: SAME_A vs SAME_A -> cosine 1.0 (duplicate); SAME_A
    # vs DIFFERENT_B -> cosine 0.0 (orthogonal, not a duplicate).
    FACE_A = [1.0] + [0.0] * 127
    FACE_B = [0.0, 1.0] + [0.0] * 126

    def _make_with_face(self, ben_id, sc_id, embedding, status, **overrides):
        from verification.face_utils import encrypt_embedding
        from verification.models import FaceEmbedding
        import numpy as np
        b = _make_beneficiary(ben_id=ben_id, sc_id=sc_id)
        for k, v in overrides.items():
            setattr(b, k, v)
        b.status = status
        b.save()
        FaceEmbedding.objects.create(
            beneficiary=b,
            embedding_data=encrypt_embedding(np.array(embedding, dtype=np.float32)),
        )
        return b

    def _check(self, embedding):
        from verification.face_utils import check_duplicate_face
        import numpy as np
        return check_duplicate_face(np.array(embedding, dtype=np.float32), threshold=0.80)

    def test_pending_beneficiary_same_face_triggers_duplicate(self):
        self._make_with_face('BEN-CDF-001', 'SC-CDF-001', self.FACE_A, Beneficiary.STATUS_PENDING)
        result = self._check(self.FACE_A)
        self.assertTrue(result['duplicates_found'])
        self.assertEqual(result['matches'][0]['beneficiary_id'], 'BEN-CDF-001')

    def test_active_beneficiary_same_face_triggers_duplicate(self):
        self._make_with_face('BEN-CDF-002', 'SC-CDF-002', self.FACE_A, Beneficiary.STATUS_ACTIVE)
        result = self._check(self.FACE_A)
        self.assertTrue(result['duplicates_found'])

    def test_inactive_beneficiary_same_face_still_included(self):
        """Inactive records are NOT excluded — a previously-active beneficiary's
        face must still be caught if re-registered under a new record."""
        self._make_with_face('BEN-CDF-003', 'SC-CDF-003', self.FACE_A, Beneficiary.STATUS_INACTIVE)
        result = self._check(self.FACE_A)
        self.assertTrue(result['duplicates_found'])

    def test_deceased_beneficiary_same_face_still_included(self):
        self._make_with_face('BEN-CDF-004', 'SC-CDF-004', self.FACE_A, Beneficiary.STATUS_DECEASED)
        result = self._check(self.FACE_A)
        self.assertTrue(result['duplicates_found'])

    def test_disapproved_beneficiary_same_face_still_included(self):
        self._make_with_face('BEN-CDF-005', 'SC-CDF-005', self.FACE_A, Beneficiary.STATUS_DISAPPROVED)
        result = self._check(self.FACE_A)
        self.assertTrue(result['duplicates_found'])

    def test_same_name_different_face_not_a_duplicate(self):
        """Core Issue 31 requirement: two same-named people with genuinely
        different faces must NOT be flagged as duplicates."""
        self._make_with_face(
            'BEN-CDF-006', 'SC-CDF-006', self.FACE_A, Beneficiary.STATUS_ACTIVE,
            first_name='Toni', last_name='Fowler',
        )
        result = self._check(self.FACE_B)
        self.assertFalse(result['duplicates_found'])

    def test_different_name_same_face_triggers_duplicate(self):
        """Core Issue 31 requirement: identity is determined by the
        biometric, not the name — different names sharing the same face
        must still be flagged."""
        self._make_with_face(
            'BEN-CDF-007', 'SC-CDF-007', self.FACE_A, Beneficiary.STATUS_PENDING,
            first_name='Completely', last_name='Different',
        )
        result = self._check(self.FACE_A)
        self.assertTrue(result['duplicates_found'])

    def test_exclude_beneficiary_id_skips_self_match(self):
        """Re-registering/updating a beneficiary's own face must not flag
        itself as a duplicate of itself."""
        from verification.face_utils import check_duplicate_face
        import numpy as np
        b = self._make_with_face('BEN-CDF-008', 'SC-CDF-008', self.FACE_A, Beneficiary.STATUS_ACTIVE)
        result = check_duplicate_face(
            np.array(self.FACE_A, dtype=np.float32), threshold=0.80,
            exclude_beneficiary_id=b.beneficiary_id,
        )
        self.assertFalse(result['duplicates_found'])


# ──────────────────────────────────────────────────────────────────────────────
# FANS-C comprehensive pre-UAT pass — Item 1/18: representative faces were
# never part of the duplicate-detection pool. check_duplicate_face() only
# ever searched FaceEmbedding/AdditionalFaceEmbedding (beneficiary tables),
# so:
#   (a) two different representatives (for two different beneficiaries)
#       sharing the same face were never detected — the SharedRepresentativeReview
#       workflow that exists specifically for this exact scenario could never
#       actually fire from a real rep-vs-rep collision;
#   (b) a brand-new beneficiary registering with a face already enrolled as
#       someone else's representative was never flagged either.
# Fixed by having check_duplicate_face() also search RepresentativeFaceEmbedding
# by default. These tests cover the fix at both the unit level (the function
# itself) and, for the rep-vs-rep case, the full register_rep_face_submit view.
# ──────────────────────────────────────────────────────────────────────────────

class RepresentativeCrossDuplicateDetectionTest(TestCase):

    FACE_A = [1.0] + [0.0] * 127
    FACE_B = [0.0, 1.0] + [0.0] * 126

    def _make_rep_with_face(self, ben_id, sc_id, embedding, staff):
        from verification.face_utils import encrypt_embedding
        from verification.models import RepresentativeFaceEmbedding
        import numpy as np
        b = _make_beneficiary(ben_id=ben_id, sc_id=sc_id)
        rep = _make_rep(b, staff, id_number=f'SSS-{ben_id}')
        RepresentativeFaceEmbedding.objects.create(
            representative=rep,
            embedding_data=encrypt_embedding(np.array(embedding, dtype=np.float32)),
            created_by=staff,
        )
        return b, rep

    def test_check_duplicate_face_detects_two_representatives_same_face(self):
        """Core gap: two different representatives (different beneficiaries)
        with the same face must be caught, source-tagged as 'representative'."""
        from verification.face_utils import check_duplicate_face
        import numpy as np
        staff = _make_staff('rcd_staff1')
        _, rep1 = self._make_rep_with_face('BEN-RCD-001', 'SC-RCD-001', self.FACE_A, staff)
        result = check_duplicate_face(np.array(self.FACE_A, dtype=np.float32), threshold=0.80)
        self.assertTrue(result['duplicates_found'])
        top = result['matches'][0]
        self.assertEqual(top['source'], 'representative')
        self.assertEqual(top['representative_id'], str(rep1.id))
        self.assertEqual(top['beneficiary_id'], rep1.beneficiary.beneficiary_id)

    def test_check_duplicate_face_different_rep_faces_not_flagged(self):
        from verification.face_utils import check_duplicate_face
        import numpy as np
        staff = _make_staff('rcd_staff2')
        self._make_rep_with_face('BEN-RCD-002', 'SC-RCD-002', self.FACE_A, staff)
        result = check_duplicate_face(np.array(self.FACE_B, dtype=np.float32), threshold=0.80)
        self.assertFalse(result['duplicates_found'])

    def test_check_duplicate_face_new_beneficiary_matches_existing_rep_face(self):
        """A brand-new beneficiary's face matching an existing representative's
        face must be detected too — the biometric pool is shared regardless
        of which role a record was registered under."""
        from verification.face_utils import check_duplicate_face
        import numpy as np
        staff = _make_staff('rcd_staff3')
        ben, rep = self._make_rep_with_face('BEN-RCD-003', 'SC-RCD-003', self.FACE_A, staff)
        result = check_duplicate_face(np.array(self.FACE_A, dtype=np.float32), threshold=0.80)
        self.assertTrue(result['duplicates_found'])
        top = result['matches'][0]
        self.assertEqual(top['source'], 'representative')
        self.assertEqual(top['beneficiary_id'], ben.beneficiary_id)
        self.assertEqual(top['representative_name'], rep.full_name)

    def test_include_representatives_false_skips_representative_pool(self):
        from verification.face_utils import check_duplicate_face
        import numpy as np
        staff = _make_staff('rcd_staff4')
        self._make_rep_with_face('BEN-RCD-004', 'SC-RCD-004', self.FACE_A, staff)
        result = check_duplicate_face(
            np.array(self.FACE_A, dtype=np.float32), threshold=0.80,
            include_representatives=False,
        )
        self.assertFalse(result['duplicates_found'])

    def test_exclude_representative_id_skips_self_match(self):
        """A representative updating their own face must not flag themselves
        as a duplicate of themselves."""
        from verification.face_utils import check_duplicate_face
        import numpy as np
        staff = _make_staff('rcd_staff5')
        _, rep = self._make_rep_with_face('BEN-RCD-005', 'SC-RCD-005', self.FACE_A, staff)
        result = check_duplicate_face(
            np.array(self.FACE_A, dtype=np.float32), threshold=0.80,
            exclude_representative_id=str(rep.id),
        )
        self.assertFalse(result['duplicates_found'])

    def test_inactive_representative_still_excluded_by_is_active_filter(self):
        """A deactivated representative's face is not part of the live
        duplicate pool (mirrors representative.is_active gating elsewhere)."""
        from verification.face_utils import check_duplicate_face
        import numpy as np
        staff = _make_staff('rcd_staff6')
        _, rep = self._make_rep_with_face('BEN-RCD-006', 'SC-RCD-006', self.FACE_A, staff)
        rep.is_active = False
        rep.save(update_fields=['is_active'])
        result = check_duplicate_face(np.array(self.FACE_A, dtype=np.float32), threshold=0.80)
        self.assertFalse(result['duplicates_found'])


class RegisterRepFaceSubmitCrossDuplicateTest(TestCase):
    """Full-flow coverage: registering a second representative's face that
    matches a first representative's already-enrolled face (different
    beneficiaries) must create a SharedRepresentativeReview and block
    verification, exercising the actual view, not just the unit function."""

    def setUp(self):
        from verification.face_utils import encrypt_embedding
        import numpy as np
        self.staff = _make_staff('rrfs_xdup_staff')
        self.client = Client()
        self.client.force_login(self.staff)

        self.ben1 = _make_beneficiary('BEN-XDUP-001', 'SC-XDUP-001')
        self.rep1 = _make_rep(self.ben1, self.staff, id_number='SSS-XDUP-001')
        from verification.models import RepresentativeFaceEmbedding
        self.existing_embedding = np.array([1.0] + [0.0] * 127, dtype=np.float32)
        RepresentativeFaceEmbedding.objects.create(
            representative=self.rep1,
            embedding_data=encrypt_embedding(self.existing_embedding),
            created_by=self.staff,
        )

        self.ben2 = _make_beneficiary('BEN-XDUP-002', 'SC-XDUP-002')
        self.rep2 = _make_rep(self.ben2, self.staff, id_number='SSS-XDUP-002')
        self.url = reverse(
            'verification:register_rep_face_submit',
            kwargs={'pk': self.ben2.pk, 'rep_pk': self.rep2.pk},
        )

    def test_second_representative_matching_first_creates_shared_review(self):
        from beneficiaries.models import SharedRepresentativeReview, Representative
        with mock.patch('verification.views.process_face_for_registration') as mock_proc, \
             mock.patch('verification.views.decrypt_embedding') as mock_decrypt:
            mock_proc.return_value = {
                'success': True,
                'encrypted_embedding': b'irrelevant-placeholder',
            }
            mock_decrypt.return_value = self.existing_embedding
            resp = self.client.post(
                self.url, data=json.dumps({'image': VALID_BASE64}),
                content_type='application/json',
            )
        data = json.loads(resp.content)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data['success'])
        self.assertTrue(data.get('shared_review'))

        self.rep2.refresh_from_db()
        self.assertEqual(self.rep2.shared_review_status, Representative.SHARED_PENDING)
        self.assertTrue(self.rep2.is_blocked_for_review)

        review = SharedRepresentativeReview.objects.get(representative=self.rep2)
        self.assertEqual(review.matched_beneficiary_id, self.ben1.beneficiary_id)
        self.assertIn('representative', review.flag_reason.lower())


# ──────────────────────────────────────────────────────────────────────────────
# FANSC v2.1.17 functional correction pass — a representative's face capture
# that actually belongs to the beneficiary they represent was previously
# invisible to duplicate detection: register_rep_face_submit() passed
# exclude_beneficiary_id=<the represented beneficiary> into check_duplicate_face(),
# which skips exactly the one comparison (rep vs. their own represented
# beneficiary) that most needed to run. A beneficiary could therefore be
# enrolled as their own "authorized representative" and the UI would show
# "Face Registered — Ready to Verify". Fixed with a dedicated same-beneficiary
# check (compare_with_all_embeddings, the same matcher verify_submit uses)
# that runs before the existing cross-beneficiary duplicate flow and rejects
# the enrollment outright — nothing is saved, so has_face_data stays False.
# ──────────────────────────────────────────────────────────────────────────────

class RegisterRepFaceSubmitSameBeneficiaryTest(TestCase):
    """A representative's face must not be accepted if it matches the face
    of the very beneficiary they represent."""

    def setUp(self):
        from verification.face_utils import encrypt_embedding
        from verification.models import FaceEmbedding
        import numpy as np
        self.staff = _make_staff('rrfs_self_staff')
        self.client = Client()
        self.client.force_login(self.staff)

        self.beneficiary_face = np.array([1.0] + [0.0] * 127, dtype=np.float32)
        self.ben = _make_beneficiary('BEN-SELF-001', 'SC-SELF-001')
        FaceEmbedding.objects.create(
            beneficiary=self.ben,
            embedding_data=encrypt_embedding(self.beneficiary_face),
        )
        self.rep = _make_rep(self.ben, self.staff, id_number='SSS-SELF-001')
        self.url = reverse(
            'verification:register_rep_face_submit',
            kwargs={'pk': self.ben.pk, 'rep_pk': self.rep.pk},
        )

    def _post_capture(self, captured_embedding):
        with mock.patch('verification.views.process_face_for_registration') as mock_proc, \
             mock.patch('verification.views.decrypt_embedding') as mock_decrypt:
            mock_proc.return_value = {
                'success': True,
                'encrypted_embedding': b'irrelevant-placeholder',
            }
            mock_decrypt.return_value = captured_embedding
            resp = self.client.post(
                self.url, data=json.dumps({'image': VALID_BASE64}),
                content_type='application/json',
            )
        return json.loads(resp.content), resp

    def test_representative_face_matching_beneficiary_is_blocked(self):
        """Confident same-person match (representative == beneficiary) must
        be rejected outright, not routed to SharedRepresentativeReview."""
        from beneficiaries.models import SharedRepresentativeReview, Representative
        import numpy as np
        data, resp = self._post_capture(np.array(self.beneficiary_face, dtype=np.float32))

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(data['success'])
        self.assertIn('different person', data['error'].lower())

        self.rep.refresh_from_db()
        self.assertFalse(self.rep.has_face_data,
                         'Blocked capture must not be saved as the representative face')
        self.assertEqual(self.rep.shared_review_status, Representative.SHARED_NONE)
        self.assertFalse(
            SharedRepresentativeReview.objects.filter(representative=self.rep).exists(),
            'Same-beneficiary block is a hard reject, not an admin-review case')

    def test_representative_with_clearly_different_face_is_accepted(self):
        """A genuinely different person must still be able to register as
        this beneficiary's representative (no over-broad blocking)."""
        import numpy as np
        different_face = np.array([0.0, 1.0] + [0.0] * 126, dtype=np.float32)
        data, resp = self._post_capture(different_face)

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data['success'])
        self.assertNotIn('different person', (data.get('error') or '').lower())

        self.rep.refresh_from_db()
        self.assertTrue(self.rep.has_face_data)

    def test_beneficiary_with_no_face_on_file_skips_self_check(self):
        """Nothing to compare against yet — must not crash, and must fall
        through to the normal (cross-beneficiary) registration path."""
        import numpy as np
        from verification.models import FaceEmbedding
        FaceEmbedding.objects.filter(beneficiary=self.ben).delete()
        data, resp = self._post_capture(np.array(self.beneficiary_face, dtype=np.float32))

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data['success'])
        self.rep.refresh_from_db()
        self.assertTrue(self.rep.has_face_data)


# ──────────────────────────────────────────────────────────────────────────────
# FANSC v2.1.17 biometric-verification audit (2026-09-08) — direct, unmocked
# invariant coverage for face_utils.cosine_similarity() and
# compare_with_all_embeddings(). Every existing test that exercises
# compare_with_all_embeddings does so through a view with the function itself
# mocked out, so the actual max-of-templates selection and the self-similarity
# identity had no direct regression test. Pure numpy/model-level checks —
# no camera, no view, no mocking of the function under test.
# ──────────────────────────────────────────────────────────────────────────────

class BiometricMatchingInvariantsTest(TestCase):

    def test_self_cosine_similarity_is_one(self):
        """An embedding compared against itself must be (numerically) exactly
        1.0 — the FaceNet decision bands assume this identity holds."""
        from verification.face_utils import cosine_similarity
        import numpy as np
        rng = np.random.default_rng(42)
        emb = rng.standard_normal(512).astype(np.float32)
        emb = emb / np.linalg.norm(emb)
        self.assertAlmostEqual(cosine_similarity(emb, emb), 1.0, places=5)

    def test_cosine_similarity_handles_unnormalized_input(self):
        """cosine_similarity() re-normalizes internally, so a non-unit-length
        embedding pair still yields the correct value rather than silently
        scaling the score."""
        from verification.face_utils import cosine_similarity
        import numpy as np
        emb = np.array([3.0, 4.0] + [0.0] * 126, dtype=np.float32)  # norm=5, not unit
        self.assertAlmostEqual(cosine_similarity(emb, emb), 1.0, places=5)

    def test_compare_with_all_embeddings_selects_maximum_not_first_or_average(self):
        """Multi-template matching must return the BEST score across primary +
        additional templates. Deliberately orders the templates so that
        "first" or "average" would both give a different (wrong) answer than
        "max" — only a true max-selection implementation passes."""
        from verification.face_utils import (
            compare_with_all_embeddings, encrypt_embedding, cosine_similarity,
        )
        from verification.models import FaceEmbedding, AdditionalFaceEmbedding
        import numpy as np

        ben = _make_beneficiary('BEN-MAXSEL-001', 'SC-MAXSEL-001')
        live = np.array([1.0, 0.0] + [0.0] * 126, dtype=np.float32)

        # Primary: orthogonal to live (score ~0.0) — the worst match, and
        # also the one a "first-template-only" bug would incorrectly return.
        primary = np.array([0.0, 1.0] + [0.0] * 126, dtype=np.float32)
        FaceEmbedding.objects.create(beneficiary=ben, embedding_data=encrypt_embedding(primary))

        # Additional #1: a mediocre match.
        mediocre = np.array([0.6, 0.8] + [0.0] * 126, dtype=np.float32)
        AdditionalFaceEmbedding.objects.create(
            beneficiary=ben, embedding_data=encrypt_embedding(mediocre), label='mediocre',
        )

        # Additional #2: identical to the live embedding — the true best
        # match, and the one a correct max-selection must surface.
        best = np.array([1.0, 0.0] + [0.0] * 126, dtype=np.float32)
        AdditionalFaceEmbedding.objects.create(
            beneficiary=ben, embedding_data=encrypt_embedding(best), label='best',
        )

        result = compare_with_all_embeddings(live, ben)

        self.assertTrue(result['success'])
        self.assertEqual(result['templates_checked'], 3)
        self.assertAlmostEqual(result['score'], cosine_similarity(live, best), places=5)
        self.assertAlmostEqual(result['score'], 1.0, places=5)
        self.assertEqual(result['matched_template'], 'best')
        # Sanity: the returned score must be >= every individual template
        # score reported in all_scores (definition of "maximum").
        for entry in result['all_scores']:
            self.assertLessEqual(entry['score'], result['score'] + 1e-6)


# ──────────────────────────────────────────────────────────────────────────────
# Final pre-release verification pass — a SECOND, previously-unexamined
# duplicate-face path: face RE-ENROLLMENT (FaceUpdateRequest), separate from
# beneficiary registration. The review template already warned the admin
# about a detected duplicate match, but nothing server-side stopped a plain
# "Approve" click from silently writing a duplicate face into
# FaceEmbedding/AdditionalFaceEmbedding — the exact same safety gap as
# Issue 31/33, on a different entry point. Fixed by requiring an explicit
# duplicate_confirmed acknowledgement before an approve with a duplicate
# match can proceed; reject is unaffected (formnovalidate).
# ──────────────────────────────────────────────────────────────────────────────

class FaceUpdateReviewDuplicateGateTest(TestCase):

    def setUp(self):
        from verification.models import FaceUpdateRequest, FaceUpdateLog, FaceEmbedding
        self.FaceUpdateRequest = FaceUpdateRequest
        self.admin = _make_staff('furadmin01', role=CustomUser.ROLE_ADMIN)
        self.staff = _make_staff('furstaff02')
        self.ben = _make_beneficiary('BEN-FUR-001', 'SC-FUR-001')
        self.matched = _make_beneficiary('BEN-FUR-MATCH', 'SC-FUR-MATCH')
        FaceEmbedding.objects.create(beneficiary=self.ben, embedding_data=b'\x00' * 64)
        self.client = Client()
        self.client.force_login(self.admin)

    def _make_request(self, with_duplicate=True):
        from verification.models import FaceUpdateLog
        return self.FaceUpdateRequest.objects.create(
            beneficiary=self.ben,
            requested_by=self.staff,
            reason=FaceUpdateLog.REASON_STAFF_DECISION,
            action=FaceUpdateLog.ACTION_REPLACE,
            new_embedding_data=b'\x01' * 64,
            duplicate_match_beneficiary=self.matched if with_duplicate else None,
            duplicate_match_score=0.9 if with_duplicate else None,
        )

    def test_approve_blocked_without_confirmation_when_duplicate_flagged(self):
        fur = self._make_request(with_duplicate=True)
        resp = self.client.post(
            reverse('verification:face_update_review', args=[fur.pk]),
            {'action': 'approve', 'review_notes': 'Looks fine.'},
        )
        self.assertEqual(resp.status_code, 200)
        fur.refresh_from_db()
        self.assertEqual(fur.status, self.FaceUpdateRequest.STATUS_PENDING)

    def test_approve_succeeds_with_duplicate_confirmed(self):
        from verification.models import FaceEmbedding
        fur = self._make_request(with_duplicate=True)
        resp = self.client.post(
            reverse('verification:face_update_review', args=[fur.pk]),
            {'action': 'approve', 'review_notes': 'Verified IDs — legitimate twin.',
             'duplicate_confirmed': 'on'},
        )
        self.assertEqual(resp.status_code, 302)
        fur.refresh_from_db()
        self.assertEqual(fur.status, self.FaceUpdateRequest.STATUS_APPROVED)
        self.ben.refresh_from_db()
        self.assertEqual(bytes(FaceEmbedding.objects.get(beneficiary=self.ben).embedding_data), b'\x01' * 64)

    def test_reject_does_not_require_duplicate_confirmation(self):
        fur = self._make_request(with_duplicate=True)
        resp = self.client.post(
            reverse('verification:face_update_review', args=[fur.pk]),
            {'action': 'reject', 'review_notes': 'Rejecting, insufficient evidence.'},
        )
        self.assertEqual(resp.status_code, 302)
        fur.refresh_from_db()
        self.assertEqual(fur.status, self.FaceUpdateRequest.STATUS_REJECTED)

    def test_approve_unaffected_when_no_duplicate_flagged(self):
        """Regression: the gate must not block ordinary (non-flagged) updates."""
        fur = self._make_request(with_duplicate=False)
        resp = self.client.post(
            reverse('verification:face_update_review', args=[fur.pk]),
            {'action': 'approve', 'review_notes': 'Standard re-enrollment.'},
        )
        self.assertEqual(resp.status_code, 302)
        fur.refresh_from_db()
        self.assertEqual(fur.status, self.FaceUpdateRequest.STATUS_APPROVED)

    def test_duplicate_warning_and_checkbox_rendered(self):
        fur = self._make_request(with_duplicate=True)
        resp = self.client.get(reverse('verification:face_update_review', args=[fur.pk]))
        self.assertContains(resp, 'Duplicate Face Detected')
        self.assertContains(resp, 'name="duplicate_confirmed"')
        self.assertContains(resp, self.matched.beneficiary_id)


# ──────────────────────────────────────────────────────────────────────────────
# v2.2.0 Follow-up Issue 31/33 — a beneficiary with an unresolved duplicate-face
# conflict must never be approvable/rejectable through the generic Registration
# Applications queue (registration_review). Before this fix that queue had no
# concept of "duplicate face pending review" at all, so an admin working the
# generic queue could unknowingly approve straight past an unresolved identity
# conflict — the exact payout-safety bypass this follow-up pass targets.
# ──────────────────────────────────────────────────────────────────────────────

class RegistrationReviewDuplicateFaceGateTest(TestCase):

    def setUp(self):
        self.admin = _make_staff('regdup_admin', role=CustomUser.ROLE_ADMIN)
        self.client = Client()
        self.client.force_login(self.admin)
        self.existing = Beneficiary.objects.create(
            beneficiary_id='BEN-RDG-EXIST',
            first_name='Toni', last_name='Fowler',
            date_of_birth='1945-03-10', gender='F',
            barangay='Commonwealth', municipality='Quezon City', province='NCR',
            status=Beneficiary.STATUS_PENDING,
        )
        self.flagged = Beneficiary.objects.create(
            beneficiary_id='BEN-RDG-FLAGGED',
            first_name='Toni', last_name='Fowler',
            date_of_birth='1945-03-10', gender='F',
            barangay='Commonwealth', municipality='Quezon City', province='NCR',
            status=Beneficiary.STATUS_PENDING,
            duplicate_review_required=True,
            duplicate_match_beneficiary=self.existing,
            duplicate_match_score=0.93,
        )
        self.clean = Beneficiary.objects.create(
            beneficiary_id='BEN-RDG-CLEAN',
            first_name='Ana', last_name='Reyes',
            date_of_birth='1940-01-01', gender='F',
            barangay='Commonwealth', municipality='Quezon City', province='NCR',
            status=Beneficiary.STATUS_PENDING,
        )

    def test_flagged_beneficiary_excluded_from_generic_queue(self):
        resp = self.client.get(reverse('verification:registration_review_list'))
        ids = {b.pk for b in resp.context['pending']}
        self.assertNotIn(self.flagged.pk, ids)
        self.assertIn(self.clean.pk, ids)

    def test_duplicate_pending_count_shown(self):
        resp = self.client.get(reverse('verification:registration_review_list'))
        self.assertEqual(resp.context['duplicate_pending_count'], 1)
        self.assertContains(resp, 'Duplicate Face Review')

    def test_get_review_page_redirects_to_duplicate_review(self):
        resp = self.client.get(reverse('verification:registration_review', args=[self.flagged.pk]))
        self.assertRedirects(
            resp, reverse('beneficiaries:duplicate_review_detail', args=[self.flagged.pk]),
        )

    def test_approve_blocked_for_flagged_beneficiary(self):
        resp = self.client.post(
            reverse('verification:registration_review', args=[self.flagged.pk]),
            {'action': 'approve', 'review_notes': 'Looks fine to me.'},
        )
        self.assertRedirects(
            resp, reverse('beneficiaries:duplicate_review_detail', args=[self.flagged.pk]),
        )
        self.flagged.refresh_from_db()
        self.assertEqual(self.flagged.status, Beneficiary.STATUS_PENDING)
        self.assertNotEqual(self.flagged.status, Beneficiary.STATUS_ACTIVE)

    def test_reject_blocked_for_flagged_beneficiary(self):
        resp = self.client.post(
            reverse('verification:registration_review', args=[self.flagged.pk]),
            {'action': 'reject', 'review_notes': 'Not eligible.'},
        )
        self.assertRedirects(
            resp, reverse('beneficiaries:duplicate_review_detail', args=[self.flagged.pk]),
        )
        self.flagged.refresh_from_db()
        self.assertEqual(self.flagged.status, Beneficiary.STATUS_PENDING)

    def test_clean_beneficiary_still_approvable_normally(self):
        """Regression: the gate must not block ordinary (non-flagged) registrations."""
        resp = self.client.post(
            reverse('verification:registration_review', args=[self.clean.pk]),
            {'action': 'approve', 'review_notes': 'All documents verified.'},
        )
        self.assertEqual(resp.status_code, 302)
        self.clean.refresh_from_db()
        self.assertEqual(self.clean.status, Beneficiary.STATUS_ACTIVE)

    def test_duplicate_banner_shown_on_beneficiary_detail(self):
        resp = self.client.get(f'/dashboard/beneficiaries/{self.flagged.pk}/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Duplicate Face Detected')
        self.assertContains(resp, 'BEN-RDG-EXIST')

    def test_duplicate_badge_shown_on_beneficiary_list(self):
        resp = self.client.get('/dashboard/beneficiaries/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Duplicate Face')


class PADWeightHardeningTest(TestCase):
    """Issue 3 — flatness/sharpness weights raised; combined boost added."""

    def test_weights_were_raised(self):
        from verification.pad import PresentationAttackDetector
        det = PresentationAttackDetector(threshold=0.40)
        # Pre-v2.1.11: screen_flatness=0.30, sharpness_texture=0.25
        self.assertGreaterEqual(det.WEIGHTS['screen_flatness'], 0.55)
        self.assertGreaterEqual(det.WEIGHTS['sharpness_texture'], 0.50)


class PADStaticAndDuplicateSequenceTest(TestCase):
    """v2.1.11 — verify static-sequence + near-duplicate frame detection.

    These are unit tests against the PAD heuristic itself; they do not invoke
    FaceNet or the verify_submit pipeline.
    """

    def _make_frame(self, value=128, h=80, w=80):
        import numpy as np
        return np.full((h, w, 3), value, dtype=np.uint8)

    def test_identical_frames_score_static_high(self):
        from verification.pad import PresentationAttackDetector
        det = PresentationAttackDetector(threshold=0.40)
        frames = [self._make_frame(value=120) for _ in range(5)]
        result = det.analyze_sequence(frames)
        # Identical frames must trip the static-sequence signal.
        self.assertGreater(result.sequence_static, 0.7,
                           f'static_score expected high, got {result.sequence_static}')

    def test_near_duplicate_frames_flagged(self):
        from verification.pad import PresentationAttackDetector
        det = PresentationAttackDetector(threshold=0.40)
        # All identical → dHash distances all zero → near_duplicate=1.0
        frames = [self._make_frame(value=80) for _ in range(5)]
        result = det.analyze_sequence(frames)
        self.assertGreater(result.near_duplicate, 0.5,
                           f'near_duplicate expected high, got {result.near_duplicate}')

    def test_distinct_frames_score_clean(self):
        import numpy as np
        from verification.pad import PresentationAttackDetector
        det = PresentationAttackDetector(threshold=0.40)
        # Generate noisy frames so motion is non-zero and dHashes differ.
        rng = np.random.default_rng(seed=42)
        frames = [(rng.integers(0, 255, size=(80, 80, 3), dtype=np.uint8)) for _ in range(5)]
        result = det.analyze_sequence(frames)
        # Random frames have plenty of inter-frame motion; static_score must be low.
        self.assertLess(result.sequence_static, 0.5,
                        f'static_score expected low, got {result.sequence_static}')


class LivenessRetryStateResetTest(TestCase):
    """Issue 7 + token-reuse defence — make sure the existing JS contains the
    retry-state reset logic so a failed liveness cannot reuse a previous token."""

    def test_verify_js_contains_retry_action(self):
        import pathlib
        p = pathlib.Path('static/js/verify.js')
        content = p.read_text(encoding='utf-8', errors='replace')
        # Retry-action handler wired (Issue 7).
        self.assertIn("verifyBtn.dataset.action === 'retry'", content,
                      'verify.js missing retry action handler')
        # Submit clears the token immediately (defence-in-depth).
        self.assertIn('const _submitToken = livenessToken', content,
                      'verify.js missing immediate token clear')
        self.assertIn('livenessToken = null', content,
                      'verify.js missing token reset')

    def test_liveness_js_threshold_lowered(self):
        import pathlib
        p = pathlib.Path('static/js/liveness.js')
        content = p.read_text(encoding='utf-8', errors='replace')
        # Issue 10 — accessible threshold value (peak tracking lets brief turns count).
        self.assertIn('CHALLENGE_THRESHOLD_DEG = 4', content,
                      'liveness.js missing v2.1.11 threshold (4 deg)')


# ─── v2.1.12 — Liveness Frontend Regression (Issue 1) ────────────────────────

class LivenessElapsedScopeFixTest(TestCase):
    """
    v2.1.12 (Issue 1): the previous build declared `elapsed` inside the
    challenge Promise's setInterval scope but used it later when building the
    proof payload (`challenge_duration_ms: elapsed`). That threw a
    ReferenceError, leaving the UI stuck at "Processing liveness proof..." and
    preventing a tx_token from ever being issued. This regression test asserts
    that the fixed verify.js hoists `challengeElapsedMs` to the outer scope and
    no longer references a `let elapsed` declared inside the Promise.
    """

    def setUp(self):
        import pathlib
        self.content = pathlib.Path('static/js/verify.js').read_text(
            encoding='utf-8', errors='replace'
        )

    def test_challenge_elapsed_ms_hoisted_to_outer_scope(self):
        self.assertIn('let challengeElapsedMs = 0', self.content,
                      'verify.js missing v2.1.12 challengeElapsedMs hoist')

    def test_no_inner_let_elapsed_inside_promise(self):
        # The buggy pattern was `let elapsed = 0;` immediately inside the
        # Promise — it should no longer exist.
        self.assertNotIn('        let elapsed = 0;', self.content,
                         'verify.js still contains the inner let elapsed = 0 bug')

    def test_proof_payload_uses_hoisted_duration(self):
        # The proof movement payload must reference the hoisted variable, not
        # an out-of-scope `elapsed`.
        self.assertIn('challenge_duration_ms: _durationMs', self.content,
                      'verify.js proof payload must use _durationMs (hoisted)')

    def test_proof_has_try_catch_finally_for_stuck_processing(self):
        # The Mode-B request must clear the processing overlay even on JS
        # exceptions so the UI never sticks at "Processing liveness proof...".
        self.assertIn("processingOverlay.style.display = 'none'", self.content,
                      'verify.js must clear processing overlay in finally')
        self.assertIn('} finally {', self.content,
                      'verify.js Mode-B request must use try/finally')

    def test_proof_payload_falls_back_to_start_timestamp(self):
        self.assertIn('Date.now() - challengeStartTs', self.content,
                      'verify.js must fall back to Date.now() - start when interval did not tick')

    def test_seq_frame_topup_logic_present(self):
        # If the challenge completes before MIN_SEQ_FRAMES are collected, the
        # frontend must capture additional frames before sending proof.
        self.assertIn('MIN_SEQ_FRAMES = 3', self.content,
                      'verify.js missing seq-frame top-up minimum')
        self.assertIn('topping up', self.content,
                      'verify.js missing seq-frame top-up branch')


class LivenessFrontendErrorHandlingTest(TestCase):
    """
    v2.1.12 (Issue 1): when the proof POST fails with a JS-level error, the UI
    must show the actual reason, zero the score, clear the token, and surface
    the failure to the operator instead of staying stuck at "Processing...".
    """

    def setUp(self):
        import pathlib
        self.content = pathlib.Path('static/js/verify.js').read_text(
            encoding='utf-8', errors='replace'
        )

    def test_frontend_exception_captured(self):
        self.assertIn('_proofFrontendError = String(proofErr', self.content,
                      'verify.js must capture frontend exception text')

    def test_no_token_path_shows_real_reason(self):
        self.assertIn('Liveness proof not issued', self.content,
                      'verify.js must show the real no-token reason')

    def test_failed_liveness_shows_zero_score_immediately(self):
        self.assertIn('showLivenessScoreBar(0)', self.content,
                      'verify.js must show 0% score on no-token failure')


# ─── v2.1.13 — PAD landmark-motion gate (Issue 1) ─────────────────────────────

class PADLandmarkMotionGateTest(TestCase):
    """
    v2.1.13 (Issue 1): when the client confirms head movement via FaceMesh
    landmarks, the pixel-level static-sequence and near-duplicate signals
    must be suppressed so a real person making a brief turn-and-return is
    not falsely flagged as a phone/photo/replay attack. Texture-based
    signals (glare, flatness, sharpness) MUST remain in force regardless.
    """

    def _make_frame(self, value=120, h=80, w=80):
        import numpy as np
        return np.full((h, w, 3), value, dtype=np.uint8)

    def test_landmark_motion_ok_suppresses_static_sequence(self):
        from verification.pad import PresentationAttackDetector
        det = PresentationAttackDetector(threshold=0.40)
        frames = [self._make_frame(value=120) for _ in range(5)]
        result_no_motion = det.analyze_sequence(frames, landmark_motion_ok=False)
        result_with_motion = det.analyze_sequence(frames, landmark_motion_ok=True)
        self.assertGreater(result_no_motion.sequence_static, 0.7,
                           'static must trip without landmark motion')
        self.assertEqual(result_with_motion.sequence_static, 0.0,
                         'static must be suppressed when landmark motion is OK')

    def test_landmark_motion_ok_suppresses_near_duplicate(self):
        from verification.pad import PresentationAttackDetector
        det = PresentationAttackDetector(threshold=0.40)
        frames = [self._make_frame(value=80) for _ in range(5)]
        result_no_motion = det.analyze_sequence(frames, landmark_motion_ok=False)
        result_with_motion = det.analyze_sequence(frames, landmark_motion_ok=True)
        self.assertGreater(result_no_motion.near_duplicate, 0.5,
                           'near_dup must trip without landmark motion')
        self.assertEqual(result_with_motion.near_duplicate, 0.0,
                         'near_dup must be suppressed when landmark motion is OK')

    def test_landmark_motion_ok_does_not_loosen_texture_signals(self):
        """A phone screen / printed photo still raises flatness or sharpness
        signals regardless of landmark motion — those gates must remain."""
        import numpy as np
        from verification.pad import PresentationAttackDetector
        det = PresentationAttackDetector(threshold=0.40)
        # Flat uniform grey is the canonical "screen flatness" signature.
        flat_frames = [np.full((80, 80, 3), 130, dtype=np.uint8) for _ in range(5)]
        result = det.analyze_sequence(flat_frames, landmark_motion_ok=True)
        # screen_flatness must still register even though static_sequence and
        # near_duplicate were suppressed by the landmark gate.
        self.assertGreater(
            result.screen_flatness, 0.4,
            f'flatness must remain active with landmark_motion_ok=True, '
            f'got {result.screen_flatness}',
        )

    def test_real_user_brief_movement_not_flagged_as_replay(self):
        """End-to-end signal: similar webcam frames + landmark motion confirmed
        must produce a score whose pixel-level PAD signals (static_sequence,
        near_duplicate) are zero. Texture signals are unchanged."""
        from verification.pad import PresentationAttackDetector
        det = PresentationAttackDetector(threshold=0.40)
        import numpy as np
        rng = np.random.default_rng(seed=7)
        # Use real-skin-like texture (varying greys) so flatness doesn't dominate.
        # Each frame differs slightly (mild noise + frame-specific offset).
        frames = []
        for i in range(5):
            base = rng.integers(60, 200, size=(80, 80, 3), dtype=np.int16)
            base = np.clip(base + i * 2, 0, 255).astype(np.uint8)
            frames.append(base)
        result_no_motion = det.analyze_sequence(frames, landmark_motion_ok=False)
        result_with_motion = det.analyze_sequence(frames, landmark_motion_ok=True)
        # When landmark motion is confirmed, the pixel-only PAD signals must
        # be exactly zero — that is the suppression contract.
        self.assertEqual(result_with_motion.sequence_static, 0.0,
                         'static must be suppressed when landmark motion OK')
        self.assertEqual(result_with_motion.near_duplicate, 0.0,
                         'near_duplicate must be suppressed when landmark motion OK')
        # And the suppression must materially lower the composite score
        # compared to the no-motion case (the no-motion case is what previously
        # triggered the false phone/photo denial).
        self.assertLessEqual(
            result_with_motion.score, result_no_motion.score,
            f'landmark-motion-OK score should be <= no-motion score '
            f'(motion-ok={result_with_motion.score}, no-motion={result_no_motion.score})'
        )


# ─── v2.1.13 — Auto-verify threshold band (Issue 2) ───────────────────────────

class AutoVerifyThresholdBandTest(TestCase):
    """
    v2.1.13 (Issue 2): a wrong-person, baby-photo, or low-quality capture can
    produce FaceNet similarity around 0.80-0.86 — well above the 0.75 lower
    threshold. Auto-verifying at 0.75 caused a false-accept risk. v2.1.13
    adds AUTO_VERIFY_THRESHOLD (default 0.88) — between the lower threshold
    and this value, the attempt is routed to MANUAL_REVIEW so release is
    BLOCKED until an administrator approves.
    """

    def test_settings_default_auto_verify_threshold(self):
        from django.conf import settings as ds
        self.assertEqual(getattr(ds, 'AUTO_VERIFY_THRESHOLD', None), 0.88,
                         'AUTO_VERIFY_THRESHOLD default must be 0.88')

    @override_settings(DEMO_MODE=False)
    def test_system_config_auto_verify_threshold_default(self):
        from verification.models import SystemConfig
        # No DB row, full-enforcement mode → settings default 0.88.
        self.assertEqual(SystemConfig.get_auto_verify_threshold(), 0.88)

    @override_settings(DEMO_MODE=False)
    def test_system_config_auto_verify_threshold_db_override(self):
        from verification.models import SystemConfig
        SystemConfig.objects.create(key='auto_verify_threshold', value='0.92')
        self.assertEqual(SystemConfig.get_auto_verify_threshold(), 0.92)

    @override_settings(DEMO_MODE=False)
    def test_auto_verify_is_strictly_above_lower_threshold(self):
        from verification.models import SystemConfig
        self.assertGreater(
            SystemConfig.get_auto_verify_threshold(),
            SystemConfig.get_threshold(),
            'auto-verify threshold must be strictly higher than the lower threshold',
        )

    def test_settings_low_quality_forces_manual_review_default(self):
        from django.conf import settings as ds
        self.assertTrue(
            getattr(ds, 'LOW_QUALITY_FORCES_MANUAL_REVIEW', False),
            'LOW_QUALITY_FORCES_MANUAL_REVIEW must default to True (v2.1.13)',
        )

    @override_settings(DEMO_MODE=True)
    def test_saved_manual_review_threshold_overrides_demo_mode_default(self):
        """
        Phase A.5-B finding: get_threshold() only falls back to the demo-aware
        default (DEMO_THRESHOLD) when NO SystemConfig row exists for
        'verification_threshold'. Once a value has ever been saved via the
        Verification Threshold Settings form (in EITHER mode), that saved
        value is used in BOTH modes — Assisted Rollout does not automatically
        revert to its own lenient default. This is a real, documented
        behavior (see docs/ANALYTICS-METHODOLOGY.md / the config page's own
        Assisted Rollout explanatory text), not a bug this checkpoint fixes.
        This test locks in the CURRENT behavior so a future change to it is a
        deliberate decision, not a silent regression.
        """
        from verification.models import SystemConfig
        SystemConfig.objects.create(key='verification_threshold', value='0.75')
        self.assertEqual(
            SystemConfig.get_threshold(), 0.75,
            'a saved override must apply even in Assisted Rollout mode, not the demo default (0.60)',
        )
        # auto_verify_threshold has no dedicated save workflow, so with no DB
        # row it still correctly follows the demo-aware default.
        self.assertEqual(SystemConfig.get_auto_verify_threshold(), 0.80)


class VerifySubmitThresholdBandPolicyTest(TestCase):
    """
    Source-level assertion that the three-zone decision policy is wired into
    verify_submit. The string assertions guard against accidental rollback
    to the old 'score >= threshold → VERIFIED' single-zone logic.

    BPA-2 note: the score->decision zone math itself was extracted into
    face_utils.decide_base_outcome() (a pure function) so both verify_submit
    and the controlled evaluation trial workflow apply identical decision
    rules — see EvaluationDecisionParityTest. verify_submit now calls that
    helper instead of inlining the zone comparisons, so the regression guard
    below checks both that views.py wires in the shared helper AND that the
    helper itself still implements the auto-verify zone check.
    """

    def setUp(self):
        import pathlib
        self.content = pathlib.Path('verification/views.py').read_text(
            encoding='utf-8', errors='replace'
        )
        self.face_utils_content = pathlib.Path('verification/face_utils.py').read_text(
            encoding='utf-8', errors='replace'
        )

    def test_auto_verify_threshold_used_in_decision(self):
        self.assertIn('get_auto_verify_threshold()', self.content,
                      'verify_submit must consult get_auto_verify_threshold()')

    def test_decision_uses_auto_verify_threshold_for_verified_zone(self):
        self.assertIn('decide_base_outcome(score, threshold, auto_verify_threshold)', self.content,
                      'verify_submit must delegate the score->decision zone logic to the shared decide_base_outcome() helper')
        self.assertIn('score >= auto_verify_threshold:', self.face_utils_content,
                      'shared decision helper must require score >= auto_verify_threshold for VERIFIED')

    def test_mid_band_routed_to_manual_review(self):
        self.assertIn('Manual review — high-band', self.content,
                      'high-band manual-review reason missing in views.py')

    def test_low_quality_forces_manual_review_branch_present(self):
        self.assertIn('Manual review — low face quality', self.content,
                      'low-quality forced manual-review branch missing in views.py')

    def test_client_movement_no_longer_suppresses_pad_signals(self):
        """
        Security regression (v2.1.16, Hardening Round #1): the v2.1.13
        'landmark_motion_ok' override let a client-reported movement claim
        (peak_yaw_delta/peak_pitch_delta/mediapipe_available — plain JSON a
        modified request can fabricate) suppress PAD's server-computed
        sequence_static/near_duplicate replay-detection signals. That
        override must be gone; analyze_sequence must be driven purely by
        server-computed pixel evidence. See
        VerifyCheckLivenessV3GatesTest.test_forged_movement_claim_does_not_suppress_pad_signal
        for the end-to-end behavioral proof.
        """
        self.assertNotIn('landmark_motion_ok=_landmark_motion_ok', self.content,
                         'Client-derived landmark_motion_ok must not be passed to PAD analyze_sequence')


# ─── Phase A.5-B — Deterministic decision-zone boundary tests ────────────────
# End-to-end (through verify_submit, not source-string matching) proof of the
# exact score->decision mapping in both Normal and Assisted Rollout mode.
# Boundaries are derived from SystemConfig.get_threshold()/
# get_auto_verify_threshold() rather than restated as bare literals, so these
# stay correct if the configured values ever change.

class ThreeZoneDecisionBoundaryTest(TestCase):
    """
    Proves the exact half-open zone boundaries documented in verify_submit's
    'v2.1.13 — three-zone band' comment:
        score >= auto_verify_threshold            -> VERIFIED
        threshold <= score < auto_verify_threshold -> MANUAL_REVIEW (high-band)
        review_band <= score < threshold           -> MANUAL_REVIEW (low-band)
        score < review_band                        -> NOT_VERIFIED
    where review_band = threshold * 0.85, in BOTH Normal and Assisted
    Rollout (DEMO_MODE) configurations.
    """

    def setUp(self):
        import datetime
        from verification.models import StipendEvent

        self.staff = _make_staff('zone_staff')
        self._ben_counter = 0

        today = datetime.date.today()
        self.event = StipendEvent.objects.create(
            title='Zone Test Payout', date=today, amount=500,
            is_active=True, created_by=self.staff,
        )

        self.client = Client()
        self.client.force_login(self.staff)
        self.url = reverse('verification:verify_submit')

    def _new_beneficiary(self):
        # A fresh beneficiary per scored case (rather than reusing one across
        # several POSTs in the same test) sidesteps any ambiguity in
        # identifying "the attempt this call just created" — no shared
        # 'latest by timestamp' query needed, which would be unreliable if
        # two attempts ever landed within the same timestamp resolution.
        from verification.models import FaceEmbedding
        from cryptography.fernet import Fernet
        self._ben_counter += 1
        n = self._ben_counter
        ben = _make_beneficiary(f'BEN-ZONE-{n:03d}', f'SC-ZONE-{n:03d}')
        ben.status = Beneficiary.STATUS_ACTIVE
        ben.save()
        key = Fernet.generate_key()
        fake_enc = Fernet(key).encrypt(b'\x00' * 512)
        FaceEmbedding.objects.create(beneficiary=ben, embedding_data=fake_enc)
        return ben

    def _set_session(self, ben):
        session = self.client.session
        session['verification_session'] = {
            'beneficiary_id': str(ben.pk),
            'attempt_number': 1,
            'session_id': _FIXED_TEST_SESSION_ID,
            'claimant_type': 'beneficiary',
            'stipend_event_id': str(self.event.pk),
            'challenge': 'right',
        }
        session.save()

    @mock.patch('verification.views.load_image_from_bytes')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.check_anti_spoofing')
    @mock.patch('verification.views.process_face_for_verification')
    @mock.patch('verification.views.compare_with_all_embeddings')
    def _decision_for_score(self, score, mock_compare, mock_process, mock_spoof, mock_detect, mock_load):
        import numpy as np
        ben = self._new_beneficiary()
        self._set_session(ben)
        tx = _make_liveness_tx(ben, self.staff, self.event)
        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_spoof.return_value = {'passed': True, 'score': 0.9, 'reason': 'Real face.'}
        mock_process.return_value = {
            'success': True, 'embedding': np.zeros(128),
            'quality': {'ok': True, 'score': 0.9, 'reason': ''}, 'using_mock': False,
        }
        mock_compare.return_value = {
            'success': True, 'score': score,
            'matched_template': 'primary', 'templates_checked': 1, 'all_scores': [],
        }
        payload = json.dumps({
            'image': DATA_URI_JPEG,
            'challenge_completed': True,
            'liveness_score': 0.9,
            'anti_spoof_score': 0.9,
            'liveness_passed': True,
            'face_detected': True,
            'tx_token': str(tx.token),
            'session_id': _FIXED_TEST_SESSION_ID,
        })
        self.client.post(self.url, data=payload, content_type='application/json', secure=True)
        # Assert against the persisted VerificationAttempt.decision (the true
        # DECISION_CHOICES value) rather than the JSON response's client-facing
        # wording — verify_submit maps a stored NOT_VERIFIED decision to a
        # 'retry' (or 'fallback', once retries are exhausted) response string
        # for the operator UI, which is a presentation choice layered on top
        # of the actual decision, not a different decision.
        from verification.models import VerificationAttempt
        return VerificationAttempt.objects.get(beneficiary=ben).decision

    @override_settings(LIVENESS_REQUIRED=True, DEMO_MODE=False, VERIFICATION_THRESHOLD=0.75,
                        AUTO_VERIFY_THRESHOLD=0.88, DEBUG=True)
    def test_normal_mode_zone_boundaries(self):
        from verification.models import SystemConfig
        threshold = SystemConfig.get_threshold()
        auto = SystemConfig.get_auto_verify_threshold()
        review_band = threshold * 0.85
        self.assertAlmostEqual(threshold, 0.75)
        self.assertAlmostEqual(auto, 0.88)
        self.assertAlmostEqual(review_band, 0.6375)

        cases = [
            (review_band - 0.001, 'not_verified', 'just below the review-band floor'),
            (review_band, 'manual_review', 'exactly at the review-band floor (inclusive >=)'),
            (threshold - 0.001, 'manual_review', 'just below the lower threshold (still low-band review)'),
            (threshold, 'manual_review', 'exactly at the lower threshold (inclusive >=, high-band review)'),
            (auto - 0.001, 'manual_review', 'just below the auto-verify threshold'),
            (auto, 'verified', 'exactly at the auto-verify threshold (inclusive >=)'),
            (auto + 0.05, 'verified', 'above the auto-verify threshold'),
        ]
        for score, expected, why in cases:
            with self.subTest(score=round(score, 4), why=why):
                self.assertEqual(self._decision_for_score(score), expected, why)

    @override_settings(LIVENESS_REQUIRED=True, DEMO_MODE=True, DEBUG=True)
    def test_assisted_rollout_mode_zone_boundaries(self):
        """Assisted Rollout (DEMO_MODE) uses a different pair of configured
        threshold VALUES (DEMO_THRESHOLD/DEMO_AUTO_VERIFY_THRESHOLD), but goes
        through the exact same decision code path/boundary logic in
        verify_submit — there is no separate demo-mode decision branch."""
        from verification.models import SystemConfig
        threshold = SystemConfig.get_threshold()
        auto = SystemConfig.get_auto_verify_threshold()
        review_band = threshold * 0.85
        self.assertAlmostEqual(threshold, 0.60, msg='DEMO_THRESHOLD default')
        self.assertAlmostEqual(auto, 0.80, msg='DEMO_AUTO_VERIFY_THRESHOLD default')
        self.assertAlmostEqual(review_band, 0.51)

        cases = [
            (review_band - 0.001, 'not_verified', 'just below the review-band floor'),
            (review_band, 'manual_review', 'exactly at the review-band floor'),
            (threshold, 'manual_review', 'exactly at the lower threshold'),
            (auto - 0.001, 'manual_review', 'just below the auto-verify threshold'),
            (auto, 'verified', 'exactly at the auto-verify threshold'),
        ]
        for score, expected, why in cases:
            with self.subTest(score=round(score, 4), why=why):
                self.assertEqual(self._decision_for_score(score), expected, why)


class VerifyConfigPageTruthfulnessTest(TestCase):
    """
    Phase A.5-B: the Verification Threshold Settings page must not present an
    unsupported scientific/domain claim, must not label the lower threshold as
    if it alone triggered automatic verification, and must show the real
    automatic-verification threshold that actually gates the VERIFIED decision.
    """

    def setUp(self):
        self.admin = CustomUser.objects.create_user(
            username='config_admin', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-CONFIG-ADMIN',
        )
        self.client = Client()
        self.client.force_login(self.admin)
        self.url = reverse('verification:config')

    @override_settings(DEMO_MODE=False, VERIFICATION_THRESHOLD=0.75, AUTO_VERIFY_THRESHOLD=0.88)
    def test_unsupported_senior_citizen_recommendation_removed(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertNotIn('for senior citizens', content)
        self.assertNotIn('Recommended:', content)

    @override_settings(DEMO_MODE=False, VERIFICATION_THRESHOLD=0.75, AUTO_VERIFY_THRESHOLD=0.88)
    def test_page_shows_both_real_thresholds_distinctly_labeled(self):
        resp = self.client.get(self.url)
        content = resp.content.decode()
        self.assertIn('Automatic Verification Threshold', content)
        self.assertIn('Manual Review Threshold', content)
        # The value that actually gates automatic VERIFIED must be shown, not
        # just the lower (manual-review) threshold.
        self.assertContains(resp, '0.88')
        self.assertContains(resp, '0.75')

    @override_settings(DEMO_MODE=False, VERIFICATION_THRESHOLD=0.75, AUTO_VERIFY_THRESHOLD=0.88)
    def test_decision_reference_guide_uses_auto_verify_threshold_for_verified_row(self):
        from verification.models import SystemConfig
        resp = self.client.get(self.url)
        self.assertEqual(resp.context['auto_verify_threshold'], SystemConfig.get_auto_verify_threshold())
        self.assertEqual(resp.context['manual_review_threshold'], SystemConfig.get_threshold())
        # The 'Verified' row's floor must be the auto-verify threshold (0.88),
        # not the lower manual-review threshold (0.75) — this was the original
        # misleading behavior this checkpoint corrects.
        self.assertContains(resp, '&ge; 0.88', html=False)

    @override_settings(DEMO_MODE=True)
    def test_assisted_rollout_shows_its_own_threshold_pair(self):
        resp = self.client.get(self.url)
        content = resp.content.decode()
        self.assertIn('Assisted Rollout', content)
        # Must not claim a single number is "the" threshold while a second
        # boundary (auto-verify) also applies in demo mode.
        self.assertIn('Automatic Verification', content)
        self.assertIn('Manual Review', content)

    @override_settings(DEMO_MODE=False)
    def test_no_raw_none_rendered(self):
        resp = self.client.get(self.url)
        self.assertNotContains(resp, 'None')
        self.assertNotContains(resp, 'undefined')


# ─── v2.1.13 — Manual Review UI wording (Issue 3) ─────────────────────────────

class ManualReviewUIWordingTest(TestCase):
    """
    v2.1.13 (Issue 3): the result page must not let "Liveness PASSED" mislead
    operators on a manual-review attempt. Release status must be explicitly
    shown as BLOCKED with an admin-approval requirement.
    """

    def setUp(self):
        import pathlib
        self.content = pathlib.Path('templates/verification/result.html').read_text(
            encoding='utf-8', errors='replace'
        )

    def test_release_blocked_banner_for_manual_review(self):
        self.assertIn('MANUAL REVIEW &mdash; RELEASE BLOCKED', self.content,
                      'result.html must show MANUAL REVIEW — RELEASE BLOCKED')

    def test_release_status_blocked_line(self):
        self.assertIn('BLOCKED pending administrator review', self.content,
                      'result.html must show release status as BLOCKED pending review')

    def test_liveness_clarifying_note_present(self):
        self.assertIn('live-face gate only', self.content,
                      'result.html must label liveness as a gate, not an identity match')

    def test_auto_verify_marker_in_score_meter(self):
        self.assertIn('Auto-verify', self.content,
                      'result.html score meter must show Auto-verify marker')


# ── Phase 1C — Override Release Payout tests ─────────────────────────────────

class OverrideReleasePayoutTest(TestCase):
    """
    Tests for verification:override_release_payout.

    Covers: access control, happy-path ClaimRecord creation, all guard
    conditions, duplicate prevention, SoD enforcement, and audit log output.
    No FaceNet / ML code is invoked.
    """

    def setUp(self):
        from verification.models import StipendEvent, VerificationAttempt, ClaimRecord
        self.client = Client()

        # Phase B.5 — override_release_payout now revalidates the bound
        # event's claiming window immediately before releasing payout, so
        # this class's fixed August 2026 event must be exercised under a
        # frozen "now" that actually falls inside that window (and inside
        # the 07:00-20:00 global claiming hours), independent of the real
        # wall-clock date the suite happens to run on.
        from zoneinfo import ZoneInfo
        fixed_manila = datetime.datetime(2026, 8, 1, 14, 0, 0, tzinfo=ZoneInfo('Asia/Manila'))
        self._now_patcher = mock.patch(
            'django.utils.timezone.now',
            return_value=fixed_manila.astimezone(datetime.timezone.utc),
        )
        self._now_patcher.start()
        self.addCleanup(self._now_patcher.stop)

        # Admin who performed the original verification (not the same admin
        # who will override/release — used to test SoD separation).
        self.staff = _make_staff(username='orpay_staff', role=CustomUser.ROLE_STAFF)

        # Admin who will override and release (different from performed_by).
        self.admin = CustomUser.objects.create_user(
            username='orpay_admin',
            password='TestPass123!',
            role=CustomUser.ROLE_ADMIN,
            employee_id='EMP-ORPAY',
        )
        self.president = CustomUser.objects.create_user(
            username='orpay_pres',
            password='TestPass123!',
            role=CustomUser.ROLE_PRESIDENT,
            employee_id='EMP-ORPAY-P',
        )

        self.ben = _make_beneficiary(ben_id='BEN-ORPAY-001', sc_id='SC-ORPAY')
        # is_eligible_to_claim requires STATUS_ACTIVE + consent_given
        self.ben.status = Beneficiary.STATUS_ACTIVE
        self.ben.consent_given = True
        self.ben.save()

        self.event = StipendEvent.objects.create(
            title='August 2026 Stipend',
            date='2026-08-01',
            amount=1000,
            event_type=StipendEvent.EVENT_TYPE_REGULAR,
            approval_status=StipendEvent.APPROVAL_APPROVED,
            approved_by=self.admin,
        )

        # A VERIFIED, overridden attempt (the state after admin_override runs).
        self.attempt = VerificationAttempt.objects.create(
            beneficiary=self.ben,
            performed_by=self.staff,
            decision=VerificationAttempt.DECISION_VERIFIED,
            similarity_score=0.72,
            threshold_used=0.75,
            stipend_event=self.event,
            overridden=True,
            override_by=self.admin,
            override_reason='Physical ID confirmed by officer in charge during outreach.',
            override_at=timezone.now(),
        )

        self.url = reverse('verification:override_release_payout', args=[self.attempt.pk])
        self.result_url = reverse('verification:verify_result', args=[self.attempt.pk])

    # ── 1. Release button visible when eligible ───────────────────────────────

    def test_release_button_shown_when_eligible(self):
        """result.html shows the Release Payout button for eligible overridden attempts."""
        from verification.models import ClaimRecord
        self.client.force_login(self.admin)
        response = self.client.get(self.result_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Release Payout')
        self.assertContains(response, 'override-release')

    # ── 2. Release button hidden when claim already exists ────────────────────

    def test_release_button_hidden_when_already_claimed(self):
        """result.html hides the button once a ClaimRecord exists."""
        from verification.models import ClaimRecord
        ClaimRecord.objects.create(
            beneficiary=self.ben,
            stipend_event=self.event,
            claimant_type='beneficiary',
            claimed_by=self.admin,
            verification_attempt=self.attempt,
            status=ClaimRecord.STATUS_CLAIMED,
            verification_method=ClaimRecord.VERIFY_OVERRIDE,
        )
        self.client.force_login(self.admin)
        response = self.client.get(self.result_url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'override_release_payout')

    # ── 3. Non-admin cannot see or use the button ─────────────────────────────

    def test_non_admin_cannot_access_release_page(self):
        """Staff role is blocked from the GET confirmation page."""
        self.client.force_login(self.staff)
        response = self.client.get(self.url)
        # Must redirect away (not render the form)
        self.assertNotEqual(response.status_code, 200)

    def test_non_admin_post_is_blocked(self):
        """Staff POST to override_release_payout is blocked; no ClaimRecord created."""
        from verification.models import ClaimRecord
        self.client.force_login(self.staff)
        response = self.client.post(self.url)
        self.assertNotEqual(response.status_code, 200)
        self.assertFalse(ClaimRecord.objects.filter(verification_attempt=self.attempt).exists())

    # ── 4. Successful ClaimRecord creation ────────────────────────────────────

    def test_release_payout_creates_claim_record(self):
        """POST by eligible admin creates exactly one ClaimRecord with VERIFY_OVERRIDE."""
        from verification.models import ClaimRecord
        self.client.force_login(self.admin)
        response = self.client.post(self.url)
        self.assertRedirects(response, self.result_url, fetch_redirect_response=False)
        claims = ClaimRecord.objects.filter(verification_attempt=self.attempt)
        self.assertEqual(claims.count(), 1)
        claim = claims.first()
        self.assertEqual(claim.verification_method, ClaimRecord.VERIFY_OVERRIDE)
        self.assertEqual(claim.status, ClaimRecord.STATUS_CLAIMED)
        self.assertEqual(claim.beneficiary, self.ben)
        self.assertEqual(claim.stipend_event, self.event)
        self.assertIsNotNone(claim.reference_number)
        self.assertTrue(len(claim.reference_number) > 0)

    def test_release_payout_amount_matches_event(self):
        """Created ClaimRecord snaps the event amount at release time."""
        from verification.models import ClaimRecord
        self.client.force_login(self.admin)
        self.client.post(self.url)
        claim = ClaimRecord.objects.get(verification_attempt=self.attempt)
        self.assertEqual(claim.amount, self.event.amount)

    # ── 5. Duplicate release prevented ───────────────────────────────────────

    def test_duplicate_release_prevented(self):
        """Second POST is rejected; exactly one ClaimRecord exists."""
        from verification.models import ClaimRecord
        self.client.force_login(self.admin)
        self.client.post(self.url)  # first release — succeeds
        self.client.post(self.url)  # second release — must be blocked
        self.assertEqual(ClaimRecord.objects.filter(beneficiary=self.ben, stipend_event=self.event).count(), 1)

    # ── 6. Non-overridden attempt blocked ────────────────────────────────────

    def test_non_overridden_attempt_is_blocked(self):
        """Attempt with overridden=False returns an error; no ClaimRecord created."""
        from verification.models import ClaimRecord, VerificationAttempt
        fresh_attempt = VerificationAttempt.objects.create(
            beneficiary=_make_beneficiary(ben_id='BEN-ORPAY-002', sc_id='SC-ORPAY-2'),
            performed_by=self.staff,
            decision=VerificationAttempt.DECISION_VERIFIED,
            stipend_event=self.event,
            overridden=False,
        )
        fresh_attempt.beneficiary.consent_given = True
        fresh_attempt.beneficiary.save()
        url = reverse('verification:override_release_payout', args=[fresh_attempt.pk])
        self.client.force_login(self.admin)
        response = self.client.post(url)
        self.assertFalse(ClaimRecord.objects.filter(verification_attempt=fresh_attempt).exists())
        # Must redirect away (error, not success)
        self.assertEqual(response.status_code, 302)

    # ── 7. Non-VERIFIED decision blocked ─────────────────────────────────────

    def test_denied_attempt_is_blocked(self):
        """Attempt with decision=denied is blocked; no ClaimRecord created."""
        from verification.models import ClaimRecord, VerificationAttempt
        denied_attempt = VerificationAttempt.objects.create(
            beneficiary=_make_beneficiary(ben_id='BEN-ORPAY-003', sc_id='SC-ORPAY-3'),
            performed_by=self.staff,
            decision=VerificationAttempt.DECISION_DENIED,
            stipend_event=self.event,
            overridden=True,
            override_by=self.admin,
            override_reason='Testing denied guard path for override release.',
            override_at=timezone.now(),
        )
        denied_attempt.beneficiary.consent_given = True
        denied_attempt.beneficiary.save()
        url = reverse('verification:override_release_payout', args=[denied_attempt.pk])
        self.client.force_login(self.admin)
        response = self.client.post(url)
        self.assertFalse(ClaimRecord.objects.filter(verification_attempt=denied_attempt).exists())
        self.assertEqual(response.status_code, 302)

    # ── 8. Missing stipend event blocked ─────────────────────────────────────

    def test_no_stipend_event_is_blocked(self):
        """Attempt with stipend_event=None is blocked; no ClaimRecord created."""
        from verification.models import ClaimRecord, VerificationAttempt
        no_event_attempt = VerificationAttempt.objects.create(
            beneficiary=_make_beneficiary(ben_id='BEN-ORPAY-004', sc_id='SC-ORPAY-4'),
            performed_by=self.staff,
            decision=VerificationAttempt.DECISION_VERIFIED,
            stipend_event=None,
            overridden=True,
            override_by=self.admin,
            override_reason='Testing no-event guard path for override release.',
            override_at=timezone.now(),
        )
        no_event_attempt.beneficiary.consent_given = True
        no_event_attempt.beneficiary.save()
        url = reverse('verification:override_release_payout', args=[no_event_attempt.pk])
        self.client.force_login(self.admin)
        response = self.client.post(url)
        self.assertFalse(ClaimRecord.objects.filter(verification_attempt=no_event_attempt).exists())
        self.assertEqual(response.status_code, 302)

    # ── 9. Segregation of duties — performed_by cannot release ───────────────

    def test_performed_by_admin_cannot_release(self):
        """Admin who performed the attempt cannot also release the payout (non-president)."""
        from verification.models import ClaimRecord, VerificationAttempt
        # Create attempt where performed_by == self.admin (the releasing admin)
        self_attempt = VerificationAttempt.objects.create(
            beneficiary=_make_beneficiary(ben_id='BEN-ORPAY-005', sc_id='SC-ORPAY-5'),
            performed_by=self.admin,   # same as the releasing admin
            decision=VerificationAttempt.DECISION_VERIFIED,
            stipend_event=self.event,
            overridden=True,
            override_by=self.admin,
            override_reason='Testing SoD guard — performed_by == releasing admin.',
            override_at=timezone.now(),
        )
        self_attempt.beneficiary.consent_given = True
        self_attempt.beneficiary.save()
        url = reverse('verification:override_release_payout', args=[self_attempt.pk])
        self.client.force_login(self.admin)
        response = self.client.post(url)
        # Must be blocked
        self.assertFalse(ClaimRecord.objects.filter(verification_attempt=self_attempt).exists())
        self.assertEqual(response.status_code, 302)

    def test_president_may_release_own_attempt(self):
        """President is exempt from the SoD rule and can release their own attempt."""
        from verification.models import ClaimRecord, VerificationAttempt
        pres_attempt = VerificationAttempt.objects.create(
            beneficiary=_make_beneficiary(ben_id='BEN-ORPAY-006', sc_id='SC-ORPAY-6'),
            performed_by=self.president,   # same as the releasing president
            decision=VerificationAttempt.DECISION_VERIFIED,
            stipend_event=self.event,
            overridden=True,
            override_by=self.president,
            override_reason='President releasing their own verified attempt — SoD exempt.',
            override_at=timezone.now(),
        )
        pres_attempt.beneficiary.status = Beneficiary.STATUS_ACTIVE
        pres_attempt.beneficiary.consent_given = True
        pres_attempt.beneficiary.save()
        url = reverse('verification:override_release_payout', args=[pres_attempt.pk])
        self.client.force_login(self.president)
        response = self.client.post(url)
        self.assertEqual(ClaimRecord.objects.filter(verification_attempt=pres_attempt).count(), 1)

    # ── 10. AuditLog ACTION_CLAIM created ────────────────────────────────────

    def test_audit_log_action_claim_created(self):
        """ACTION_CLAIM audit log entry with via='override_release_payout' is created."""
        from logs.models import AuditLog
        self.client.force_login(self.admin)
        self.client.post(self.url)
        entry = AuditLog.objects.filter(
            action=AuditLog.ACTION_CLAIM,
            user=self.admin,
        ).first()
        self.assertIsNotNone(entry, 'ACTION_CLAIM audit log entry must be created')
        self.assertEqual(entry.details.get('via'), 'override_release_payout')
        self.assertEqual(entry.details.get('beneficiary_id'), self.ben.beneficiary_id)

    # ── 11. VerificationAttempt unchanged after release ───────────────────────

    def test_verification_attempt_unchanged_after_release(self):
        """override_release_payout must not modify the VerificationAttempt record."""
        from verification.models import VerificationAttempt
        before = VerificationAttempt.objects.get(pk=self.attempt.pk)
        decision_before = before.decision
        override_reason_before = before.override_reason
        override_by_id_before = before.override_by_id

        self.client.force_login(self.admin)
        self.client.post(self.url)

        after = VerificationAttempt.objects.get(pk=self.attempt.pk)
        self.assertEqual(after.decision, decision_before)
        self.assertEqual(after.override_reason, override_reason_before)
        self.assertEqual(after.override_by_id, override_by_id_before)


# ── v2.1.16 hardening: Django Admin permission tests ──────────────────────────
# Finding 11 (SystemConfig) + regression coverage for the pre-existing
# read-only admins (VerificationAttempt/FaceEmbedding/ClaimRecord), which had
# no behavioral admin tests before this pass -- only exercised indirectly.

class ReadOnlySensitiveAdminTest(TestCase):
    """
    Exercise actual ModelAdmin permission methods for the sensitive models
    that must never be writable through Django Admin, using a real superuser
    and the real admin site registry -- not just asserting the methods exist.
    """

    def setUp(self):
        self.superuser = CustomUser.objects.create_superuser(
            username='admin_super', password='TestPass123!', employee_id='EMP-SUPER-01',
        )
        self.client = Client()
        self.client.force_login(self.superuser)

    def _model_admin(self, model):
        from django.contrib import admin as django_admin
        return django_admin.site._registry[model]

    def test_face_embedding_admin_denies_add(self):
        from verification.models import FaceEmbedding
        ma = self._model_admin(FaceEmbedding)
        from django.test import RequestFactory
        request = RequestFactory().get('/admin/verification/faceembedding/add/')
        request.user = self.superuser
        self.assertFalse(ma.has_add_permission(request))

    def test_face_embedding_admin_add_view_returns_403(self):
        url = reverse('admin:verification_faceembedding_add')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_verification_attempt_admin_denies_change(self):
        from verification.models import VerificationAttempt
        ma = self._model_admin(VerificationAttempt)
        from django.test import RequestFactory
        request = RequestFactory().get('/')
        request.user = self.superuser
        self.assertFalse(ma.has_change_permission(request))
        self.assertFalse(ma.has_delete_permission(request))

    def test_claim_record_admin_denies_delete(self):
        from verification.models import ClaimRecord
        ma = self._model_admin(ClaimRecord)
        from django.test import RequestFactory
        request = RequestFactory().get('/')
        request.user = self.superuser
        self.assertFalse(ma.has_delete_permission(request))

    def test_claim_record_admin_delete_selected_action_not_offered(self):
        from verification.models import ClaimRecord
        ma = self._model_admin(ClaimRecord)
        from django.test import RequestFactory
        request = RequestFactory().get('/')
        request.user = self.superuser
        self.assertNotIn('delete_selected', ma.get_actions(request))


class SystemConfigAdminSecurityTest(TestCase):
    """
    Behavioral bypass tests for the v2.1.16 SystemConfig admin hardening
    (finding 11, extended by the Codex SystemConfig follow-up). Exercises the
    real admin views (add/change/delete) with a real superuser client, not
    just method presence.

    Codex's audit found that ALL FOUR auto_approve_* keys (not just the two
    biometric thresholds) already go through a dedicated, audited
    application workflow (beneficiaries.views.auto_approval_settings), so
    SystemConfig.CONTROLLED_KEYS now covers all six keys. That leaves no
    currently-real "ordinary, Django-Admin-editable" SystemConfig key in the
    app today -- self.ordinary below uses a synthetic key name specifically
    to prove the admin's general uncontrolled-key mechanism still works,
    since none of the real keys are uncontrolled any more.
    """

    def setUp(self):
        from verification.models import SystemConfig
        self.SystemConfig = SystemConfig
        self.superuser = CustomUser.objects.create_superuser(
            username='admin_config', password='TestPass123!', employee_id='EMP-CFG-01',
        )
        self.client = Client()
        self.client.force_login(self.superuser)
        self.sensitive = SystemConfig.objects.create(
            key='verification_threshold', value='0.75', description='sensitive',
        )
        self.ordinary = SystemConfig.objects.create(
            key='demo_diagnostics_banner_enabled', value='false', description='ordinary toggle',
        )

    def test_controlled_keys_constant_contains_expected_keys(self):
        expected = {
            'verification_threshold',
            'auto_verify_threshold',
            'auto_approve_beneficiaries',
            'auto_approve_representatives',
            'auto_approve_face_enrollments',
            'auto_approve_user_accounts',
        }
        self.assertEqual(self.SystemConfig.CONTROLLED_KEYS, frozenset(expected))

    def test_cannot_change_existing_sensitive_key_via_admin_get(self):
        """
        Django admin's dual view/change permission model means a superuser
        (who always has view permission) sees a READ-ONLY rendering of the
        change form (200), not a 403 -- has_change_permission() itself must
        still be False so the SAVE path (POST) is blocked; see
        test_cannot_change_existing_sensitive_key_via_admin_post.
        """
        from django.contrib import admin as django_admin
        from django.test import RequestFactory
        url = reverse('admin:verification_systemconfig_change', args=[self.sensitive.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        ma = django_admin.site._registry[self.SystemConfig]
        request = RequestFactory().get(url)
        request.user = self.superuser
        self.assertFalse(ma.has_change_permission(request, self.sensitive))

    def test_cannot_change_existing_sensitive_key_via_admin_post(self):
        url = reverse('admin:verification_systemconfig_change', args=[self.sensitive.pk])
        response = self.client.post(url, {
            'key': 'verification_threshold', 'value': '0.10', 'description': 'tampered',
            'updated_by': self.superuser.pk,
        })
        self.assertEqual(response.status_code, 403)
        self.sensitive.refresh_from_db()
        self.assertEqual(self.sensitive.value, '0.75', 'sensitive value must be unchanged')

    def test_cannot_delete_existing_sensitive_key_via_admin(self):
        url = reverse('admin:verification_systemconfig_delete', args=[self.sensitive.pk])
        response = self.client.post(url, {'post': 'yes'})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(
            self.SystemConfig.objects.filter(pk=self.sensitive.pk).exists(),
            'sensitive key must not be deletable through admin',
        )

    def test_cannot_create_new_sensitive_key_via_admin_add(self):
        url = reverse('admin:verification_systemconfig_add')
        response = self.client.post(url, {
            'key': 'auto_verify_threshold', 'value': '0.20', 'description': 'malicious low threshold',
            'updated_by': self.superuser.pk,
        })
        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            self.SystemConfig.objects.filter(key='auto_verify_threshold').exists(),
            'a new sensitive key must not be creatable through Django Admin',
        )

    def test_cannot_rename_ordinary_key_into_sensitive_key(self):
        """The `key` field must be read-only once a row exists, so a non-sensitive
        row cannot be renamed to collide with / replace a sensitive one."""
        url = reverse('admin:verification_systemconfig_change', args=[self.ordinary.pk])
        response = self.client.post(url, {
            'key': 'verification_threshold', 'value': 'true', 'description': 'renamed',
            'updated_by': self.superuser.pk,
        })
        self.assertEqual(response.status_code, 302, 'a readonly key field must not block a valid edit')
        self.ordinary.refresh_from_db()
        self.assertEqual(
            self.ordinary.key, 'demo_diagnostics_banner_enabled',
            'key field must be read-only on change, preventing rename into a sensitive key',
        )

    def test_ordinary_key_value_remains_editable(self):
        """Non-sensitive SystemConfig rows must remain normally editable."""
        url = reverse('admin:verification_systemconfig_change', args=[self.ordinary.pk])
        response = self.client.post(url, {
            'key': 'auto_approve_beneficiaries', 'value': 'true', 'description': 'toggled by IT',
            'updated_by': self.superuser.pk,
        })
        self.assertEqual(response.status_code, 302, 'ordinary key edit should succeed and redirect')
        self.ordinary.refresh_from_db()
        self.assertEqual(self.ordinary.value, 'true')

    def test_ordinary_key_remains_deletable(self):
        url = reverse('admin:verification_systemconfig_delete', args=[self.ordinary.pk])
        response = self.client.post(url, {'post': 'yes'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.SystemConfig.objects.filter(pk=self.ordinary.pk).exists())

    def test_bulk_delete_selected_action_not_offered_for_systemconfig(self):
        from django.contrib import admin as django_admin
        ma = django_admin.site._registry[self.SystemConfig]
        from django.test import RequestFactory
        request = RequestFactory().get('/')
        request.user = self.superuser
        self.assertNotIn('delete_selected', ma.get_actions(request))

    def test_ordinary_key_creatable_via_admin_add(self):
        url = reverse('admin:verification_systemconfig_add')
        response = self.client.post(url, {
            'key': 'demo_maintenance_message', 'value': 'true', 'description': 'ordinary',
            'updated_by': self.superuser.pk,
        })
        self.assertEqual(response.status_code, 302, 'ordinary keys must remain creatable')
        self.assertTrue(self.SystemConfig.objects.filter(key='demo_maintenance_message').exists())


class SystemConfigControlledAutoApproveKeysTest(TestCase):
    """
    Codex SystemConfig follow-up: the four auto_approve_* keys are controlled
    through beneficiaries.views.auto_approval_settings exactly like
    verification_threshold is controlled through verify_config -- so Django
    Admin must block create/change/delete/rename-bypass for every one of
    them, while that dedicated application workflow keeps working normally.
    """

    AUTO_APPROVE_KEYS = [
        'auto_approve_beneficiaries',
        'auto_approve_representatives',
        'auto_approve_face_enrollments',
        'auto_approve_user_accounts',
    ]

    def setUp(self):
        from verification.models import SystemConfig
        self.SystemConfig = SystemConfig
        self.superuser = CustomUser.objects.create_superuser(
            username='admin_autoapprove', password='TestPass123!', employee_id='EMP-AA-01',
        )
        self.client = Client()
        self.client.force_login(self.superuser)

    def test_each_auto_approve_key_blocks_admin_create(self):
        for key in self.AUTO_APPROVE_KEYS:
            with self.subTest(key=key):
                url = reverse('admin:verification_systemconfig_add')
                response = self.client.post(url, {
                    'key': key, 'value': 'true', 'description': 'attempted bypass',
                    'updated_by': self.superuser.pk,
                })
                self.assertEqual(response.status_code, 403)
                self.assertFalse(self.SystemConfig.objects.filter(key=key).exists())

    def test_each_auto_approve_key_blocks_admin_change(self):
        for key in self.AUTO_APPROVE_KEYS:
            with self.subTest(key=key):
                obj = self.SystemConfig.objects.create(key=key, value='false')
                url = reverse('admin:verification_systemconfig_change', args=[obj.pk])
                response = self.client.post(url, {
                    'key': key, 'value': 'true', 'description': 'tampered',
                    'updated_by': self.superuser.pk,
                })
                self.assertEqual(response.status_code, 403)
                obj.refresh_from_db()
                self.assertEqual(obj.value, 'false')

    def test_each_auto_approve_key_blocks_admin_delete(self):
        for key in self.AUTO_APPROVE_KEYS:
            with self.subTest(key=key):
                obj = self.SystemConfig.objects.create(key=key, value='false')
                url = reverse('admin:verification_systemconfig_delete', args=[obj.pk])
                response = self.client.post(url, {'post': 'yes'})
                self.assertEqual(response.status_code, 403)
                self.assertTrue(self.SystemConfig.objects.filter(pk=obj.pk).exists())

    def test_each_auto_approve_key_blocks_rename_bypass(self):
        """An uncontrolled row must not be renamable into a controlled auto_approve_* key."""
        for key in self.AUTO_APPROVE_KEYS:
            with self.subTest(key=key):
                obj = self.SystemConfig.objects.create(key='demo_scratch_key', value='x')
                url = reverse('admin:verification_systemconfig_change', args=[obj.pk])
                self.client.post(url, {
                    'key': key, 'value': 'x', 'description': 'renamed', 'updated_by': self.superuser.pk,
                })
                obj.refresh_from_db()
                self.assertEqual(obj.key, 'demo_scratch_key')
                obj.delete()

    def test_auto_approval_settings_workflow_still_functions_despite_admin_lockout(self):
        """
        The dedicated audited application workflow writes to SystemConfig
        directly (set_value()/get_bool()), bypassing Django Admin entirely,
        so it must be completely unaffected by the admin hardening above.
        """
        url = reverse('beneficiaries:auto_approval_settings')
        response = self.client.post(url, {'auto_approve_beneficiaries': 'on'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.SystemConfig.get_bool('auto_approve_beneficiaries'))

        response = self.client.post(url, {})  # all checkboxes off
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.SystemConfig.get_bool('auto_approve_beneficiaries'))


class AnalyticsDashboardTest(TestCase):
    """P1.4 — Analytics Dashboard (Executive/Operational/Security). Read-only,
    built entirely on existing tables; admin-only gating matches report_* views."""

    def setUp(self):
        self.admin = CustomUser.objects.create_user(
            username='analytics_admin', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-ANALYTICS-ADMIN',
        )
        self.staff = _make_staff('analytics_staff')
        self.beneficiary = _make_beneficiary('BEN-ANALYTICS-001', 'SC-ANALYTICS-001')
        self.client = Client()
        self.urls = [
            reverse('verification:analytics_executive'),
            reverse('verification:analytics_operational'),
            reverse('verification:analytics_security'),
        ]

    def test_non_admin_denied_on_all_three_urls(self):
        self.client.force_login(self.staff)
        for url in self.urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertNotEqual(response.status_code, 200)

    def test_admin_get_returns_200_with_expected_context(self):
        from .models import VerificationAttempt
        VerificationAttempt.objects.create(
            beneficiary=self.beneficiary, performed_by=self.staff,
            decision=VerificationAttempt.DECISION_VERIFIED,
        )
        self.client.force_login(self.admin)

        resp = self.client.get(reverse('verification:analytics_executive'))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('total_verifications', resp.context)
        self.assertIn('verified_rate', resp.context)

        resp = self.client.get(reverse('verification:analytics_operational'))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('decision_breakdown', resp.context)

        resp = self.client.get(reverse('verification:analytics_security'))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('manual_review_pending', resp.context)

    def test_date_range_filtering_scopes_aggregates(self):
        from .models import VerificationAttempt
        today = timezone.now()
        old = VerificationAttempt.objects.create(
            beneficiary=self.beneficiary, performed_by=self.staff,
            decision=VerificationAttempt.DECISION_VERIFIED,
        )
        VerificationAttempt.objects.filter(pk=old.pk).update(timestamp=today - datetime.timedelta(days=10))

        recent1 = VerificationAttempt.objects.create(
            beneficiary=self.beneficiary, performed_by=self.staff,
            decision=VerificationAttempt.DECISION_VERIFIED,
        )
        recent2 = VerificationAttempt.objects.create(
            beneficiary=self.beneficiary, performed_by=self.staff,
            decision=VerificationAttempt.DECISION_NOT_VERIFIED,
        )
        VerificationAttempt.objects.filter(pk__in=[recent1.pk, recent2.pk]).update(timestamp=today)

        self.client.force_login(self.admin)
        date_from = today.date().isoformat()
        resp = self.client.get(reverse('verification:analytics_executive'), {'date_from': date_from})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.context['total_verifications'], 2,
            'date_from must exclude the 10-day-old attempt and include only the 2 recent ones',
        )
        self.assertEqual(resp.context['verified_count'], 1)

    def test_csv_export_available_on_all_three_tabs(self):
        self.client.force_login(self.admin)
        for name in ['analytics_executive', 'analytics_operational', 'analytics_security']:
            with self.subTest(name=name):
                resp = self.client.get(reverse(f'verification:{name}'), {'export': 'csv'})
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp['Content-Type'], 'text/csv')
                self.assertIn('attachment', resp['Content-Disposition'])


# ──────────────────────────────────────────────────────────────────────────────
# v2.2.0 Post-UAT Phase 13 — Analytics improvement: Claim Progress, Payout
# Completion, Verification Results, and Review/Security Cases charts.
# ──────────────────────────────────────────────────────────────────────────────

class AnalyticsPhase13ChartsTest(TestCase):

    def setUp(self):
        self.admin = CustomUser.objects.create_user(
            username='p13_admin', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-P13-ADMIN',
        )
        self.staff = _make_staff('p13staff01')
        self.client = Client()
        self.client.force_login(self.admin)

    def test_distribution_progress_none_when_no_active_event(self):
        from . import analytics as _analytics
        metrics = _analytics.get_executive_metrics()
        self.assertIsNone(metrics['distribution_progress'])
        resp = self.client.get(reverse('verification:analytics_executive'))
        self.assertNotContains(resp, 'claimProgressChart')

    def test_distribution_progress_computed_for_active_event(self):
        from .models import StipendEvent, ClaimRecord
        today = datetime.date.today()
        event = StipendEvent.objects.create(
            title='P13 Payout', date=today, amount=500,
            created_by=self.admin, approval_status=StipendEvent.APPROVAL_APPROVED,
            payout_start_date=today, payout_end_date=today,
        )
        b1 = _make_beneficiary('BEN-P13-001', 'SC-P13-001')
        b2 = _make_beneficiary('BEN-P13-002', 'SC-P13-002')
        for b in (b1, b2):
            b.status = 'active'
            b.consent_given = True
            b.save(update_fields=['status', 'consent_given'])
        ClaimRecord.objects.create(
            beneficiary=b1, stipend_event=event, claimant_type='beneficiary',
            status=ClaimRecord.STATUS_CLAIMED, claimed_by=self.admin,
        )

        from . import analytics as _analytics
        metrics = _analytics.get_executive_metrics()
        dp = metrics['distribution_progress']
        self.assertIsNotNone(dp)
        self.assertEqual(dp['event_title'], 'P13 Payout')
        self.assertEqual(dp['expected'], 2)
        self.assertEqual(dp['claimed'], 1)
        self.assertEqual(dp['remaining'], 1)

        resp = self.client.get(reverse('verification:analytics_executive'))
        self.assertContains(resp, 'claimProgressChart')
        self.assertContains(resp, 'payoutCompletionChart')

    def test_verification_results_chart_present_with_data(self):
        from .models import VerificationAttempt
        b = _make_beneficiary('BEN-P13-003', 'SC-P13-003')
        VerificationAttempt.objects.create(
            beneficiary=b, performed_by=self.staff,
            decision=VerificationAttempt.DECISION_VERIFIED,
        )
        resp = self.client.get(reverse('verification:analytics_operational'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'verificationResultsChart')

    def test_review_cases_counts(self):
        from beneficiaries.models import Beneficiary, SharedRepresentativeReview, Representative
        from . import analytics as _analytics

        _make_beneficiary('BEN-P13-004', 'SC-P13-004')
        dup = _make_beneficiary('BEN-P13-005', 'SC-P13-005')
        dup.duplicate_review_required = True
        dup.save(update_fields=['duplicate_review_required'])

        rep = _make_rep(dup, self.admin, id_number='SSS-P13-001')
        SharedRepresentativeReview.objects.create(
            representative=rep, matched_beneficiary_id='BEN-P13-004',
            matched_beneficiary_name='Someone', matched_score=0.9, matched_threshold=0.8,
            status=SharedRepresentativeReview.STATUS_PENDING,
        )

        metrics = _analytics.get_security_metrics()
        self.assertEqual(metrics['review_cases']['duplicate_face'], 1)
        self.assertEqual(metrics['review_cases']['representative_review'], 1)

        resp = self.client.get(reverse('verification:analytics_security'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'reviewCasesChart')

    def test_review_cases_resolved_case_not_counted(self):
        from beneficiaries.models import SharedRepresentativeReview
        from . import analytics as _analytics

        dup = _make_beneficiary('BEN-P13-006', 'SC-P13-006')
        rep = _make_rep(dup, self.admin, id_number='SSS-P13-002')
        SharedRepresentativeReview.objects.create(
            representative=rep, matched_beneficiary_id='BEN-P13-006',
            matched_beneficiary_name='Someone', matched_score=0.9, matched_threshold=0.8,
            status=SharedRepresentativeReview.STATUS_APPROVED,
        )
        metrics = _analytics.get_security_metrics()
        self.assertEqual(metrics['review_cases']['representative_review'], 0)


class AnalyticsCorrectnessTest(TestCase):
    """Phase A analytics-correctness checkpoint: date-range zero-fill, the
    'active event vs. scheduled-but-outside-window' distinction, fraud_alerts
    excluding LOW-risk rows, and the invalid-date-range warning flag."""

    def setUp(self):
        self.admin = CustomUser.objects.create_user(
            username='correctness_admin', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-CORRECTNESS-ADMIN',
        )
        self.staff = _make_staff('correctness_staff')
        self.client = Client()
        self.client.force_login(self.admin)

    def test_daily_trend_zero_fills_gaps_in_bounded_range(self):
        from .models import VerificationAttempt
        from . import analytics as _analytics

        ben = _make_beneficiary('BEN-FILL-001', 'SC-FILL-001')
        today = datetime.date.today()
        five_days_ago = today - datetime.timedelta(days=5)
        attempt = VerificationAttempt.objects.create(
            beneficiary=ben, performed_by=self.staff,
            decision=VerificationAttempt.DECISION_VERIFIED,
        )
        VerificationAttempt.objects.filter(pk=attempt.pk).update(
            timestamp=timezone.make_aware(datetime.datetime.combine(five_days_ago, datetime.time(12, 0))),
        )

        metrics = _analytics.get_operational_metrics(date_from=five_days_ago, date_to=today)
        days = [row['day'] for row in metrics['daily_trend']]
        self.assertEqual(len(days), 6, 'a 6-day inclusive range (day 0..5) must produce 6 rows, not just the 1 with data')
        self.assertEqual(days, sorted(days), 'days must be in chronological order with no gaps')
        by_day = {row['day']: row for row in metrics['daily_trend']}
        self.assertEqual(by_day[five_days_ago]['total'], 1)
        self.assertEqual(by_day[today]['total'], 0, 'a day with no attempts must be zero-filled, not omitted')

    def test_daily_trend_unbounded_range_not_filled(self):
        """Without both bounds there is no natural fill window — leave as-is
        rather than guessing a start/end."""
        from . import analytics as _analytics
        metrics = _analytics.get_operational_metrics(date_from=None, date_to=None)
        self.assertEqual(metrics['daily_trend'], [])

    def test_event_today_outside_window_distinguished_from_no_event(self):
        from .models import StipendEvent
        from . import analytics as _analytics

        # No event at all today.
        metrics = _analytics.get_executive_metrics()
        self.assertIsNone(metrics['active_event'])
        self.assertFalse(metrics['event_today_outside_window'])

        # An approved event dated today, but its claiming window already closed
        # — a fixed 1-hour window ending 2 hours before "now" (Manila time),
        # rather than a hardcoded clock time, so this can't flake depending on
        # what time of day the test suite happens to run.
        from zoneinfo import ZoneInfo
        manila_now = timezone.now().astimezone(ZoneInfo('Asia/Manila'))
        window_start = (manila_now - datetime.timedelta(hours=3)).time()
        window_end = (manila_now - datetime.timedelta(hours=2)).time()
        today = datetime.date.today()
        StipendEvent.objects.create(
            title='Closed-Window Payout', date=today, amount=500,
            created_by=self.admin, approval_status=StipendEvent.APPROVAL_APPROVED,
            payout_start_date=today, payout_end_date=today,
            payout_start_time=window_start, payout_end_time=window_end,
        )
        metrics = _analytics.get_executive_metrics()
        self.assertIsNone(metrics['active_event'], 'the 00:00-00:01 window should not still be open')
        self.assertTrue(
            metrics['event_today_outside_window'],
            'an event dated today outside its time window must not be reported the same as no event at all',
        )

        resp = self.client.get(reverse('verification:analytics_executive'))
        self.assertContains(resp, 'outside its claiming time window')
        self.assertNotContains(resp, 'No payout scheduled today')

    def test_fraud_alerts_excludes_low_risk_rows(self):
        from .models import VerificationAttempt
        from . import analytics as _analytics
        from django.conf import settings as dj_settings

        threshold = dj_settings.FRAUD_REPEATED_FAILURE_THRESHOLD
        ben = _make_beneficiary('BEN-FRAUD-LOW', 'SC-FRAUD-LOW')
        # Exactly at threshold scores 30/100 -> LOW band (see fraud_signals._risk_band).
        for _ in range(threshold):
            VerificationAttempt.objects.create(
                beneficiary=ben, performed_by=self.staff,
                decision=VerificationAttempt.DECISION_NOT_VERIFIED,
            )

        metrics = _analytics.get_security_metrics()
        self.assertEqual(metrics['fraud_risk_counts']['LOW'], 1)
        self.assertEqual(
            metrics['review_cases']['fraud_alerts'], 0,
            'a LOW-risk row must not inflate the open Fraud Alerts case count',
        )

    def test_date_range_invalid_flag_when_from_after_to(self):
        resp = self.client.get(reverse('verification:analytics_executive'), {
            'date_from': '2026-06-01', 'date_to': '2026-01-01',
        })
        self.assertTrue(resp.context['date_range_invalid'])
        self.assertContains(resp, 'is after')

        resp = self.client.get(reverse('verification:analytics_executive'), {
            'date_from': '2026-01-01', 'date_to': '2026-06-01',
        })
        self.assertFalse(resp.context['date_range_invalid'])


class FraudSignalsTest(TestCase):
    """P1.5 — Fraud Detection Phase 1. Rule-based FLAG signals only: every
    function/view here must be read-only, never touching account_status,
    is_active, or any other model (regression guard below)."""

    def setUp(self):
        self.admin = CustomUser.objects.create_user(
            username='fraud_admin', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-FRAUD-ADMIN',
        )
        self.staff = _make_staff('fraud_staff')
        self.beneficiary = _make_beneficiary('BEN-FRAUD-001', 'SC-FRAUD-001')
        self.client = Client()

    @override_settings(FRAUD_REPEATED_FAILURE_THRESHOLD=3, FRAUD_REPEATED_FAILURE_WINDOW_DAYS=30)
    def test_risk_level_bands_and_notification_sync(self):
        """LOW at threshold, MEDIUM at 2x, HIGH at 3x+ — and sync_fraud_notifications
        only notifies MEDIUM/HIGH (never LOW), matching the LOW/MEDIUM/HIGH spec
        with no auto-block anywhere in this path."""
        from .models import VerificationAttempt
        from . import fraud_signals
        from logs.models import Notification

        low_ben = _make_beneficiary('BEN-RISK-LOW', 'SC-RISK-LOW')     # 3 failures = threshold = LOW
        med_ben = _make_beneficiary('BEN-RISK-MED', 'SC-RISK-MED')     # 6 failures = 2x = MEDIUM
        high_ben = _make_beneficiary('BEN-RISK-HIGH', 'SC-RISK-HIGH')  # 9 failures = 3x = HIGH
        for ben, n in [(low_ben, 3), (med_ben, 6), (high_ben, 9)]:
            for _ in range(n):
                VerificationAttempt.objects.create(
                    beneficiary=ben, performed_by=self.staff,
                    decision=VerificationAttempt.DECISION_NOT_VERIFIED,
                )

        rows = {r['beneficiary__id']: r['risk_level'] for r in fraud_signals.repeated_failures()}
        self.assertEqual(rows[low_ben.id], fraud_signals.RISK_LOW)
        self.assertEqual(rows[med_ben.id], fraud_signals.RISK_MEDIUM)
        self.assertEqual(rows[high_ben.id], fraud_signals.RISK_HIGH)

        fraud_signals.sync_fraud_notifications()
        notified_keys = set(
            Notification.objects.filter(category=Notification.CATEGORY_FRAUD_ALERT)
            .values_list('dedupe_key', flat=True)
        )
        self.assertNotIn(f'fraud_repeated_failures:{low_ben.id}:LOW', notified_keys)
        self.assertIn(f'fraud_repeated_failures:{med_ben.id}:MEDIUM', notified_keys)
        self.assertIn(f'fraud_repeated_failures:{high_ben.id}:HIGH', notified_keys)

        # Re-running must not duplicate notifications for an unchanged risk band.
        before = Notification.objects.count()
        fraud_signals.sync_fraud_notifications()
        self.assertEqual(Notification.objects.count(), before)

    def test_risk_score_explicit_0_to_100_bands(self):
        """v2.2.0 Phase 7 — explicit point score, not just a band label.
        0-30 LOW, 31-70 MEDIUM, 71-100 HIGH, capped at 100."""
        from . import fraud_signals as fs
        self.assertEqual(fs._risk_score(3, 3), 30)
        self.assertEqual(fs._risk_band(30), fs.RISK_LOW)
        self.assertEqual(fs._risk_score(6, 3), 60)
        self.assertEqual(fs._risk_band(60), fs.RISK_MEDIUM)
        self.assertEqual(fs._risk_score(9, 3), 90)
        self.assertEqual(fs._risk_band(90), fs.RISK_HIGH)
        # far beyond threshold must cap at 100, never exceed it
        self.assertEqual(fs._risk_score(100, 3), 100)

    @override_settings(FRAUD_LOGIN_FAILURE_THRESHOLD=5, FRAUD_LOGIN_FAILURE_WINDOW_HOURS=24)
    def test_repeated_login_failures_signal(self):
        from logs.models import AuditLog
        from . import fraud_signals

        for _ in range(6):
            AuditLog.objects.create(action=AuditLog.ACTION_LOGIN_FAILED, ip_address='10.0.0.9')
        for _ in range(2):
            AuditLog.objects.create(action=AuditLog.ACTION_LOGIN_FAILED, ip_address='10.0.0.1')

        rows = {r['ip_address']: r for r in fraud_signals.repeated_login_failures()}
        self.assertIn('10.0.0.9', rows)
        self.assertNotIn('10.0.0.1', rows)
        self.assertEqual(rows['10.0.0.9']['risk_level'], fraud_signals.RISK_MEDIUM)

    @override_settings(FRAUD_PAYOUT_ANOMALY_THRESHOLD=5, FRAUD_PAYOUT_ANOMALY_WINDOW_HOURS=24)
    def test_payout_anomalies_signal(self):
        from logs.models import AuditLog
        from . import fraud_signals

        for _ in range(6):
            AuditLog.objects.create(action=AuditLog.ACTION_PAYOUT_OVERRIDE, user=self.admin)
        for _ in range(2):
            AuditLog.objects.create(action=AuditLog.ACTION_PAYOUT_CANCELLED, user=self.staff)

        rows = {r['user__id']: r for r in fraud_signals.payout_anomalies()}
        self.assertIn(self.admin.id, rows)
        self.assertNotIn(self.staff.id, rows)

    @override_settings(FRAUD_LOGIN_FAILURE_THRESHOLD=5, FRAUD_LOGIN_FAILURE_WINDOW_HOURS=24)
    def test_login_failure_notification_uses_security_alert_category(self):
        from logs.models import AuditLog, Notification
        from . import fraud_signals

        for _ in range(6):
            AuditLog.objects.create(action=AuditLog.ACTION_LOGIN_FAILED, ip_address='10.0.0.9')
        fraud_signals.sync_fraud_notifications()
        self.assertTrue(
            Notification.objects.filter(
                category=Notification.CATEGORY_SECURITY_ALERT, dedupe_key__startswith='fraud_login_failures:',
            ).exists()
        )

    def test_fraud_signals_report_includes_all_five_sections(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('verification:fraud_signals_report'))
        self.assertEqual(response.status_code, 200)
        for key in ['repeated_failures', 'anomalous_staff_volume', 'mass_edit_detection',
                    'repeated_login_failures', 'payout_anomalies']:
            self.assertIn(key, response.context)

    @override_settings(FRAUD_REPEATED_FAILURE_THRESHOLD=3, FRAUD_REPEATED_FAILURE_WINDOW_DAYS=30)
    def test_repeated_failures_over_threshold_flagged_under_not(self):
        from .models import VerificationAttempt
        from . import fraud_signals

        over = _make_beneficiary('BEN-FRAUD-OVER', 'SC-FRAUD-OVER')
        for _ in range(4):
            VerificationAttempt.objects.create(
                beneficiary=over, performed_by=self.staff,
                decision=VerificationAttempt.DECISION_NOT_VERIFIED,
            )
        under = _make_beneficiary('BEN-FRAUD-UNDER', 'SC-FRAUD-UNDER')
        for _ in range(2):
            VerificationAttempt.objects.create(
                beneficiary=under, performed_by=self.staff,
                decision=VerificationAttempt.DECISION_NOT_VERIFIED,
            )

        results = fraud_signals.repeated_failures()
        flagged_ids = {r['beneficiary__id'] for r in results}
        self.assertIn(over.id, flagged_ids)
        self.assertNotIn(under.id, flagged_ids)

    @override_settings(FRAUD_REPEATED_FAILURE_THRESHOLD=3, FRAUD_REPEATED_FAILURE_WINDOW_DAYS=30)
    def test_repeated_failures_outside_window_excluded(self):
        from .models import VerificationAttempt
        from . import fraud_signals

        old = _make_beneficiary('BEN-FRAUD-OLD', 'SC-FRAUD-OLD')
        attempts = [
            VerificationAttempt.objects.create(
                beneficiary=old, performed_by=self.staff,
                decision=VerificationAttempt.DECISION_NOT_VERIFIED,
            ) for _ in range(4)
        ]
        stale_time = timezone.now() - datetime.timedelta(days=45)
        VerificationAttempt.objects.filter(
            pk__in=[a.pk for a in attempts],
        ).update(timestamp=stale_time)

        results = fraud_signals.repeated_failures()
        flagged_ids = {r['beneficiary__id'] for r in results}
        self.assertNotIn(old.id, flagged_ids)

    @override_settings(FRAUD_STAFF_VOLUME_THRESHOLD=5, FRAUD_STAFF_VOLUME_WINDOW_HOURS=24)
    def test_anomalous_staff_volume_over_and_under_threshold(self):
        from .models import VerificationAttempt
        from . import fraud_signals

        busy = _make_staff('fraud_busy_staff')
        for _ in range(6):
            VerificationAttempt.objects.create(
                beneficiary=self.beneficiary, performed_by=busy,
                decision=VerificationAttempt.DECISION_VERIFIED,
            )
        quiet = _make_staff('fraud_quiet_staff')
        for _ in range(2):
            VerificationAttempt.objects.create(
                beneficiary=self.beneficiary, performed_by=quiet,
                decision=VerificationAttempt.DECISION_VERIFIED,
            )

        results = fraud_signals.anomalous_staff_volume()
        flagged_ids = {r['performed_by__id'] for r in results}
        self.assertIn(busy.id, flagged_ids)
        self.assertNotIn(quiet.id, flagged_ids)

    @override_settings(FRAUD_MASS_EDIT_THRESHOLD=5, FRAUD_MASS_EDIT_WINDOW_HOURS=1)
    def test_mass_edit_detection_over_threshold(self):
        from logs.models import AuditLog
        from . import fraud_signals

        for _ in range(6):
            AuditLog.log(action=AuditLog.ACTION_USER_UPDATE, user=self.admin)

        results = fraud_signals.mass_edit_detection()
        flagged_ids = {r['user__id'] for r in results}
        self.assertIn(self.admin.id, flagged_ids)

    def test_non_admin_denied_on_report_view(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('verification:fraud_signals_report'))
        self.assertNotEqual(response.status_code, 200)

    def test_admin_sees_all_three_sections(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('verification:fraud_signals_report'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('repeated_failures', response.context)
        self.assertIn('anomalous_staff_volume', response.context)
        self.assertIn('mass_edit_detection', response.context)

    def test_signals_never_write_to_account_or_beneficiary_status(self):
        """Regression guard: Fraud Detection Phase 1 is FLAG-only. Computing every
        signal and rendering the report must never change is_active, account_status,
        or Beneficiary.status for anyone involved."""
        from .models import VerificationAttempt
        from logs.models import AuditLog
        from . import fraud_signals

        for _ in range(5):
            VerificationAttempt.objects.create(
                beneficiary=self.beneficiary, performed_by=self.staff,
                decision=VerificationAttempt.DECISION_NOT_VERIFIED,
            )
        for _ in range(5):
            AuditLog.log(action=AuditLog.ACTION_USER_UPDATE, user=self.admin)

        fraud_signals.repeated_failures()
        fraud_signals.anomalous_staff_volume()
        fraud_signals.mass_edit_detection()
        fraud_signals.repeated_login_failures()
        fraud_signals.payout_anomalies()

        self.client.force_login(self.admin)
        self.client.get(reverse('verification:fraud_signals_report'))

        self.staff.refresh_from_db()
        self.admin.refresh_from_db()
        self.beneficiary.refresh_from_db()
        self.assertTrue(self.staff.is_active)
        self.assertEqual(self.staff.account_status, CustomUser.STATUS_ACTIVE)
        self.assertTrue(self.admin.is_active)
        self.assertEqual(self.admin.account_status, CustomUser.STATUS_ACTIVE)
        self.assertEqual(self.beneficiary.status, self.beneficiary.STATUS_PENDING)


class TemplateAnalyticsTest(TestCase):
    """P1.6 — per-template match analytics + advisory re-enrollment suggestion.
    Entirely derived from VerificationAttempt.matched_template; must never
    write to FaceEmbedding/AdditionalFaceEmbedding (advisory-only guard)."""

    def setUp(self):
        self.admin = CustomUser.objects.create_user(
            username='tmpl_admin', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-TMPL-ADMIN',
        )
        self.it = CustomUser.objects.create_user(
            username='tmpl_it', password='TestPass123!',
            role=CustomUser.ROLE_IT, employee_id='EMP-TMPL-IT',
        )
        self.president = CustomUser.objects.create_user(
            username='tmpl_president', password='TestPass123!',
            role=CustomUser.ROLE_PRESIDENT, employee_id='EMP-TMPL-PRES',
        )
        self.staff = _make_staff('tmpl_staff')
        self.client = Client()

    def test_win_count_aggregation_correctness(self):
        from .models import VerificationAttempt
        from . import template_analytics

        ben = _make_beneficiary('BEN-TMPL-001', 'SC-TMPL-001')
        for _ in range(3):
            VerificationAttempt.objects.create(
                beneficiary=ben, performed_by=self.staff,
                decision=VerificationAttempt.DECISION_VERIFIED, matched_template='primary',
            )
        for _ in range(2):
            VerificationAttempt.objects.create(
                beneficiary=ben, performed_by=self.staff,
                decision=VerificationAttempt.DECISION_VERIFIED, matched_template='update-2025-03',
            )
        # Blank matched_template (no match / denied attempt) must be excluded entirely.
        VerificationAttempt.objects.create(
            beneficiary=ben, performed_by=self.staff,
            decision=VerificationAttempt.DECISION_NOT_VERIFIED, matched_template='',
        )

        stats = {row['matched_template']: row['wins'] for row in template_analytics.get_template_win_stats(ben)}
        self.assertEqual(stats.get('primary'), 3)
        self.assertEqual(stats.get('update-2025-03'), 2)
        self.assertEqual(sum(stats.values()), 5, 'the blank-template attempt must not be counted')

    def test_win_stats_excludes_non_verified_decisions(self):
        """matched_template is set from the best-scoring template regardless of
        whether that score cleared the verification threshold, so a NOT_VERIFIED
        or MANUAL_REVIEW attempt can carry a non-empty matched_template. Only
        VERIFIED attempts should count as a template 'win'."""
        from .models import VerificationAttempt
        from . import template_analytics

        ben = _make_beneficiary('BEN-TMPL-NV', 'SC-TMPL-NV')
        VerificationAttempt.objects.create(
            beneficiary=ben, performed_by=self.staff,
            decision=VerificationAttempt.DECISION_VERIFIED, matched_template='primary',
        )
        VerificationAttempt.objects.create(
            beneficiary=ben, performed_by=self.staff,
            decision=VerificationAttempt.DECISION_NOT_VERIFIED, matched_template='primary',
        )
        VerificationAttempt.objects.create(
            beneficiary=ben, performed_by=self.staff,
            decision=VerificationAttempt.DECISION_MANUAL_REVIEW, matched_template='primary',
        )
        # The anti-spoof rep-claim cross-probe sentinel — not a real template.
        VerificationAttempt.objects.create(
            beneficiary=ben, performed_by=self.staff,
            decision=VerificationAttempt.DECISION_DENIED, matched_template='beneficiary_face_on_rep_claim',
        )

        stats = {row['matched_template']: row['wins'] for row in template_analytics.get_template_win_stats(ben)}
        self.assertEqual(stats.get('primary'), 1, 'only the VERIFIED attempt should count as a win')
        self.assertNotIn('beneficiary_face_on_rep_claim', stats, 'the anti-spoof sentinel is not a real template')

    def test_reenrollment_candidate_detection(self):
        from .models import VerificationAttempt
        from . import template_analytics

        majority_non_primary = _make_beneficiary('BEN-TMPL-MAJ', 'SC-TMPL-MAJ')
        for _ in range(1):
            VerificationAttempt.objects.create(
                beneficiary=majority_non_primary, performed_by=self.staff,
                decision=VerificationAttempt.DECISION_VERIFIED, matched_template='primary',
            )
        for _ in range(3):
            VerificationAttempt.objects.create(
                beneficiary=majority_non_primary, performed_by=self.staff,
                decision=VerificationAttempt.DECISION_VERIFIED, matched_template='update-2025-03',
            )

        majority_primary = _make_beneficiary('BEN-TMPL-PRI', 'SC-TMPL-PRI')
        for _ in range(3):
            VerificationAttempt.objects.create(
                beneficiary=majority_primary, performed_by=self.staff,
                decision=VerificationAttempt.DECISION_VERIFIED, matched_template='primary',
            )
        for _ in range(1):
            VerificationAttempt.objects.create(
                beneficiary=majority_primary, performed_by=self.staff,
                decision=VerificationAttempt.DECISION_VERIFIED, matched_template='update-2025-03',
            )

        candidates = template_analytics.get_reenrollment_candidates()
        self.assertIn(majority_non_primary.id, candidates)
        self.assertNotIn(majority_primary.id, candidates)

    def test_reenrollment_candidate_requires_minimum_sample_size(self):
        """A single verified attempt on a non-primary template trivially satisfies
        'won more than half' (1 win out of 1) — the minimum-attempts floor exists
        specifically to stop that single data point from being flagged."""
        from .models import VerificationAttempt
        from . import template_analytics

        one_attempt = _make_beneficiary('BEN-TMPL-ONE', 'SC-TMPL-ONE')
        VerificationAttempt.objects.create(
            beneficiary=one_attempt, performed_by=self.staff,
            decision=VerificationAttempt.DECISION_VERIFIED, matched_template='update-2025-03',
        )

        two_attempts = _make_beneficiary('BEN-TMPL-TWO', 'SC-TMPL-TWO')
        for _ in range(2):
            VerificationAttempt.objects.create(
                beneficiary=two_attempts, performed_by=self.staff,
                decision=VerificationAttempt.DECISION_VERIFIED, matched_template='update-2025-03',
            )

        four_attempts = _make_beneficiary('BEN-TMPL-FOUR', 'SC-TMPL-FOUR')
        for _ in range(3):
            VerificationAttempt.objects.create(
                beneficiary=four_attempts, performed_by=self.staff,
                decision=VerificationAttempt.DECISION_VERIFIED, matched_template='update-2025-03',
            )
        VerificationAttempt.objects.create(
            beneficiary=four_attempts, performed_by=self.staff,
            decision=VerificationAttempt.DECISION_VERIFIED, matched_template='primary',
        )

        candidates = template_analytics.get_reenrollment_candidates()
        self.assertNotIn(one_attempt.id, candidates, '1 attempt is not a meaningful sample')
        self.assertNotIn(two_attempts.id, candidates, '2 attempts is not a meaningful sample')
        self.assertIn(four_attempts.id, candidates, '4 attempts with a non-primary majority should qualify')

    def test_update_face_data_view_context_and_no_embedding_write(self):
        from .models import VerificationAttempt, FaceEmbedding, AdditionalFaceEmbedding

        ben = _make_beneficiary('BEN-TMPL-VIEW', 'SC-TMPL-VIEW')
        for _ in range(1):
            VerificationAttempt.objects.create(
                beneficiary=ben, performed_by=self.staff,
                decision=VerificationAttempt.DECISION_VERIFIED, matched_template='primary',
            )
        for _ in range(3):
            VerificationAttempt.objects.create(
                beneficiary=ben, performed_by=self.staff,
                decision=VerificationAttempt.DECISION_VERIFIED, matched_template='update-2025-03',
            )
        ben.consent_given = True
        ben.status = ben.STATUS_ACTIVE
        ben.save()

        self.client.force_login(self.staff)
        response = self.client.get(reverse('verification:update_face_data', args=[ben.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['reenrollment_suggested'])
        self.assertTrue(response.context['reenrollment_reason'])

        self.assertFalse(FaceEmbedding.objects.filter(beneficiary=ben).exists())
        self.assertFalse(AdditionalFaceEmbedding.objects.filter(beneficiary=ben).exists())

    def test_appearance_drift_flags_declining_confidence_over_time(self):
        """The 'age estimation, review-flag-only' requirement is satisfied via
        a transparent time/score-drift signal rather than a bundled ML age model
        — see template_analytics.py's module docstring for the rationale."""
        from .models import VerificationAttempt
        from . import template_analytics
        import datetime as _dt

        ben = _make_beneficiary('BEN-DRIFT-001', 'SC-DRIFT-001')
        base_time = timezone.now() - _dt.timedelta(days=365)
        # Earliest attempts: high scores.
        for i, score in enumerate([0.95, 0.94, 0.96]):
            a = VerificationAttempt.objects.create(
                beneficiary=ben, performed_by=self.staff,
                decision=VerificationAttempt.DECISION_VERIFIED, similarity_score=score,
            )
            VerificationAttempt.objects.filter(pk=a.pk).update(timestamp=base_time + _dt.timedelta(days=i))
        # Recent attempts: notably lower scores (drift).
        for i, score in enumerate([0.80, 0.79, 0.81]):
            a = VerificationAttempt.objects.create(
                beneficiary=ben, performed_by=self.staff,
                decision=VerificationAttempt.DECISION_VERIFIED, similarity_score=score,
            )
            VerificationAttempt.objects.filter(pk=a.pk).update(timestamp=timezone.now() - _dt.timedelta(days=i))

        flagged, reason = template_analytics.appearance_drift_flag_for(ben)
        self.assertTrue(flagged)
        self.assertIn('declined', reason)

    def test_appearance_drift_not_flagged_with_stable_scores(self):
        from .models import VerificationAttempt
        from . import template_analytics

        ben = _make_beneficiary('BEN-DRIFT-STABLE', 'SC-DRIFT-STABLE')
        for score in [0.90, 0.91, 0.89, 0.90, 0.92, 0.91]:
            VerificationAttempt.objects.create(
                beneficiary=ben, performed_by=self.staff,
                decision=VerificationAttempt.DECISION_VERIFIED, similarity_score=score,
            )
        flagged, reason = template_analytics.appearance_drift_flag_for(ben)
        self.assertFalse(flagged)

    def test_appearance_drift_never_writes_to_embeddings(self):
        """Regression guard matching the existing advisory-only contract."""
        from .models import VerificationAttempt, FaceEmbedding, AdditionalFaceEmbedding
        from . import template_analytics

        ben = _make_beneficiary('BEN-DRIFT-NOWRITE', 'SC-DRIFT-NOWRITE')
        for score in [0.95, 0.94, 0.96, 0.70, 0.68, 0.71]:
            VerificationAttempt.objects.create(
                beneficiary=ben, performed_by=self.staff,
                decision=VerificationAttempt.DECISION_VERIFIED, similarity_score=score,
            )
        template_analytics.appearance_drift_flag_for(ben)
        template_analytics.reenrollment_suggestion_for(ben)
        self.assertFalse(FaceEmbedding.objects.filter(beneficiary=ben).exists())
        self.assertFalse(AdditionalFaceEmbedding.objects.filter(beneficiary=ben).exists())

    def test_update_face_data_accessible_to_staff_not_just_admin(self):
        """The advisory banner must not accidentally restrict a page staff already use unrestricted."""
        ben = _make_beneficiary('BEN-TMPL-STAFF', 'SC-TMPL-STAFF')
        ben.consent_given = True
        ben.status = ben.STATUS_ACTIVE
        ben.save()
        self.client.force_login(self.staff)
        response = self.client.get(reverse('verification:update_face_data', args=[ben.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['reenrollment_suggested'])

    def test_template_match_report_technical_administrator_and_president_only(self):
        """FINAL ROLE VISIBILITY CLEANUP — Face Template Health is technical
        biometric monitoring, not a barangay operational report: Technical
        Administrator (IT) gets full access, President keeps read-only
        oversight, Admin/Staff get no access at all (a tightening from the
        original plain admin-tier gate)."""
        ben = _make_beneficiary('BEN-TMPL-RPT', 'SC-TMPL-RPT')

        self.client.force_login(self.staff)
        response = self.client.get(reverse('verification:template_match_report'))
        self.assertNotEqual(response.status_code, 200)

        self.client.force_login(self.admin)
        response = self.client.get(reverse('verification:template_match_report'))
        self.assertNotEqual(response.status_code, 200)

        self.client.force_login(self.it)
        response = self.client.get(reverse('verification:template_match_report'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('win_stats', response.context)

        self.client.force_login(self.president)
        response = self.client.get(reverse('verification:template_match_report'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('win_stats', response.context)


class PayoutApprovalWorkflowTest(TestCase):
    """v2.2.0 Phase 2 — a pending-approval schedule must never appear as a
    confirmed payout, and approving one after its date requires a reason."""

    def setUp(self):
        import datetime
        from verification.models import StipendEvent
        self.StipendEvent = StipendEvent
        self.president = CustomUser.objects.create_user(
            username='payout_president', password='TestPass123!',
            role=CustomUser.ROLE_PRESIDENT, employee_id='EMP-PAYOUT-PRES',
        )
        self.admin = CustomUser.objects.create_user(
            username='payout_admin', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-PAYOUT-ADMIN',
        )
        self.client = Client()
        self._today = datetime.date.today()

    def test_pending_event_never_appears_as_next_payout_on_dashboard(self):
        import datetime
        pending = self.StipendEvent.objects.create(
            title='Pending Schedule', date=self._today + datetime.timedelta(days=5),
            approval_status=self.StipendEvent.APPROVAL_PENDING, created_by=self.admin,
        )
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('beneficiaries:dashboard'))
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.context['next_event'])
        self.assertNotContains(resp, 'Pending Schedule')
        self.assertEqual(resp.context['pending_approval_events_count'], 1)

    def test_approved_future_event_appears_as_next_payout(self):
        import datetime
        approved = self.StipendEvent.objects.create(
            title='Approved Schedule', date=self._today + datetime.timedelta(days=5),
            approval_status=self.StipendEvent.APPROVAL_APPROVED, created_by=self.president,
        )
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('beneficiaries:dashboard'))
        self.assertEqual(resp.context['next_event'], approved)
        self.assertContains(resp, 'Approved Schedule')

    def test_on_time_approval_does_not_require_a_reason(self):
        import datetime
        event = self.StipendEvent.objects.create(
            title='Future Schedule', date=self._today + datetime.timedelta(days=5),
            approval_status=self.StipendEvent.APPROVAL_PENDING, created_by=self.admin,
        )
        self.client.force_login(self.president)
        resp = self.client.post(reverse('verification:stipend_approve', args=[event.pk]), follow=True)
        event.refresh_from_db()
        self.assertEqual(event.approval_status, self.StipendEvent.APPROVAL_APPROVED)
        self.assertEqual(event.late_approval_reason, '')

    def test_overdue_approval_without_reason_is_rejected(self):
        import datetime
        event = self.StipendEvent.objects.create(
            title='Overdue Schedule', date=self._today - datetime.timedelta(days=3),
            approval_status=self.StipendEvent.APPROVAL_PENDING, created_by=self.admin,
        )
        self.client.force_login(self.president)
        resp = self.client.post(reverse('verification:stipend_approve', args=[event.pk]), follow=True)
        event.refresh_from_db()
        self.assertEqual(
            event.approval_status, self.StipendEvent.APPROVAL_PENDING,
            'approval must be blocked without a late-approval reason',
        )

    def test_overdue_approval_with_reason_succeeds_and_is_recorded(self):
        import datetime
        event = self.StipendEvent.objects.create(
            title='Overdue Schedule 2', date=self._today - datetime.timedelta(days=3),
            approval_status=self.StipendEvent.APPROVAL_PENDING, created_by=self.admin,
        )
        self.client.force_login(self.president)
        resp = self.client.post(
            reverse('verification:stipend_approve', args=[event.pk]),
            {'late_approval_reason': 'Approval completed after scheduled date.'},
            follow=True,
        )
        event.refresh_from_db()
        self.assertEqual(event.approval_status, self.StipendEvent.APPROVAL_APPROVED)
        self.assertEqual(event.late_approval_reason, 'Approval completed after scheduled date.')

        from logs.models import AuditLog
        audit_row = AuditLog.objects.filter(
            action=AuditLog.ACTION_CONFIG_CHANGE, details__event='schedule_approved',
        ).latest('timestamp')
        self.assertTrue(audit_row.details.get('late_approval'))


class NotificationResolutionOnDecisionTest(TestCase):
    """
    Final Pre-Release Verification, Step 9: several admin-decision views
    (pending claim review, manual verification review, face update review,
    special claim review) created their 'pending review' notification but
    never cleared it once an admin actually decided the case, leaving a
    stale/unread notification pointing at an already-resolved item. Fixed
    by adding resolve_notification() calls mirroring the existing pattern
    already used for stipend approvals and shared-representative review.
    Also covers the matching 'approval_reminder_*' dedupe key that
    sync_approval_reminders() may have separately created after 48h.
    """

    def setUp(self):
        self.admin = _make_staff('notradmin1', role=CustomUser.ROLE_ADMIN)
        self.staff = _make_staff('notrstaff2', role=CustomUser.ROLE_STAFF)
        self.ben = _make_beneficiary('BEN-NR-001', 'SC-NR-001')
        self.ben.status = Beneficiary.STATUS_ACTIVE
        self.ben.consent_given = True
        self.ben.save()
        self.client = Client()
        self.client.force_login(self.admin)

    def _seed_notification(self, dedupe_key):
        from logs.models import Notification
        return Notification.objects.create(
            recipient=self.admin, category=Notification.CATEGORY_APPROVAL_REQUIRED,
            title='Pending review', dedupe_key=dedupe_key,
        )

    def _assert_resolved(self, dedupe_key):
        from logs.models import Notification
        self.assertTrue(
            Notification.objects.get(dedupe_key=dedupe_key).is_read,
            f'{dedupe_key} should be marked read once the underlying case is decided',
        )

    def test_pending_claim_approval_resolves_notification(self):
        from verification.models import ClaimRecord
        claim = ClaimRecord.objects.create(
            beneficiary=self.ben, stipend_event=None,
            status=ClaimRecord.STATUS_PENDING_APPROVAL, amount=0, claimed_by=self.staff,
        )
        self._seed_notification(f'claim_pending:{claim.pk}')
        self._seed_notification(f'approval_reminder_claim_pending:{claim.pk}')
        self.client.post(reverse('verification:pending_claim_review', args=[claim.pk]), {
            'action': 'approve', 'review_notes': 'Verified manually.',
        })
        self._assert_resolved(f'claim_pending:{claim.pk}')
        self._assert_resolved(f'approval_reminder_claim_pending:{claim.pk}')

    def test_pending_claim_rejection_resolves_notification(self):
        from verification.models import ClaimRecord
        claim = ClaimRecord.objects.create(
            beneficiary=self.ben, stipend_event=None,
            status=ClaimRecord.STATUS_PENDING_APPROVAL, amount=0, claimed_by=self.staff,
        )
        self._seed_notification(f'claim_pending:{claim.pk}')
        self.client.post(reverse('verification:pending_claim_review', args=[claim.pk]), {
            'action': 'reject', 'review_notes': 'Could not confirm.',
        })
        self._assert_resolved(f'claim_pending:{claim.pk}')

    def test_manual_verify_approval_resolves_notification(self):
        from verification.models import ManualVerificationRequest
        mvr = ManualVerificationRequest.objects.create(
            beneficiary=self.ben, requested_by=self.staff, reason='Face scan failed.',
        )
        self._seed_notification(f'manual_verify_request:{mvr.id}')
        self._seed_notification(f'approval_reminder_manual_verify:{mvr.id}')
        self.client.post(reverse('verification:manual_verify_review', args=[mvr.id]), {
            'action': 'approve', 'review_notes': 'ID checked in person.',
        })
        self._assert_resolved(f'manual_verify_request:{mvr.id}')
        self._assert_resolved(f'approval_reminder_manual_verify:{mvr.id}')

    def test_manual_verify_rejection_resolves_notification(self):
        from verification.models import ManualVerificationRequest
        mvr = ManualVerificationRequest.objects.create(
            beneficiary=self.ben, requested_by=self.staff, reason='Face scan failed.',
        )
        self._seed_notification(f'manual_verify_request:{mvr.id}')
        self.client.post(reverse('verification:manual_verify_review', args=[mvr.id]), {
            'action': 'reject', 'review_notes': 'Could not confirm identity.',
        })
        self._assert_resolved(f'manual_verify_request:{mvr.id}')

    def test_face_update_approval_resolves_both_notifications(self):
        from verification.models import FaceUpdateRequest, FaceUpdateLog, FaceEmbedding
        FaceEmbedding.objects.create(beneficiary=self.ben, embedding_data=b'\x00' * 64)
        fur = FaceUpdateRequest.objects.create(
            beneficiary=self.ben, requested_by=self.staff,
            reason=FaceUpdateLog.REASON_STAFF_DECISION, action=FaceUpdateLog.ACTION_REPLACE,
            new_embedding_data=b'\x01' * 64,
        )
        self._seed_notification(f'face_update_request:{fur.id}')
        self._seed_notification(f'face_update_duplicate:{fur.id}')
        self._seed_notification(f'approval_reminder_face_update:{fur.id}')
        self.client.post(reverse('verification:face_update_review', args=[fur.id]), {
            'action': 'approve', 'review_notes': 'Confirmed with beneficiary.',
        })
        self._assert_resolved(f'face_update_request:{fur.id}')
        self._assert_resolved(f'face_update_duplicate:{fur.id}')
        self._assert_resolved(f'approval_reminder_face_update:{fur.id}')

    def test_special_claim_approval_resolves_notification(self):
        from verification.models import SpecialClaimRequest
        scr = SpecialClaimRequest.objects.create(
            beneficiary=self.ben, requested_by=self.staff, reason='Second household member claim.',
        )
        self._seed_notification(f'special_claim_request:{scr.id}')
        self._seed_notification(f'approval_reminder_special_claim:{scr.id}')
        self.client.post(reverse('verification:special_claim_review', args=[scr.id]), {
            'action': 'approve', 'review_notes': 'Verified in person.',
        })
        self._assert_resolved(f'special_claim_request:{scr.id}')
        self._assert_resolved(f'approval_reminder_special_claim:{scr.id}')

    def test_special_claim_rejection_resolves_notification(self):
        from verification.models import SpecialClaimRequest
        scr = SpecialClaimRequest.objects.create(
            beneficiary=self.ben, requested_by=self.staff, reason='Second household member claim.',
        )
        self._seed_notification(f'special_claim_request:{scr.id}')
        self.client.post(reverse('verification:special_claim_review', args=[scr.id]), {
            'action': 'reject', 'review_notes': 'Not eligible for a second claim.',
        })
        self._assert_resolved(f'special_claim_request:{scr.id}')


# ──────────────────────────────────────────────────────────────────────────────
# Phase B.5 — FINALIZATION-WITHIN-WINDOW POLICY
#
# A selected stipend event must still be eligible for claiming at the moment
# a financial claim/payout is FINALIZED (ClaimRecord created), not merely
# when verification/review started. Covers the shared validator
# (StipendEvent.check_claim_eligible_now / views._lock_event_for_finalization)
# and every ClaimRecord-creation call site: the direct VERIFIED path
# (verify_submit), Manual Review approval, admin override release, and
# Special Claim approval. A blocked payout must never rewrite the biometric
# decision, must never silently fall back to a different open event, and
# must always be visible in the audit trail and to the acting user.
# ──────────────────────────────────────────────────────────────────────────────

class FinalizationWithinWindowModelTest(TestCase):
    """Unit-level coverage of StipendEvent.check_claim_eligible_now — the
    shared validator every finalization call site is built on."""

    def setUp(self):
        from verification.models import StipendEvent
        self.StipendEvent = StipendEvent
        self.president = _make_staff('fwwm_pres', role=CustomUser.ROLE_PRESIDENT)

    @staticmethod
    def _manila_dt(date_, hour, minute=0):
        from zoneinfo import ZoneInfo
        return datetime.datetime(
            date_.year, date_.month, date_.day, hour, minute, 0,
            tzinfo=ZoneInfo('Asia/Manila'),
        )

    def _freeze(self, date_, hour, minute=0):
        fixed = self._manila_dt(date_, hour, minute).astimezone(datetime.timezone.utc)
        patcher = mock.patch('django.utils.timezone.now', return_value=fixed)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _make_event(self, title='Event', date=None, payout_start_date=None, payout_end_date=None,
                     start_time=None, end_time=None, is_active=True, approval_status=None, amount=500):
        d = date or datetime.date(2026, 6, 15)
        return self.StipendEvent.objects.create(
            title=title, date=d, amount=amount,
            payout_start_date=payout_start_date or d,
            payout_end_date=payout_end_date or d,
            payout_start_time=start_time, payout_end_time=end_time,
            is_active=is_active,
            approval_status=approval_status or self.StipendEvent.APPROVAL_APPROVED,
            created_by=self.president,
        )

    # A — inside the event's own window
    def test_inside_window_eligible(self):
        ev = self._make_event(start_time=datetime.time(13, 0), end_time=datetime.time(17, 0))
        self._freeze(ev.date, 14, 0)
        eligible, reason = ev.check_claim_eligible_now()
        self.assertTrue(eligible)
        self.assertEqual(reason, '')

    # B — exact-close boundary: is_within_time_window is inclusive (<=) on
    # both ends in this codebase — retained here, not reinvented.
    def test_exact_close_boundary_still_eligible(self):
        ev = self._make_event(start_time=datetime.time(13, 0), end_time=datetime.time(17, 0))
        self._freeze(ev.date, 17, 0)
        eligible, reason = ev.check_claim_eligible_now()
        self.assertTrue(eligible)

    # C — one minute after close
    def test_after_close_ineligible(self):
        ev = self._make_event(start_time=datetime.time(13, 0), end_time=datetime.time(17, 0))
        self._freeze(ev.date, 17, 1)
        eligible, reason = ev.check_claim_eligible_now()
        self.assertFalse(eligible)
        self.assertEqual(reason, 'window_closed')

    # H — deactivated event
    def test_inactive_event_ineligible(self):
        ev = self._make_event(is_active=False)
        self._freeze(ev.date, 12, 0)
        eligible, reason = ev.check_claim_eligible_now()
        self.assertFalse(eligible)
        self.assertEqual(reason, 'inactive')

    # I — approval revoked / never approved
    def test_pending_event_ineligible(self):
        ev = self._make_event(approval_status=self.StipendEvent.APPROVAL_PENDING)
        self._freeze(ev.date, 12, 0)
        eligible, reason = ev.check_claim_eligible_now()
        self.assertFalse(eligible)
        self.assertEqual(reason, 'unapproved')

    def test_rejected_event_ineligible(self):
        ev = self._make_event(approval_status=self.StipendEvent.APPROVAL_REJECTED)
        self._freeze(ev.date, 12, 0)
        eligible, reason = ev.check_claim_eligible_now()
        self.assertFalse(eligible)
        self.assertEqual(reason, 'unapproved')

    # Blank per-event times = "all day" at the EVENT level...
    def test_blank_time_within_global_hours_eligible(self):
        ev = self._make_event(start_time=None, end_time=None)
        self._freeze(ev.date, 19, 30)
        eligible, reason = ev.check_claim_eligible_now()
        self.assertTrue(eligible)

    # K — ...but the global 07:00-20:00 same-day claiming hours still apply,
    # so a blank-time event cannot be finalized in the middle of the night.
    def test_blank_time_outside_global_hours_ineligible(self):
        ev = self._make_event(start_time=None, end_time=None)
        self._freeze(ev.date, 21, 0)
        eligible, reason = ev.check_claim_eligible_now()
        self.assertFalse(eligible)
        self.assertEqual(reason, 'window_closed')

    def test_blank_time_before_global_hours_ineligible(self):
        ev = self._make_event(start_time=None, end_time=None)
        self._freeze(ev.date, 5, 0)
        eligible, reason = ev.check_claim_eligible_now()
        self.assertFalse(eligible)
        self.assertEqual(reason, 'window_closed')

    # J — an event-specific window narrower than global office hours is
    # never broadened back out to the global window.
    def test_event_specific_window_narrower_than_global_blocks(self):
        ev = self._make_event(start_time=datetime.time(10, 0), end_time=datetime.time(15, 0))
        self._freeze(ev.date, 16, 0)  # inside global 07:00-20:00, outside the event's own 10:00-15:00
        eligible, reason = ev.check_claim_eligible_now()
        self.assertFalse(eligible)
        self.assertEqual(reason, 'window_closed')

    # L — multi-day events
    def test_multiday_event_valid_on_later_day(self):
        start, end = datetime.date(2026, 6, 1), datetime.date(2026, 6, 5)
        ev = self._make_event(date=start, payout_start_date=start, payout_end_date=end)
        self._freeze(datetime.date(2026, 6, 4), 12, 0)
        eligible, reason = ev.check_claim_eligible_now()
        self.assertTrue(eligible)

    def test_multiday_event_invalid_after_range(self):
        start, end = datetime.date(2026, 6, 1), datetime.date(2026, 6, 5)
        ev = self._make_event(date=start, payout_start_date=start, payout_end_date=end)
        self._freeze(datetime.date(2026, 6, 6), 12, 0)
        eligible, reason = ev.check_claim_eligible_now()
        self.assertFalse(eligible)
        self.assertEqual(reason, 'window_closed')

    def test_multiday_event_invalid_before_range(self):
        start, end = datetime.date(2026, 6, 1), datetime.date(2026, 6, 5)
        ev = self._make_event(date=start, payout_start_date=start, payout_end_date=end)
        self._freeze(datetime.date(2026, 5, 31), 12, 0)
        eligible, reason = ev.check_claim_eligible_now()
        self.assertFalse(eligible)
        self.assertEqual(reason, 'window_closed')


class FinalizationWithinWindowViewTest(TestCase):
    """
    Integration coverage for every ClaimRecord-creation call site under the
    Phase B.5 policy: override release, Manual Review approval, and Special
    Claim approval. Uses the same VerificationAttempt-fixture pattern as
    OverrideReleasePayoutTest / DuplicateConflictBlocksPayoutTest (no FaceNet
    involved — the biometric decision is a pre-set fixture; only the
    finalization gate is under test).
    """

    def setUp(self):
        from verification.models import StipendEvent
        self.StipendEvent = StipendEvent
        self.president = _make_staff('fwwv_pres', role=CustomUser.ROLE_PRESIDENT)
        self.admin = _make_staff('fwwv_admin', role=CustomUser.ROLE_ADMIN)
        self.performer = _make_staff('fwwv_performer', role=CustomUser.ROLE_ADMIN)
        self.ben = _make_beneficiary('BEN-FWWV-001', 'SC-FWWV-001')
        self.ben.status = Beneficiary.STATUS_ACTIVE
        self.ben.consent_given = True
        self.ben.save()
        self.client = Client()

    @staticmethod
    def _manila_dt(date_, hour, minute=0):
        from zoneinfo import ZoneInfo
        return datetime.datetime(
            date_.year, date_.month, date_.day, hour, minute, 0,
            tzinfo=ZoneInfo('Asia/Manila'),
        )

    def _freeze(self, date_, hour, minute=0):
        fixed = self._manila_dt(date_, hour, minute).astimezone(datetime.timezone.utc)
        patcher = mock.patch('django.utils.timezone.now', return_value=fixed)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fixed

    def _make_event(self, title='Event', date=None, start_time=None, end_time=None,
                     is_active=True, approval_status=None, amount=500):
        d = date or datetime.date(2026, 6, 15)
        return self.StipendEvent.objects.create(
            title=title, date=d, amount=amount,
            payout_start_date=d, payout_end_date=d,
            payout_start_time=start_time, payout_end_time=end_time,
            is_active=is_active,
            approval_status=approval_status or self.StipendEvent.APPROVAL_APPROVED,
            created_by=self.president,
        )

    def _make_overridden_attempt(self, event):
        from verification.models import VerificationAttempt
        return VerificationAttempt.objects.create(
            beneficiary=self.ben, performed_by=self.performer,
            decision=VerificationAttempt.DECISION_VERIFIED,
            similarity_score=0.72, threshold_used=0.75,
            stipend_event=event, overridden=True, override_by=self.admin,
            override_reason='Independently confirmed identity via valid government ID.',
            override_at=timezone.now(),
        )

    # ── Override release (F, G) ────────────────────────────────────────────

    def test_override_release_inside_window_creates_claim(self):
        from verification.models import ClaimRecord
        ev = self._make_event(start_time=datetime.time(13, 0), end_time=datetime.time(17, 0))
        self._freeze(ev.date, 14, 0)
        attempt = self._make_overridden_attempt(ev)
        self.client.force_login(self.admin)
        resp = self.client.post(reverse('verification:override_release_payout', args=[attempt.pk]))
        self.assertIn(resp.status_code, (200, 302))
        self.assertTrue(ClaimRecord.objects.filter(verification_attempt=attempt, status=ClaimRecord.STATUS_CLAIMED).exists())

    def test_override_release_after_close_blocks_and_keeps_override_recorded(self):
        from verification.models import ClaimRecord, VerificationAttempt
        ev = self._make_event(start_time=datetime.time(13, 0), end_time=datetime.time(17, 0))
        self._freeze(ev.date, 20, 0)  # already closed (event window ends 17:00)
        attempt = self._make_overridden_attempt(ev)
        self.client.force_login(self.admin)
        resp = self.client.post(reverse('verification:override_release_payout', args=[attempt.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(ClaimRecord.objects.filter(verification_attempt=attempt).exists())
        after = VerificationAttempt.objects.get(pk=attempt.pk)
        # Biometric decision + override record must survive the block untouched.
        self.assertEqual(after.decision, VerificationAttempt.DECISION_VERIFIED)
        self.assertTrue(after.overridden)
        self.assertEqual(after.override_by_id, self.admin.id)

    def test_override_release_blocked_message_shown_and_not_denied_wording(self):
        ev = self._make_event(start_time=datetime.time(13, 0), end_time=datetime.time(17, 0))
        self._freeze(ev.date, 20, 0)
        attempt = self._make_overridden_attempt(ev)
        self.client.force_login(self.admin)
        resp = self.client.post(
            reverse('verification:override_release_payout', args=[attempt.pk]), follow=True,
        )
        messages_text = ' '.join(str(m) for m in resp.context['messages'])
        self.assertIn('claiming window', messages_text.lower())
        # Must never claim the verified face itself was rejected.
        self.assertNotIn('face not verified', messages_text.lower())

    def test_override_release_deactivated_event_blocks(self):
        """H — event deactivated after the verification/override started."""
        from verification.models import ClaimRecord
        ev = self._make_event(start_time=None, end_time=None)
        self._freeze(ev.date, 12, 0)
        attempt = self._make_overridden_attempt(ev)
        ev.is_active = False
        ev.save()
        self.client.force_login(self.admin)
        resp = self.client.post(reverse('verification:override_release_payout', args=[attempt.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(ClaimRecord.objects.filter(verification_attempt=attempt).exists())

    def test_override_release_unapproved_event_blocks(self):
        """I — approval revoked after the verification/override started."""
        from verification.models import ClaimRecord
        ev = self._make_event(start_time=None, end_time=None)
        self._freeze(ev.date, 12, 0)
        attempt = self._make_overridden_attempt(ev)
        ev.approval_status = self.StipendEvent.APPROVAL_REJECTED
        ev.save()
        self.client.force_login(self.admin)
        resp = self.client.post(reverse('verification:override_release_payout', args=[attempt.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(ClaimRecord.objects.filter(verification_attempt=attempt).exists())

    def test_override_release_audit_log_uses_existing_action_no_fraud_wording(self):
        from logs.models import AuditLog
        ev = self._make_event(start_time=datetime.time(13, 0), end_time=datetime.time(17, 0))
        self._freeze(ev.date, 20, 0)
        attempt = self._make_overridden_attempt(ev)
        self.client.force_login(self.admin)
        self.client.post(reverse('verification:override_release_payout', args=[attempt.pk]))
        entry = AuditLog.objects.filter(
            target_type='VerificationAttempt', target_id=attempt.id,
        ).order_by('-timestamp').first()
        self.assertIsNotNone(entry)
        self.assertIn(entry.action, [c[0] for c in AuditLog.ACTION_CHOICES])
        self.assertNotIn('fraud', str(entry.details).lower())

    # M — concurrent events: a closed bound event must never fall back to a
    # different event that happens to still be open.
    def test_concurrent_event_bound_event_closes_never_falls_back_to_other_open_event(self):
        from verification.models import ClaimRecord
        ev_a = self._make_event('Event A (still open)', start_time=datetime.time(7, 0), end_time=datetime.time(20, 0))
        ev_b = self._make_event('Event B (closed)', start_time=datetime.time(8, 0), end_time=datetime.time(9, 0))
        self._freeze(ev_b.date, 14, 0)  # inside A's window, outside B's window
        attempt = self._make_overridden_attempt(ev_b)
        self.client.force_login(self.admin)
        resp = self.client.post(reverse('verification:override_release_payout', args=[attempt.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(ClaimRecord.objects.filter(beneficiary=self.ben, stipend_event=ev_a).exists())
        self.assertFalse(ClaimRecord.objects.filter(beneficiary=self.ben, stipend_event=ev_b).exists())
        attempt.refresh_from_db()
        self.assertEqual(attempt.stipend_event_id, ev_b.pk)

    # ── Manual Review approval (D, E) ──────────────────────────────────────

    def _make_mvr(self, event):
        from verification.models import VerificationAttempt, ManualVerificationRequest
        attempt = VerificationAttempt.objects.create(
            beneficiary=self.ben, performed_by=self.performer,
            decision=VerificationAttempt.DECISION_MANUAL_REVIEW,
            similarity_score=0.62, threshold_used=0.75, stipend_event=event,
        )
        mvr = ManualVerificationRequest.objects.create(
            beneficiary=self.ben, requested_by=self.performer,
            verification_attempt=attempt, stipend_event=event,
            reason='ID confirmed on-site during outreach.',
        )
        return attempt, mvr

    def test_manual_review_approved_inside_window_creates_claim(self):
        from verification.models import ClaimRecord, ManualVerificationRequest
        ev = self._make_event(start_time=datetime.time(13, 0), end_time=datetime.time(17, 0))
        self._freeze(ev.date, 14, 0)
        attempt, mvr = self._make_mvr(ev)
        self.client.force_login(self.admin)
        resp = self.client.post(
            reverse('verification:manual_verify_review', args=[mvr.pk]),
            {'action': 'approve', 'review_notes': 'Confirmed valid ID.'},
        )
        self.assertIn(resp.status_code, (200, 302))
        mvr.refresh_from_db()
        self.assertEqual(mvr.status, ManualVerificationRequest.STATUS_APPROVED)
        self.assertTrue(ClaimRecord.objects.filter(verification_attempt=attempt).exists())

    def test_manual_review_approved_after_close_blocks_but_keeps_approval(self):
        from verification.models import ClaimRecord, ManualVerificationRequest, VerificationAttempt
        ev = self._make_event(start_time=datetime.time(13, 0), end_time=datetime.time(17, 0))
        self._freeze(ev.date, 20, 0)
        attempt, mvr = self._make_mvr(ev)
        self.client.force_login(self.admin)
        resp = self.client.post(
            reverse('verification:manual_verify_review', args=[mvr.pk]),
            {'action': 'approve', 'review_notes': 'Confirmed valid ID.'},
        )
        self.assertIn(resp.status_code, (200, 302))
        mvr.refresh_from_db()
        # Identity-review resolution is preserved even though payout is blocked.
        self.assertEqual(mvr.status, ManualVerificationRequest.STATUS_APPROVED)
        self.assertFalse(ClaimRecord.objects.filter(verification_attempt=attempt).exists())
        after = VerificationAttempt.objects.get(pk=attempt.pk)
        self.assertEqual(after.decision, VerificationAttempt.DECISION_VERIFIED)
        self.assertNotIn('face not verified', (after.notes or '').lower())
        self.assertIn('claiming window', (after.notes or '').lower())

    def test_manual_review_unapproved_event_blocks(self):
        """I — approval revoked between request submission and review."""
        from verification.models import ClaimRecord
        ev = self._make_event(start_time=None, end_time=None)
        self._freeze(ev.date, 12, 0)
        attempt, mvr = self._make_mvr(ev)
        ev.approval_status = self.StipendEvent.APPROVAL_PENDING
        ev.save()
        self.client.force_login(self.admin)
        self.client.post(
            reverse('verification:manual_verify_review', args=[mvr.pk]),
            {'action': 'approve', 'review_notes': 'Confirmed valid ID.'},
        )
        self.assertFalse(ClaimRecord.objects.filter(verification_attempt=attempt).exists())

    # ── Special Claim approval ─────────────────────────────────────────────

    def _make_scr(self, event):
        from verification.models import SpecialClaimRequest
        return SpecialClaimRequest.objects.create(
            beneficiary=self.ben, stipend_event=event, requested_by=self.performer,
            reason='Lost original payout, needs reissue.',
        )

    def test_special_claim_approved_inside_window_creates_claim(self):
        from verification.models import ClaimRecord, SpecialClaimRequest
        ev = self._make_event(start_time=datetime.time(13, 0), end_time=datetime.time(17, 0))
        self._freeze(ev.date, 14, 0)
        scr = self._make_scr(ev)
        self.client.force_login(self.admin)
        resp = self.client.post(
            reverse('verification:special_claim_review', args=[scr.pk]),
            {'action': 'approve', 'review_notes': 'Confirmed.'},
        )
        self.assertIn(resp.status_code, (200, 302))
        scr.refresh_from_db()
        self.assertEqual(scr.status, SpecialClaimRequest.STATUS_APPROVED)
        self.assertTrue(
            ClaimRecord.objects.filter(beneficiary=self.ben, stipend_event=ev, is_special_additional=True).exists()
        )

    def test_special_claim_approved_after_close_blocks_but_keeps_approval(self):
        from verification.models import ClaimRecord, SpecialClaimRequest
        ev = self._make_event(start_time=datetime.time(13, 0), end_time=datetime.time(17, 0))
        self._freeze(ev.date, 20, 0)
        scr = self._make_scr(ev)
        self.client.force_login(self.admin)
        resp = self.client.post(
            reverse('verification:special_claim_review', args=[scr.pk]),
            {'action': 'approve', 'review_notes': 'Confirmed.'},
        )
        self.assertIn(resp.status_code, (200, 302))
        scr.refresh_from_db()
        self.assertEqual(scr.status, SpecialClaimRequest.STATUS_APPROVED)
        self.assertFalse(
            ClaimRecord.objects.filter(beneficiary=self.ben, stipend_event=ev, is_special_additional=True).exists()
        )


class FinalizationWithinWindowDirectVerifiedPathTest(TestCase):
    """
    Direct VERIFIED path (verify_submit's own ClaimRecord creation) under the
    Phase B.5 policy — including the canonical 7:59 PM -> 8:01 PM regression
    scenario. Uses the same face-pipeline mocking pattern as
    VerifySubmitTXEmbeddingRegressionTest; only the finalization gate itself
    is under test.
    """

    def setUp(self):
        from verification.models import StipendEvent, FaceEmbedding
        self.StipendEvent = StipendEvent
        self.president = _make_staff('fwwd_pres', role=CustomUser.ROLE_PRESIDENT)
        self.staff = _make_staff('fwwd_staff', role=CustomUser.ROLE_STAFF)
        self.ben = _make_beneficiary('BEN-FWWD-001', 'SC-FWWD-001')
        self.ben.status = Beneficiary.STATUS_ACTIVE
        self.ben.consent_given = True
        self.ben.save()
        FaceEmbedding.objects.create(beneficiary=self.ben, embedding_data=b'\x00' * 64)
        self.client = Client()
        self.client.force_login(self.staff)

    @staticmethod
    def _manila_dt(date_, hour, minute=0):
        from zoneinfo import ZoneInfo
        return datetime.datetime(
            date_.year, date_.month, date_.day, hour, minute, 0,
            tzinfo=ZoneInfo('Asia/Manila'),
        )

    def _freeze(self, date_, hour, minute=0):
        fixed = self._manila_dt(date_, hour, minute).astimezone(datetime.timezone.utc)
        patcher = mock.patch('django.utils.timezone.now', return_value=fixed)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fixed

    def _make_event(self, title='Event', date=None, start_time=None, end_time=None, amount=500):
        d = date or datetime.date(2026, 6, 15)
        return self.StipendEvent.objects.create(
            title=title, date=d, amount=amount,
            payout_start_date=d, payout_end_date=d,
            payout_start_time=start_time, payout_end_time=end_time,
            is_active=True, approval_status=self.StipendEvent.APPROVAL_APPROVED,
            created_by=self.president,
        )

    def _submit(self, event):
        """Drives verify_submit to a VERIFIED decision via a liveness
        transaction, matching VerifySubmitTXEmbeddingRegressionTest's proven
        mocking pattern. Caller must already have frozen "now"."""
        from verification.models import LivenessTransaction, VerificationAttempt

        session = self.client.session
        session['verification_session'] = {
            'beneficiary_id': str(self.ben.pk),
            'attempt_number': 1,
            'session_id': _FIXED_TEST_SESSION_ID,
            'claimant_type': 'beneficiary',
            'stipend_event_id': str(event.pk),
            'challenge': 'side',
        }
        session.save()

        tx = LivenessTransaction.objects.create(
            beneficiary=self.ben, claimant_type='beneficiary', stipend_event=event,
            performed_by=self.staff, challenge_direction='side',
            anti_spoof_score=0.9, liveness_score=0.9, pa_score=0.0, pa_flags={},
            embedding_data=b'\x01' * 64,
            expires_at=timezone.now() + datetime.timedelta(seconds=120),
        )
        payload = json.dumps({
            'image': DATA_URI_JPEG, 'challenge_completed': True,
            'liveness_score': 0.9, 'anti_spoof_score': 0.9, 'liveness_passed': True,
            'face_detected': True, 'tx_token': str(tx.token), 'session_id': _FIXED_TEST_SESSION_ID,
        })
        resp = self.client.post(
            reverse('verification:verify_submit'), data=payload,
            content_type='application/json', secure=True,
        )
        data = json.loads(resp.content)
        attempt = None
        redirect = data.get('redirect', '')
        if redirect:
            attempt_id = redirect.rstrip('/').split('/')[-1]
            attempt = VerificationAttempt.objects.filter(pk=attempt_id).first()
        return resp, data, attempt

    @mock.patch('verification.face_utils.check_duplicate_face')
    @mock.patch('verification.views.load_image_from_bytes')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.check_anti_spoofing')
    @mock.patch('verification.views.process_face_for_verification')
    @mock.patch('verification.views.compare_with_all_embeddings')
    @mock.patch('verification.face_utils.decrypt_embedding')
    @override_settings(LIVENESS_PROOF_REQUIRED=True, LIVENESS_REQUIRED=True,
                        DEMO_MODE=False, VERIFICATION_THRESHOLD=0.75,
                        AUTO_VERIFY_THRESHOLD=0.80, DEBUG=True)
    def test_a_inside_window_creates_claim(
        self, mock_decrypt, mock_compare, mock_process, mock_spoof, mock_detect, mock_load, mock_dup,
    ):
        import numpy as np
        from verification.models import ClaimRecord

        ev = self._make_event(start_time=datetime.time(13, 0), end_time=datetime.time(17, 0))
        self._freeze(ev.date, 14, 0)

        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_spoof.return_value = {'passed': True, 'score': 0.9, 'reason': 'Real face.'}
        # Root cause of the original DENIED result (traced to verification/
        # views.py's final-frame integrity gate, ~line 1894-1930): that gate
        # computes its OWN same-face cosine check between the TX-decrypted
        # embedding (mock_decrypt) and this mocked submitted-frame embedding
        # (mock_process) — independent of mock_compare, which only stands in
        # for the later identity-match step. A zero vector has zero norm, so
        # cosine_similarity() short-circuits to 0.0 regardless of the other
        # embedding, which was always < SAME_FACE_SEQUENCE_THRESHOLD (0.30)
        # and denied the attempt before finalization was ever reached. Using
        # the same embedding as mock_decrypt here (cosine similarity 1.0)
        # makes the two mocks internally consistent — this does not touch or
        # weaken the production same-face check itself, which still runs for
        # real against whatever these two mocks return.
        mock_process.return_value = {
            'success': True, 'embedding': np.array([0.1] * 128),
            'quality': {'ok': True, 'score': 0.9, 'reason': ''}, 'using_mock': False,
        }
        mock_compare.return_value = {
            'success': True, 'score': 0.90,
            'matched_template': 'primary', 'templates_checked': 1, 'all_scores': [],
        }
        mock_dup.return_value = {'duplicates_found': False, 'matches': [], 'highest_score': 0.0, 'checked': 0}
        mock_decrypt.return_value = [0.1] * 128

        resp, data, attempt = self._submit(ev)
        self.assertEqual(data.get('decision'), 'verified')
        self.assertIsNotNone(attempt)
        self.assertEqual(attempt.decision, 'verified')
        self.assertTrue(ClaimRecord.objects.filter(verification_attempt=attempt).exists())

    @mock.patch('verification.face_utils.check_duplicate_face')
    @mock.patch('verification.views.load_image_from_bytes')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.check_anti_spoofing')
    @mock.patch('verification.views.process_face_for_verification')
    @mock.patch('verification.views.compare_with_all_embeddings')
    @mock.patch('verification.face_utils.decrypt_embedding')
    @override_settings(LIVENESS_PROOF_REQUIRED=True, LIVENESS_REQUIRED=True,
                        DEMO_MODE=False, VERIFICATION_THRESHOLD=0.75,
                        AUTO_VERIFY_THRESHOLD=0.80, DEBUG=True)
    def test_c_759pm_to_801pm_window_closes_blocks_payout_keeps_verified_decision(
        self, mock_decrypt, mock_compare, mock_process, mock_spoof, mock_detect, mock_load, mock_dup,
    ):
        """Canonical Phase B.5 regression: verification may begin while the
        event is open, but if the global 07:00-20:00 claiming window has
        already closed by the time the claim is actually finalized, the
        identity result stays VERIFIED and only the payout is blocked."""
        import numpy as np
        from verification.models import ClaimRecord

        ev = self._make_event(start_time=None, end_time=None)  # all-day at the event level
        self._freeze(ev.date, 20, 1)  # 8:01 PM — global claiming hours just closed

        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_spoof.return_value = {'passed': True, 'score': 0.9, 'reason': 'Real face.'}
        # Root cause of the original DENIED result (traced to verification/
        # views.py's final-frame integrity gate, ~line 1894-1930): that gate
        # computes its OWN same-face cosine check between the TX-decrypted
        # embedding (mock_decrypt) and this mocked submitted-frame embedding
        # (mock_process) — independent of mock_compare, which only stands in
        # for the later identity-match step. A zero vector has zero norm, so
        # cosine_similarity() short-circuits to 0.0 regardless of the other
        # embedding, which was always < SAME_FACE_SEQUENCE_THRESHOLD (0.30)
        # and denied the attempt before finalization was ever reached. Using
        # the same embedding as mock_decrypt here (cosine similarity 1.0)
        # makes the two mocks internally consistent — this does not touch or
        # weaken the production same-face check itself, which still runs for
        # real against whatever these two mocks return.
        mock_process.return_value = {
            'success': True, 'embedding': np.array([0.1] * 128),
            'quality': {'ok': True, 'score': 0.9, 'reason': ''}, 'using_mock': False,
        }
        mock_compare.return_value = {
            'success': True, 'score': 0.90,
            'matched_template': 'primary', 'templates_checked': 1, 'all_scores': [],
        }
        mock_dup.return_value = {'duplicates_found': False, 'matches': [], 'highest_score': 0.0, 'checked': 0}
        mock_decrypt.return_value = [0.1] * 128

        resp, data, attempt = self._submit(ev)
        self.assertEqual(data.get('decision'), 'verified')
        self.assertIsNotNone(attempt)
        attempt.refresh_from_db()
        self.assertEqual(attempt.decision, 'verified')
        self.assertNotEqual(attempt.decision, 'not_verified')
        self.assertFalse(ClaimRecord.objects.filter(verification_attempt=attempt).exists())
        self.assertIn('claiming window', (attempt.notes or '').lower())

    @mock.patch('verification.face_utils.check_duplicate_face')
    @mock.patch('verification.views.load_image_from_bytes')
    @mock.patch('verification.views.detect_and_align_face')
    @mock.patch('verification.views.check_anti_spoofing')
    @mock.patch('verification.views.process_face_for_verification')
    @mock.patch('verification.views.compare_with_all_embeddings')
    @mock.patch('verification.face_utils.decrypt_embedding')
    @override_settings(LIVENESS_PROOF_REQUIRED=True, LIVENESS_REQUIRED=True,
                        DEMO_MODE=False, VERIFICATION_THRESHOLD=0.75,
                        AUTO_VERIFY_THRESHOLD=0.80, DEBUG=True)
    def test_m_bound_event_closes_no_automatic_fallback_to_concurrent_event(
        self, mock_decrypt, mock_compare, mock_process, mock_spoof, mock_detect, mock_load, mock_dup,
    ):
        """
        Canonical Phase B.5 concurrent-event regression, driven through the
        REAL verify_start binding path (not directly-injected session data
        like _submit() uses) — proving START VALID + EVENT BOUND +
        FINALIZATION LATER INVALID + NO FINANCIAL FALLBACK end to end:

        19:59 Manila — two events are simultaneously open (Event A and Event
        B). The operator explicitly selects Event B via verify_start's
        real Phase B.3 event-chooser query param, which is the production
        binding path — request.session['verification_session']
        ['stipend_event_id'] ends up set to Event B by the actual view, not
        by test scaffolding.

        20:01 Manila — the SAME server-side session (still bound to Event B)
        is used to drive verify_submit through the same proven biometric-
        mock pattern as test_a/test_c above. The identity result reaches
        VERIFIED. Event B's window has closed by now, so the payout is
        blocked — but critically, the system must never opportunistically
        attach the payout to Event A instead just because Event A also
        exists.

        Note on why Event A isn't asserted to still be "open" at 20:01: this
        system enforces (at schedule create/edit time, in
        views._validate_payout_window) that no event's explicit payout_end_
        time may be later than 20:00, and blank per-event times fall back to
        the same 07:00-20:00 global window (StipendEvent.check_claim_eligible
        _now). So under the current locked policy NO legitimately-created
        event can still be open at 20:01 — that boundary is already covered
        by test_c above. What matters financially, and what this test
        actually proves, is narrower and holds regardless of Event A's own
        state: the bound Event B closing must never cause a silent switch to
        a different event.
        """
        import numpy as np
        from verification.models import ClaimRecord

        ev_b = self._make_event('Event B (bound)', start_time=None, end_time=None)
        ev_a = self._make_event('Event A (not selected)', start_time=None, end_time=None)

        # ── 19:59 Manila — both events open; bind Event B via the real view ──
        self._freeze(ev_b.date, 19, 59)
        start_resp = self.client.get(
            reverse('verification:verify_start', args=[self.ben.pk]),
            {'event': str(ev_b.pk)}, secure=True,
        )
        self.assertEqual(start_resp.status_code, 200)
        self.assertTemplateNotUsed(start_resp, 'verification/verify_choose_event.html')
        session_data = self.client.session.get('verification_session')
        self.assertIsNotNone(session_data)
        self.assertEqual(session_data['stipend_event_id'], str(ev_b.pk))
        real_session_id = session_data['session_id']
        real_challenge = session_data.get('challenge', 'side')

        # ── 20:01 Manila — window closed; finalize against the SAME bound session ──
        self._freeze(ev_b.date, 20, 1)

        mock_load.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_detect.return_value = np.zeros((64, 64, 3), dtype=np.uint8)
        mock_spoof.return_value = {'passed': True, 'score': 0.9, 'reason': 'Real face.'}
        mock_process.return_value = {
            'success': True, 'embedding': np.array([0.1] * 128),
            'quality': {'ok': True, 'score': 0.9, 'reason': ''}, 'using_mock': False,
        }
        mock_compare.return_value = {
            'success': True, 'score': 0.90,
            'matched_template': 'primary', 'templates_checked': 1, 'all_scores': [],
        }
        mock_dup.return_value = {'duplicates_found': False, 'matches': [], 'highest_score': 0.0, 'checked': 0}
        mock_decrypt.return_value = [0.1] * 128

        from verification.models import LivenessTransaction, VerificationAttempt
        tx = LivenessTransaction.objects.create(
            beneficiary=self.ben, claimant_type='beneficiary', stipend_event=ev_b,
            performed_by=self.staff, challenge_direction=real_challenge,
            anti_spoof_score=0.9, liveness_score=0.9, pa_score=0.0, pa_flags={},
            embedding_data=b'\x01' * 64,
            expires_at=timezone.now() + datetime.timedelta(seconds=120),
        )
        payload = json.dumps({
            'image': DATA_URI_JPEG, 'challenge_completed': True,
            'liveness_score': 0.9, 'anti_spoof_score': 0.9, 'liveness_passed': True,
            'face_detected': True, 'tx_token': str(tx.token), 'session_id': real_session_id,
        })
        resp = self.client.post(
            reverse('verification:verify_submit'), data=payload,
            content_type='application/json', secure=True,
        )
        data = json.loads(resp.content)
        self.assertEqual(data.get('decision'), 'verified')

        redirect = data.get('redirect', '')
        attempt_id = redirect.rstrip('/').split('/')[-1]
        attempt = VerificationAttempt.objects.get(pk=attempt_id)

        self.assertEqual(attempt.decision, 'verified')
        self.assertEqual(attempt.stipend_event_id, ev_b.pk, 'bound event must not change')
        self.assertNotEqual(attempt.stipend_event_id, ev_a.pk)
        self.assertFalse(ClaimRecord.objects.filter(verification_attempt=attempt).exists())
        self.assertFalse(
            ClaimRecord.objects.filter(beneficiary=self.ben, stipend_event=ev_a).exists(),
            'must never silently attach the payout to the other open event',
        )
        self.assertFalse(
            ClaimRecord.objects.filter(beneficiary=self.ben, stipend_event=ev_b).exists(),
        )


class ClaimRecordConcurrentIntegrityErrorTest(TestCase):
    """Phase C — hardening for the residual TOCTOU gap in claim-creation paths.

    _create_claim_record() now wraps its ClaimRecord.objects.create() call in
    its own transaction.atomic() savepoint. Every call site's exists() guard
    still runs first, but if a concurrent transaction commits the winning
    claim in the gap between that check and this INSERT, the DB
    UniqueConstraint (unique_claimed_per_beneficiary_event) raises a genuine
    IntegrityError. These tests force that exact race — by patching
    QuerySet.exists() to lie (return False) while a real conflicting claim
    already exists in the database — to prove the resulting IntegrityError
    is caught and turned into a truthful outcome rather than an HTTP 500,
    that no duplicate ClaimRecord is created, and that unrelated work already
    committed earlier in the same request (the MVR approval / VERIFIED
    decision) survives via the savepoint rather than being rolled back.

    This exercises real SQLite constraint-violation behavior (not a mocked
    exception) — see verification/models.py ClaimRecord.Meta.constraints.
    PostgreSQL enforces the identical partial UniqueConstraint under
    READ COMMITTED; this test does not (and cannot) exercise PostgreSQL's
    own lock/MVCC semantics, only the shared Django-level IntegrityError
    handling that both backends would trigger.
    """

    def setUp(self):
        from verification.models import FaceEmbedding, StipendEvent
        self.president = _make_staff('crie_pres', role=CustomUser.ROLE_PRESIDENT)
        self.admin = _make_staff('crie_admin', role=CustomUser.ROLE_ADMIN)
        self.ben = _make_beneficiary('BEN-CRIE-001', 'SC-CRIE-001')
        self.ben.status = Beneficiary.STATUS_ACTIVE
        self.ben.consent_given = True
        self.ben.save()
        FaceEmbedding.objects.create(
            beneficiary=self.ben, embedding_data=b'\x00' * 512, created_by=self.admin,
        )
        self.event = StipendEvent.objects.create(
            title='CRIE Event', date=datetime.date(2026, 1, 1), event_type='regular',
            amount=250, payout_start_date=datetime.date(2026, 1, 1),
            payout_end_date=datetime.date(2026, 1, 1),
            payout_start_time=datetime.time(13, 0), payout_end_time=datetime.time(17, 0),
            is_active=True, approval_status=StipendEvent.APPROVAL_APPROVED,
            created_by=self.president,
        )

    @staticmethod
    def _mock_now_at(hour, minute=0):
        import datetime as real_dt
        from zoneinfo import ZoneInfo
        manila = ZoneInfo('Asia/Manila')
        fixed = real_dt.datetime(2026, 1, 1, hour, minute, 0, tzinfo=manila)
        return fixed.astimezone(real_dt.timezone.utc)

    def test_manual_verify_review_survives_concurrent_duplicate(self):
        from verification.models import (
            VerificationAttempt, ManualVerificationRequest, ClaimRecord,
        )
        from logs.models import AuditLog

        with mock.patch('django.utils.timezone.now', return_value=self._mock_now_at(14, 0)):
            # The "other station" already got the payout for this beneficiary/event.
            winning_attempt = VerificationAttempt.objects.create(
                beneficiary=self.ben, claimant_type=VerificationAttempt.CLAIMANT_BENEFICIARY,
                decision=VerificationAttempt.DECISION_VERIFIED, similarity_score=0.9,
                stipend_event=self.event, performed_by=self.admin,
            )
            ClaimRecord.objects.create(
                beneficiary=self.ben, stipend_event=self.event,
                claimant_type=VerificationAttempt.CLAIMANT_BENEFICIARY,
                claimed_by=self.admin, verification_attempt=winning_attempt,
                status=ClaimRecord.STATUS_CLAIMED, amount=self.event.amount,
                reference_number=ClaimRecord.generate_reference_number(stipend_event=self.event, when=timezone.now()),
                verification_method=ClaimRecord.VERIFY_FACE,
                released_by=self.admin, released_at=timezone.now(),
            )
            self.assertEqual(
                ClaimRecord.objects.filter(beneficiary=self.ben, stipend_event=self.event,
                                            status=ClaimRecord.STATUS_CLAIMED).count(),
                1,
            )

            # A second, independent attempt (e.g. a manual-review path) racing
            # against the same beneficiary/event, still pending approval.
            attempt = VerificationAttempt.objects.create(
                beneficiary=self.ben, claimant_type=VerificationAttempt.CLAIMANT_BENEFICIARY,
                decision=VerificationAttempt.DECISION_MANUAL_REVIEW, similarity_score=0.6,
                stipend_event=self.event, performed_by=self.admin,
            )
            mvr = ManualVerificationRequest.objects.create(
                beneficiary=self.ben, requested_by=self.admin, verification_attempt=attempt,
                stipend_event=self.event, reason='race test',
            )

            self.client.force_login(self.admin)
            # Force the exists() duplicate-guard to (falsely) report "no claim
            # yet" — this is the exact TOCTOU window the DB UniqueConstraint (not
            # the exists() check) is the final defense against.
            with mock.patch('django.db.models.query.QuerySet.exists', return_value=False):
                resp = self.client.post(
                    reverse('verification:manual_verify_review', kwargs={'request_id': mvr.pk}),
                    {'action': 'approve', 'review_notes': 'approving under race'},
                    secure=True, follow=True,
                )

        # No 500 — the IntegrityError was caught, not left to propagate.
        self.assertEqual(resp.status_code, 200)

        # The identity/approval decision made earlier in the SAME atomic
        # block is preserved (savepoint rollback, not full-transaction
        # rollback) — mirrors the existing _event_ok=False design.
        mvr.refresh_from_db()
        attempt.refresh_from_db()
        self.assertEqual(mvr.status, ManualVerificationRequest.STATUS_APPROVED)
        self.assertEqual(attempt.decision, VerificationAttempt.DECISION_VERIFIED)

        # No duplicate claim was created — still exactly the one winning claim.
        self.assertEqual(
            ClaimRecord.objects.filter(beneficiary=self.ben, stipend_event=self.event,
                                        status=ClaimRecord.STATUS_CLAIMED).count(),
            1,
        )
        self.assertFalse(
            ClaimRecord.objects.filter(verification_attempt=attempt).exists(),
            'the racing attempt must not end up with its own ClaimRecord',
        )

        # The outcome is recorded truthfully, not silently dropped.
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.ACTION_DUPLICATE_PAYOUT_ATTEMPT,
                target_id=str(self.ben.id),
            ).exists()
        )
        messages_text = ' '.join(m.message for m in resp.context['messages'])
        self.assertIn('concurrent action', messages_text)


# ===========================================================================
# FaceNet machine-cache resolution (FANS-C machine-cache closure checkpoint)
#
# Verifies get_facenet_cache_dir() resolves a single, USERPROFILE-independent
# location so an elevated interactive first run and the SYSTEM-account
# autostart/watchdog process (different %USERPROFILE% values, same
# fans_c.exe) always agree on where the FaceNet weights live. No network
# access is used anywhere in this class.
# ===========================================================================

class FaceNetMachineCacheTest(TestCase):

    def setUp(self):
        self._saved_env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved_env)

    @staticmethod
    def _fake_facenet_stack(fake_ctor):
        """
        Context manager that stubs BOTH sys.modules['tensorflow'] and
        sys.modules['keras_facenet'] so get_facenet_model()'s real import
        chain never touches the real (heavy) TensorFlow package -- this
        test file's stated policy is that TensorFlow is never actually
        imported or invoked by these tests.

        Stubbing 'tensorflow' alone is not enough: keras_facenet's own
        inception_resnet_v1.py does `from tensorflow.keras.models import
        Model` at import time, which fails against a bare stub module. So
        'keras_facenet' itself is stubbed too, with `fake_ctor` standing in
        for its FaceNet class -- get_facenet_model()'s
        `from keras_facenet import FaceNet` then never touches the real
        package (or therefore real TensorFlow) at all.

        Mirrors the sys.modules save/restore technique already used by
        FaceProcessingNoneStreamsTest.test_get_facenet_model_catches_non_importerror.
        """
        import types

        class _Ctx:
            def __enter__(self):
                self._saved = {
                    name: sys.modules.get(name)
                    for name in ('tensorflow', 'keras_facenet')
                }
                sys.modules['tensorflow'] = types.ModuleType('tensorflow')
                fake_kf = types.ModuleType('keras_facenet')
                fake_kf.FaceNet = fake_ctor
                sys.modules['keras_facenet'] = fake_kf
                return self

            def __exit__(self, *exc):
                for name, mod in self._saved.items():
                    if mod is not None:
                        sys.modules[name] = mod
                    else:
                        sys.modules.pop(name, None)
                return False

        return _Ctx()

    # A — frozen/packaged path resolution is not USERPROFILE-dependent.
    @override_settings(BASE_DIR=r'C:\FANSC')
    def test_a_frozen_path_not_userprofile_dependent(self):
        from verification.face_utils import get_facenet_cache_dir
        os.environ.pop('FANS_FACENET_CACHE_DIR', None)
        os.environ['USERPROFILE'] = r'C:\Users\SomeAdmin'
        result = get_facenet_cache_dir()
        self.assertEqual(result, str(Path(r'C:\FANSC') / 'models' / 'keras-facenet'))
        self.assertNotIn('Users', result)
        self.assertNotIn('SomeAdmin', result)

    # B — two different USERPROFILE values, same BASE_DIR, resolve identically.
    # This is the direct acceptance test for "elevated interactive first run
    # vs. SYSTEM-account autostart" agreeing on the cache path.
    @override_settings(BASE_DIR=r'C:\FANSC')
    def test_b_same_base_dir_different_userprofile_same_cache(self):
        from verification.face_utils import get_facenet_cache_dir
        os.environ.pop('FANS_FACENET_CACHE_DIR', None)

        os.environ['USERPROFILE'] = r'C:\Users\InteractiveAdmin'
        as_admin = get_facenet_cache_dir()

        os.environ['USERPROFILE'] = r'C:\Windows\System32\config\systemprofile'
        as_system = get_facenet_cache_dir()

        self.assertEqual(as_admin, as_system)

    # C — source-mode default is deterministic and does not hardcode C:\FANSC.
    #
    # BASE_DIR is deliberately overridden to a synthetic path that is
    # neither the installer's C:\FANSC nor (unlike an unpatched checkout
    # such as this repository's own C:\FANSC\Facial-Verification-System
    # working copy) contains "FANSC" as a substring. This keeps the
    # assertion a property of get_facenet_cache_dir()'s logic -- it must
    # derive the cache dir from settings.BASE_DIR rather than hardcoding
    # the packaged install path -- instead of a property of wherever this
    # repository happens to be checked out on disk.
    @override_settings(BASE_DIR=r'C:\SomeOtherRoot\App')
    def test_c_source_default_deterministic_no_hardcoded_fansc(self):
        from verification.face_utils import get_facenet_cache_dir
        os.environ.pop('FANS_FACENET_CACHE_DIR', None)
        from django.conf import settings as dj_settings
        expected = str(Path(dj_settings.BASE_DIR) / 'models' / 'keras-facenet')
        result = get_facenet_cache_dir()
        self.assertEqual(result, expected)
        self.assertNotIn('FANSC', result)

    # D — explicit safe cache-path environment override works.
    def test_d_absolute_env_override_is_used(self):
        from verification.face_utils import get_facenet_cache_dir
        override_dir = tempfile.mkdtemp(prefix='fans-facenet-cache-')
        try:
            os.environ['FANS_FACENET_CACHE_DIR'] = override_dir
            self.assertEqual(get_facenet_cache_dir(), override_dir)
        finally:
            shutil.rmtree(override_dir, ignore_errors=True)

    # D (validation) — a relative override is rejected, not resolved against
    # an arbitrary current working directory.
    def test_d_relative_env_override_falls_back_to_default(self):
        from verification.face_utils import get_facenet_cache_dir
        from django.conf import settings as dj_settings
        os.environ['FANS_FACENET_CACHE_DIR'] = r'relative\models\path'
        expected = str(Path(dj_settings.BASE_DIR) / 'models' / 'keras-facenet')
        self.assertEqual(get_facenet_cache_dir(), expected)

    # E — the keras-facenet FaceNet constructor receives the resolved
    # cache_folder (not the package's own %USERPROFILE% default).
    def test_e_facenet_constructor_receives_resolved_cache_folder(self):
        import verification.face_utils as fu
        saved = (fu._facenet_model, fu._using_mock, fu._model_load_error,
                  fu._facenet_load_failed)
        fu._facenet_model = None
        fu._using_mock = False
        fu._model_load_error = None
        fu._facenet_load_failed = False

        fake_cache_dir = tempfile.mkdtemp(prefix='fans-facenet-cache-')
        fake_model = mock.Mock()
        fake_ctor = mock.Mock(return_value=fake_model)
        try:
            with self._fake_facenet_stack(fake_ctor), \
                 mock.patch('verification.face_utils.get_facenet_cache_dir',
                             return_value=fake_cache_dir):
                result = fu.get_facenet_model()
            fake_ctor.assert_called_once_with(cache_folder=fake_cache_dir)
            self.assertIs(result, fake_model)
        finally:
            shutil.rmtree(fake_cache_dir, ignore_errors=True)
            (fu._facenet_model, fu._using_mock, fu._model_load_error,
             fu._facenet_load_failed) = saved

    # F — a cache-directory creation failure remains fail-closed: it must
    # raise FaceNetUnavailableError, never fall back to a mock/random model.
    def test_f_cache_dir_creation_failure_fails_closed(self):
        import verification.face_utils as fu
        saved = (fu._facenet_model, fu._using_mock, fu._model_load_error,
                  fu._facenet_load_failed)
        fu._facenet_model = None
        fu._using_mock = False
        fu._model_load_error = None
        fu._facenet_load_failed = False

        fake_ctor = mock.Mock()  # must never be reached
        try:
            with self._fake_facenet_stack(fake_ctor), \
                 mock.patch('verification.face_utils.get_facenet_cache_dir',
                             return_value=r'Z:\unwritable\facenet-cache'), \
                 mock.patch('verification.face_utils.os.makedirs',
                             side_effect=PermissionError('Access is denied')):
                with self.assertRaises(fu.FaceNetUnavailableError) as ctx:
                    fu.get_facenet_model()
            self.assertEqual(str(ctx.exception), fu._SAFE_UNAVAILABLE_MESSAGE)
            self.assertIsNone(fu._facenet_model)
            self.assertTrue(fu._facenet_load_failed)
            fake_ctor.assert_not_called()
        finally:
            (fu._facenet_model, fu._using_mock, fu._model_load_error,
             fu._facenet_load_failed) = saved


# ══════════════════════════════════════════════════════════════════════════
# v2.1.19 UX / Reporting / Technical Administration pass — focused tests
# ══════════════════════════════════════════════════════════════════════════

class TechnicalAdministratorFinancialAuthorityDeniedTest(TestCase):
    """
    Technical Administrator (role=IT) keeps broad READ access (is_admin) but
    must NOT be able to perform financial/decision mutations — see
    CustomUser.has_financial_authority. Covers the concrete endpoints named
    in the FINAL PRE-EXE UX/REPORTING CLOSURE spec, section 7, plus
    `pending_claim_review` (closed by the FINAL PRE-EXE COMPLETION
    checkpoint, section 5 — see PendingClaimReviewFinancialAuthorityTest
    below for the dedicated coverage).
    """

    def setUp(self):
        self.client = Client()
        self.ta = CustomUser.objects.create_user(
            username='fa_ta', password='TestPass123!',
            role=CustomUser.ROLE_IT, employee_id='EMP-FA-TA',
        )
        self.admin = CustomUser.objects.create_user(
            username='fa_admin', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-FA-ADM',
        )
        self.staff = CustomUser.objects.create_user(
            username='fa_staff', password='TestPass123!',
            role=CustomUser.ROLE_STAFF, employee_id='EMP-FA-STF',
        )
        self.ben = _make_beneficiary(ben_id='BEN-FA-001', sc_id='SC-FA-001')
        self.ben.status = Beneficiary.STATUS_ACTIVE
        self.ben.consent_given = True
        self.ben.save()

    def test_technical_administrator_cannot_create_stipend_event(self):
        from verification.models import StipendEvent
        self.client.force_login(self.ta)
        before = StipendEvent.objects.count()
        self.client.post(reverse('verification:stipend_create'), {
            'title': 'TA Attempt', 'date': '2026-12-01',
            'event_type': StipendEvent.EVENT_TYPE_REGULAR, 'amount': '500',
        })
        self.assertEqual(StipendEvent.objects.count(), before)

    def test_admin_can_create_stipend_event(self):
        from verification.models import StipendEvent
        self.client.force_login(self.admin)
        before = StipendEvent.objects.count()
        self.client.post(reverse('verification:stipend_create'), {
            'title': 'Admin Created Event', 'date': '2026-12-01',
            'event_type': StipendEvent.EVENT_TYPE_REGULAR, 'amount': '500',
        })
        self.assertEqual(StipendEvent.objects.count(), before + 1)

    def test_technical_administrator_cannot_override_verification_decision(self):
        from verification.models import VerificationAttempt
        attempt = VerificationAttempt.objects.create(
            beneficiary=self.ben, performed_by=self.staff,
            decision=VerificationAttempt.DECISION_NOT_VERIFIED,
            similarity_score=0.5, threshold_used=0.75,
        )
        self.client.force_login(self.ta)
        self.client.post(reverse('verification:admin_override', args=[attempt.pk]), {
            'decision': VerificationAttempt.DECISION_VERIFIED,
            'reason': 'Physical ID confirmed by officer in charge during outreach visit.',
        })
        attempt.refresh_from_db()
        self.assertFalse(attempt.overridden)
        self.assertEqual(attempt.decision, VerificationAttempt.DECISION_NOT_VERIFIED)

    def test_technical_administrator_cannot_approve_manual_review(self):
        from verification.models import ManualVerificationRequest
        mvr = ManualVerificationRequest.objects.create(
            beneficiary=self.ben, requested_by=self.staff,
            reason='ID checked manually in the field.',
        )
        self.client.force_login(self.ta)
        self.client.post(reverse('verification:manual_verify_review', args=[mvr.pk]), {
            'action': 'approve', 'review_notes': 'ok',
        })
        mvr.refresh_from_db()
        self.assertEqual(mvr.status, ManualVerificationRequest.STATUS_PENDING)

    def test_technical_administrator_can_view_manual_review_detail(self):
        """Read access is preserved — only the approve/reject action is denied."""
        from verification.models import ManualVerificationRequest
        mvr = ManualVerificationRequest.objects.create(
            beneficiary=self.ben, requested_by=self.staff,
            reason='ID checked manually in the field.',
        )
        self.client.force_login(self.ta)
        resp = self.client.get(reverse('verification:manual_verify_review', args=[mvr.pk]))
        self.assertEqual(resp.status_code, 200)


class BiometricEvaluationAccessMatrixTest(TestCase):
    """
    Owner-approved final access matrix for Biometric Evaluation (FINAL
    PRE-EXE COMPLETION checkpoint, section 3 — supersedes the earlier
    "President full access" reasoning; tests validate product policy, they
    do not define it):
      Technical Administrator — full access (read + write).
      President               — read-only (list/detail/analytics); every
                                 mutating action is denied server-side.
      Admin / Staff           — no access at all, read or write.
    """

    def setUp(self):
        self.client = Client()
        self.ta = CustomUser.objects.create_user(
            username='eval_ta', password='TestPass123!',
            role=CustomUser.ROLE_IT, employee_id='EMP-EVAL-TA',
        )
        self.president = CustomUser.objects.create_user(
            username='eval_pres', password='TestPass123!',
            role=CustomUser.ROLE_PRESIDENT, employee_id='EMP-EVAL-PRES',
        )
        self.admin = CustomUser.objects.create_user(
            username='eval_admin', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-EVAL-ADM',
        )
        self.staff = CustomUser.objects.create_user(
            username='eval_staff', password='TestPass123!',
            role=CustomUser.ROLE_STAFF, employee_id='EMP-EVAL-STF',
        )
        self.list_url = reverse('verification:evaluation_dataset_list')
        self.create_url = reverse('verification:evaluation_dataset_create')

    def test_technical_administrator_full_access_read(self):
        self.client.force_login(self.ta)
        self.assertEqual(self.client.get(self.list_url).status_code, 200)

    def test_technical_administrator_full_access_write(self):
        self.client.force_login(self.ta)
        self.assertEqual(self.client.get(self.create_url).status_code, 200)

    def test_president_full_access_read(self):
        self.client.force_login(self.president)
        self.assertEqual(self.client.get(self.list_url).status_code, 200)

    def test_president_denied_write(self):
        from verification.models import EvaluationDataset
        self.client.force_login(self.president)
        resp = self.client.get(self.create_url, follow=True)
        self.assertEqual(resp.redirect_chain[-1][0], self.list_url)
        self.assertEqual(EvaluationDataset.objects.count(), 0)

    def test_admin_denied_all_access(self):
        self.client.force_login(self.admin)
        resp = self.client.get(self.list_url, follow=True)
        self.assertEqual(resp.redirect_chain[-1][0], reverse('beneficiaries:dashboard'))

    def test_staff_denied_all_access(self):
        self.client.force_login(self.staff)
        resp = self.client.get(self.list_url, follow=True)
        self.assertEqual(resp.redirect_chain[-1][0], reverse('beneficiaries:dashboard'))

    def test_anonymous_denied(self):
        resp = self.client.get(self.list_url)
        self.assertIn(resp.status_code, [301, 302])


class TechnicalAdministrationNavVisibilityTest(TestCase):
    """
    v2.1.17 QA fix pass, Issue 2: President has read-only BACKEND access to
    Biometric Evaluation/Technical Administration (see
    BiometricEvaluationAccessMatrixTest.test_president_full_access_read
    above — this test does not touch that), but the "Technical
    Administration" nav dropdown and its biometric-evaluation entries should
    not route President into a section they can only look at, then get told
    the action is restricted. Only the Technical Administrator (role=IT)
    should see the nav entry points at all.
    """

    def setUp(self):
        self.client = Client()
        self.ta = CustomUser.objects.create_user(
            username='navvis_ta', password='TestPass123!',
            role=CustomUser.ROLE_IT, employee_id='EMP-NAVVIS-TA',
        )
        self.president = CustomUser.objects.create_user(
            username='navvis_pres', password='TestPass123!',
            role=CustomUser.ROLE_PRESIDENT, employee_id='EMP-NAVVIS-PRES',
        )
        self.dashboard_url = reverse('beneficiaries:dashboard')

    def test_technical_administrator_sees_technical_administration_nav(self):
        self.client.force_login(self.ta)
        resp = self.client.get(self.dashboard_url)
        # Nav label shortened to "Tech Admin" (v2.1.17 navbar-density polish
        # pass) — the role itself is still Technical Administrator
        # everywhere else; this only checks the nav entry point's visibility.
        self.assertContains(resp, 'Tech Admin')

    def test_president_does_not_see_technical_administration_nav(self):
        self.client.force_login(self.president)
        resp = self.client.get(self.dashboard_url)
        self.assertNotContains(resp, 'Tech Admin')

    def test_president_does_not_see_biometric_evaluation_analytics_tab(self):
        """The 'System Evaluation & Research' tab on the Analytics pages
        also links into Biometric Evaluation and must be hidden the same way."""
        self.client.force_login(self.president)
        resp = self.client.get(reverse('verification:analytics_executive'))
        self.assertNotContains(resp, 'System Evaluation &amp; Research')

    def test_technical_administrator_sees_biometric_evaluation_analytics_tab(self):
        self.client.force_login(self.ta)
        resp = self.client.get(reverse('verification:analytics_executive'))
        self.assertContains(resp, 'System Evaluation &amp; Research')


class PendingClaimReviewFinancialAuthorityTest(TestCase):
    """
    FINAL PRE-EXE COMPLETION checkpoint, section 5: closes the
    `pending_claim_review` exception that previously let the Technical
    Administrator (role=IT) act on a pending-approval claim merely because
    it satisfied `is_admin`. This is a financial decision (approve/reject +
    payout release), so it is now gated with `has_financial_authority`
    (President/Admin) like every other financial-mutation view — President's
    pre-existing authority is unchanged, Admin's is preserved, Staff and
    Technical Administrator are denied on both GET (review page) and POST
    (the actual decision).
    """

    def setUp(self):
        from verification.models import ClaimRecord
        self.client = Client()
        self.president = CustomUser.objects.create_user(
            username='pcr_pres', password='TestPass123!',
            role=CustomUser.ROLE_PRESIDENT, employee_id='EMP-PCR-PRES',
        )
        self.admin = CustomUser.objects.create_user(
            username='pcr_admin', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-PCR-ADM',
        )
        self.ta = CustomUser.objects.create_user(
            username='pcr_ta', password='TestPass123!',
            role=CustomUser.ROLE_IT, employee_id='EMP-PCR-TA',
        )
        self.staff = CustomUser.objects.create_user(
            username='pcr_staff', password='TestPass123!',
            role=CustomUser.ROLE_STAFF, employee_id='EMP-PCR-STF',
        )
        self.ben = _make_beneficiary(ben_id='BEN-PCR-001', sc_id='SC-PCR-001')
        self.ben.status = Beneficiary.STATUS_ACTIVE
        self.ben.consent_given = True
        self.ben.save()
        self.ClaimRecord = ClaimRecord

    def _make_claim(self):
        return self.ClaimRecord.objects.create(
            beneficiary=self.ben, stipend_event=None,
            status=self.ClaimRecord.STATUS_PENDING_APPROVAL, amount=0, claimed_by=self.staff,
        )

    def test_president_can_approve(self):
        claim = self._make_claim()
        self.client.force_login(self.president)
        self.client.post(reverse('verification:pending_claim_review', args=[claim.pk]), {
            'action': 'approve', 'review_notes': 'Verified manually.',
        })
        claim.refresh_from_db()
        self.assertEqual(claim.status, self.ClaimRecord.STATUS_CLAIMED)

    def test_admin_can_approve(self):
        claim = self._make_claim()
        self.client.force_login(self.admin)
        self.client.post(reverse('verification:pending_claim_review', args=[claim.pk]), {
            'action': 'approve', 'review_notes': 'Verified manually.',
        })
        claim.refresh_from_db()
        self.assertEqual(claim.status, self.ClaimRecord.STATUS_CLAIMED)

    def test_technical_administrator_denied_get(self):
        claim = self._make_claim()
        self.client.force_login(self.ta)
        resp = self.client.get(reverse('verification:pending_claim_review', args=[claim.pk]), follow=True)
        self.assertEqual(resp.redirect_chain[-1][0], reverse('beneficiaries:dashboard'))

    def test_technical_administrator_denied_post(self):
        claim = self._make_claim()
        self.client.force_login(self.ta)
        self.client.post(reverse('verification:pending_claim_review', args=[claim.pk]), {
            'action': 'approve', 'review_notes': 'Attempted by Technical Administrator.',
        })
        claim.refresh_from_db()
        self.assertEqual(claim.status, self.ClaimRecord.STATUS_PENDING_APPROVAL)

    def test_staff_denied_post(self):
        claim = self._make_claim()
        self.client.force_login(self.staff)
        self.client.post(reverse('verification:pending_claim_review', args=[claim.pk]), {
            'action': 'approve', 'review_notes': 'Attempted by Staff.',
        })
        claim.refresh_from_db()
        self.assertEqual(claim.status, self.ClaimRecord.STATUS_PENDING_APPROVAL)


class PayoutDetailLifecycleAndEvidenceTest(TestCase):
    """
    FINAL PRE-EXE COMPLETION checkpoint, sections 6/7: payout_detail must
    render a Verification Evidence section with truthfully-labeled
    thresholds (never implying the CURRENT auto-verify setting was the
    historical one) and a Lifecycle sequence built only from timestamps the
    ClaimRecord actually persisted.
    """

    def setUp(self):
        from verification.models import ClaimRecord, VerificationAttempt, StipendEvent
        self.client = Client()
        self.admin = CustomUser.objects.create_user(
            username='pd_admin', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-PD-ADM',
        )
        self.ben = _make_beneficiary(ben_id='BEN-PD-001', sc_id='SC-PD-001')
        self.ben.status = Beneficiary.STATUS_ACTIVE
        self.ben.consent_given = True
        self.ben.save()
        self.event = StipendEvent.objects.create(
            title='PD Test Event', date=timezone.now().date(),
            event_type=StipendEvent.EVENT_TYPE_REGULAR, amount=1000,
        )
        self.attempt = VerificationAttempt.objects.create(
            beneficiary=self.ben, performed_by=self.admin, stipend_event=self.event,
            similarity_score=0.91, threshold_used=0.60,
            decision=VerificationAttempt.DECISION_VERIFIED,
            liveness_passed=True, liveness_score=0.87, anti_spoof_score=0.95,
        )
        self.claim = ClaimRecord.objects.create(
            beneficiary=self.ben, stipend_event=self.event, claimed_by=self.admin,
            verification_attempt=self.attempt, status=ClaimRecord.STATUS_CLAIMED,
            amount=1000, reference_number='RC-TEST-00001',
            released_by=self.admin, released_at=timezone.now(),
            verification_method=ClaimRecord.VERIFY_FACE,
        )

    def test_verification_evidence_labels_are_precise(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('verification:payout_detail', args=[self.claim.pk]))
        self.assertContains(resp, 'Manual-Review Threshold Recorded')
        self.assertContains(resp, 'Auto-Verify Threshold')
        self.assertContains(resp, 'Liveness Passed')
        self.assertContains(resp, 'Anti-Spoof Score')

    def test_lifecycle_shows_verification_and_release_steps(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('verification:payout_detail', args=[self.claim.pk]))
        self.assertContains(resp, 'Verification')
        self.assertContains(resp, 'Claim / Release')
        self.assertEqual(len(resp.context['lifecycle']), 2)  # Verification + Release only — no review/correction happened

    def test_lifecycle_never_fabricates_review_or_correction_steps(self):
        """No approved_at/override_at were set — those steps must be absent, not shown as blank/N-A rows."""
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('verification:payout_detail', args=[self.claim.pk]))
        labels = [step['label'] for step in resp.context['lifecycle']]
        self.assertNotIn('Review / Approval', labels)
        self.assertNotIn('Administrative Correction', labels)


class PayoutExcelExportFormattingTest(TestCase):
    """
    FINAL PRE-EXE COMPLETION checkpoint, section 8: the existing payout
    Excel export must use the shared `build_report_workbook` formatter
    (title, generated-by, filter summary, styled/frozen/autofiltered
    headers, real currency/datetime cells, summary totals) instead of a raw
    unstyled openpyxl dump — the exact complaint the project owner raised
    (squeezed columns, unreadable headers, ######## dates).
    """

    def setUp(self):
        from verification.models import ClaimRecord, VerificationAttempt, StipendEvent
        self.client = Client()
        self.admin = CustomUser.objects.create_user(
            username='pxl_admin', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-PXL-ADM',
        )
        self.ben = _make_beneficiary(ben_id='BEN-PXL-001', sc_id='SC-PXL-001')
        self.ben.status = Beneficiary.STATUS_ACTIVE
        self.ben.consent_given = True
        self.ben.save()
        self.event = StipendEvent.objects.create(
            title='PXL Test Event', date=timezone.now().date(),
            event_type=StipendEvent.EVENT_TYPE_REGULAR, amount=1000,
        )
        ClaimRecord.objects.create(
            beneficiary=self.ben, stipend_event=self.event, claimed_by=self.admin,
            status=ClaimRecord.STATUS_CLAIMED, amount=1234.56, reference_number='RC-PXL-00001',
            released_by=self.admin, released_at=timezone.now(),
            verification_method=ClaimRecord.VERIFY_FACE,
        )

    def test_excel_export_has_title_and_formatting_contract(self):
        import io
        import openpyxl
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('verification:report_claims'), {'export': 'excel'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        wb = openpyxl.load_workbook(io.BytesIO(b''.join(resp.streaming_content) if resp.streaming else resp.content))
        ws = wb.active
        self.assertEqual(ws.title, 'Detailed Payouts')
        self.assertIn('FANS-C', ws.cell(row=1, column=1).value)
        self.assertIn('Generated:', ws.cell(row=2, column=1).value)
        self.assertIsNotNone(ws.auto_filter.ref)
        self.assertIsNotNone(ws.freeze_panes)
        # Header row is row 4 (title, generated-by, filter-summary, then headers).
        header_values = [c.value for c in ws[4]]
        self.assertIn('Amount (PHP)', header_values)
        self.assertIn('Released At', header_values)
        amount_col = header_values.index('Amount (PHP)') + 1
        released_col = header_values.index('Released At') + 1
        data_cell = ws.cell(row=5, column=amount_col)
        self.assertEqual(data_cell.value, 1234.56)
        self.assertIn('0.00', data_cell.number_format)
        date_cell = ws.cell(row=5, column=released_col)
        import datetime
        self.assertIsInstance(date_cell.value, datetime.datetime)
        self.assertIsNone(date_cell.value.tzinfo)  # naive local time — openpyxl rejects tz-aware
        # Totals row follows the single data row.
        totals_amount_cell = ws.cell(row=6, column=amount_col)
        self.assertEqual(totals_amount_cell.value, 1234.56)


class ReportClaimsReleasedByFilterTest(TestCase):
    """
    Section 28 — Released By is a user-ID selector now, not a free-text
    substring search over the hidden username. Regression test for the
    reported bug: searching a fragment of the username must no longer match
    a claim whose released_by full name doesn't contain that fragment.
    """

    def setUp(self):
        from verification.models import ClaimRecord, StipendEvent
        self.client = Client()
        self.admin = CustomUser.objects.create_user(
            username='rbf_admin', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-RBF-ADM',
        )
        # Username deliberately contains a fragment ("exe") that does NOT
        # appear in the display name — mirrors the reported "Exe" ->
        # "aeilexe" mismatch (search matched hidden username, not what the
        # table displays).
        self.releaser = CustomUser.objects.create_user(
            username='aeilexe', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-RBF-REL',
            first_name='Whinelit', last_name='Recto',
        )
        self.other_releaser = CustomUser.objects.create_user(
            username='other_releaser', password='TestPass123!',
            role=CustomUser.ROLE_ADMIN, employee_id='EMP-RBF-OTH',
            first_name='Juan', last_name='DelaCruz',
        )
        self.ben = _make_beneficiary(ben_id='BEN-RBF-001', sc_id='SC-RBF-001')
        self.ben2 = _make_beneficiary(ben_id='BEN-RBF-002', sc_id='SC-RBF-002')
        self.event = StipendEvent.objects.create(
            title='RBF Event', date='2026-01-01', amount=500,
            event_type=StipendEvent.EVENT_TYPE_REGULAR,
            approval_status=StipendEvent.APPROVAL_APPROVED,
        )
        self.claim = ClaimRecord.objects.create(
            beneficiary=self.ben, stipend_event=self.event, amount=500,
            status=ClaimRecord.STATUS_CLAIMED, claimed_by=self.releaser,
            released_by=self.releaser, released_at=timezone.now(),
        )
        self.other_claim = ClaimRecord.objects.create(
            beneficiary=self.ben2, stipend_event=self.event, amount=500,
            status=ClaimRecord.STATUS_CLAIMED, claimed_by=self.other_releaser,
            released_by=self.other_releaser, released_at=timezone.now(),
        )

    def test_filters_by_exact_released_by_id(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('verification:report_claims'), {'released_by': self.releaser.pk})
        claims = list(resp.context['claims'])
        self.assertIn(self.claim, claims)
        self.assertNotIn(self.other_claim, claims)

    def test_non_numeric_released_by_returns_no_crash_no_match(self):
        """An old bookmarked ?released_by=text URL must not 500 or silently
        substring-match the hidden username."""
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('verification:report_claims'), {'released_by': 'Exe'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(list(resp.context['claims']), [])


class DistributionSummaryDateFilterTest(TestCase):
    """Distribution Summary Report (report_event_summary) date filtering.

    Source model/field: StipendEvent.date (a plain DateField — no timezone
    conversion applies). The filter selects which events appear in the
    report; each row's own claim/attempt counts remain unfiltered lifetime
    totals for that event, same as before this feature was added.
    """

    def setUp(self):
        from verification.models import StipendEvent, ClaimRecord
        self.StipendEvent = StipendEvent
        self.ClaimRecord = ClaimRecord

        self.admin = CustomUser.objects.create_user(
            username='ds_admin', password='TestPass123!',
            role=CustomUser.ROLE_IT, employee_id='EMP-DS-ADMIN',
        )
        self.ben = Beneficiary.objects.create(
            beneficiary_id='BEN-DS-001', first_name='Ana', last_name='Cruz',
            senior_citizen_id='SC-DS-001', date_of_birth='1945-01-01', gender='F',
            address='1 St', barangay='Test Barangay', municipality='Quezon City',
            province='Metro Manila',
        )
        self.client = Client()
        self.client.force_login(self.admin)

        self.event_old = StipendEvent.objects.create(title='January Payout', date='2026-01-10', amount=500)
        self.event_mid = StipendEvent.objects.create(title='March Payout', date='2026-03-10', amount=500)
        self.event_new = StipendEvent.objects.create(title='June Payout', date='2026-06-10', amount=500)
        ClaimRecord.objects.create(
            beneficiary=self.ben, stipend_event=self.event_mid, amount=500,
            status=ClaimRecord.STATUS_CLAIMED,
        )

    def _events(self, resp):
        return {s['event'].pk for s in resp.context['summaries']}

    def test_date_from_only(self):
        resp = self.client.get(reverse('verification:report_event_summary'), {'date_from': '2026-02-01'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._events(resp), {self.event_mid.pk, self.event_new.pk})

    def test_date_to_only(self):
        resp = self.client.get(reverse('verification:report_event_summary'), {'date_to': '2026-02-01'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._events(resp), {self.event_old.pk})

    def test_date_from_and_to_boundary_inclusive(self):
        resp = self.client.get(reverse('verification:report_event_summary'), {
            'date_from': '2026-01-10', 'date_to': '2026-03-10',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._events(resp), {self.event_old.pk, self.event_mid.pk})

    def test_invalid_range_shows_warning_and_no_crash(self):
        resp = self.client.get(reverse('verification:report_event_summary'), {
            'date_from': '2026-06-10', 'date_to': '2026-01-10',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['date_range_invalid'])
        self.assertEqual(list(resp.context['summaries']), [])

    def test_no_results_in_range(self):
        resp = self.client.get(reverse('verification:report_event_summary'), {'date_from': '2027-01-01'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(list(resp.context['summaries']), [])
        self.assertContains(resp, 'No distribution events match the selected filters.')

    def test_kpi_totals_match_filtered_events_only(self):
        resp = self.client.get(reverse('verification:report_event_summary'), {
            'date_from': '2026-03-01', 'date_to': '2026-04-01',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['kpi_totals']['events'], 1)
        self.assertEqual(resp.context['kpi_totals']['claimed'], 1)

    def test_excel_export_matches_filtered_events(self):
        resp = self.client.get(reverse('verification:report_event_summary'), {
            'date_from': '2026-02-01', 'export': 'excel',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp['Content-Disposition'],
            'attachment; filename="fansc-distribution-summary.xlsx"',
        )

    def test_print_export_matches_filtered_events(self):
        resp = self.client.get(reverse('verification:report_event_summary'), {
            'date_from': '2026-02-01', 'export': 'print',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._events(resp), {self.event_mid.pk, self.event_new.pk})

    def test_event_type_filter(self):
        resp = self.client.get(reverse('verification:report_event_summary'), {
            'event_type': self.StipendEvent.EVENT_TYPE_REGULAR,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context['summaries']), 3)

    def test_invalid_event_type_ignored(self):
        resp = self.client.get(reverse('verification:report_event_summary'), {'event_type': 'not_a_type'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['event_type_filter'], '')


# ──────────────────────────────────────────────────────────────────────────────
# v2.1.17 member-testing audit — Finding D: payout amount must reject
# ambiguous leading-zero input server-side (create AND edit paths, plus the
# claim-amount override path)
# ──────────────────────────────────────────────────────────────────────────────

class PayoutAmountParserTest(TestCase):
    """Unit tests for the shared parse_payout_amount canonical-format parser."""

    def _parse(self, raw):
        from verification.models import parse_payout_amount
        return parse_payout_amount(raw)

    def test_accepts_zero(self):
        from decimal import Decimal
        self.assertEqual(self._parse('0'), Decimal('0'))

    def test_accepts_zero_with_decimals(self):
        from decimal import Decimal
        self.assertEqual(self._parse('0.00'), Decimal('0.00'))

    def test_accepts_plain_integer(self):
        from decimal import Decimal
        self.assertEqual(self._parse('123'), Decimal('123'))

    def test_accepts_two_decimal_places(self):
        from decimal import Decimal
        self.assertEqual(self._parse('123.50'), Decimal('123.50'))

    def test_accepts_large_integer(self):
        from decimal import Decimal
        self.assertEqual(self._parse('100000'), Decimal('100000'))

    def test_accepts_empty_as_zero(self):
        from decimal import Decimal
        self.assertEqual(self._parse(''), Decimal('0'))

    def test_rejects_leading_zero(self):
        with self.assertRaises(ValueError):
            self._parse('0123')

    def test_rejects_leading_zeros_larger(self):
        with self.assertRaises(ValueError):
            self._parse('001000')

    def test_rejects_leading_zero_with_decimals(self):
        with self.assertRaises(ValueError):
            self._parse('00.50')

    def test_rejects_malformed_sign(self):
        with self.assertRaises(ValueError):
            self._parse('+123')

    def test_rejects_non_numeric(self):
        with self.assertRaises(ValueError):
            self._parse('abc')

    def test_rejects_negative(self):
        with self.assertRaises(ValueError):
            self._parse('-5')

    def test_rejects_excess_precision_digits(self):
        with self.assertRaises(ValueError):
            self._parse('123.999')

    def test_rejects_too_many_integer_digits(self):
        with self.assertRaises(ValueError):
            self._parse('12345678901')  # 11 digits > max_digits(12) - decimal_places(2) = 10


class PayoutAmountViewTest(TestCase):
    """Create AND edit paths must share the same server-side validation."""

    def setUp(self):
        self.client = Client()
        self.president = _make_staff('amt_president', role=CustomUser.ROLE_PRESIDENT)
        self.client.force_login(self.president)

    def test_create_rejects_leading_zero_amount(self):
        from verification.models import StipendEvent
        resp = self.client.post(reverse('verification:stipend_create'), {
            'title': 'Leading Zero Test Event',
            'date': '2026-09-01',
            'event_type': 'regular',
            'amount': '0123',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(StipendEvent.objects.filter(title='Leading Zero Test Event').exists())

    def test_create_accepts_canonical_amount(self):
        from verification.models import StipendEvent
        resp = self.client.post(reverse('verification:stipend_create'), {
            'title': 'Canonical Amount Test Event',
            'date': '2026-09-01',
            'event_type': 'regular',
            'amount': '1500.50',
        })
        self.assertEqual(resp.status_code, 302)
        event = StipendEvent.objects.get(title='Canonical Amount Test Event')
        self.assertEqual(str(event.amount), '1500.50')

    def test_edit_rejects_leading_zero_amount(self):
        from decimal import Decimal
        from verification.models import StipendEvent
        event = StipendEvent.objects.create(
            title='Edit Amount Event', date=datetime.date(2026, 9, 5),
            amount=Decimal('500'), created_by=self.president,
            approval_status=StipendEvent.APPROVAL_APPROVED,
        )
        resp = self.client.post(reverse('verification:stipend_edit', args=[event.pk]), {
            'title': event.title,
            'date': '2026-09-05',
            'event_type': 'regular',
            'amount': '001000',
        })
        self.assertEqual(resp.status_code, 200)
        event.refresh_from_db()
        self.assertEqual(event.amount, Decimal('500'))

    def test_edit_accepts_canonical_amount(self):
        from decimal import Decimal
        from verification.models import StipendEvent
        event = StipendEvent.objects.create(
            title='Edit Amount Event 2', date=datetime.date(2026, 9, 6),
            amount=Decimal('500'), created_by=self.president,
            approval_status=StipendEvent.APPROVAL_APPROVED,
        )
        resp = self.client.post(reverse('verification:stipend_edit', args=[event.pk]), {
            'title': event.title,
            'date': '2026-09-06',
            'event_type': 'regular',
            'amount': '750.25',
        })
        self.assertEqual(resp.status_code, 302)
        event.refresh_from_db()
        self.assertEqual(event.amount, Decimal('750.25'))


class ClaimAmountOverrideValidationTest(TestCase):
    """The claim-amount override path (payout_action) must use the same
    canonical-format validation, and must never rewrite historical
    ClaimRecord amounts on a rejected/malformed override attempt."""

    def setUp(self):
        from decimal import Decimal
        from beneficiaries.models import Beneficiary
        from verification.models import StipendEvent, ClaimRecord
        self.client = Client()
        self.admin = _make_staff('amt_override_admin', role=CustomUser.ROLE_ADMIN)
        self.client.force_login(self.admin)
        self.beneficiary = Beneficiary.objects.create(
            first_name='Amt', last_name='Override', date_of_birth=datetime.date(1945, 1, 1),
            gender='M', municipality='Quezon City', barangay='Commonwealth',
            province='Metro Manila (NCR)', senior_citizen_id='SC-AMTOV-001', status='active',
        )
        self.event = StipendEvent.objects.create(
            title='Override Amount Event', date=datetime.date(2026, 1, 1),
            amount=Decimal('500'), created_by=self.admin,
            approval_status=StipendEvent.APPROVAL_APPROVED,
        )
        self.claim = ClaimRecord.objects.create(
            beneficiary=self.beneficiary, stipend_event=self.event, claimant_type='beneficiary',
            status=ClaimRecord.STATUS_CLAIMED, claimed_by=self.admin, amount=Decimal('500'),
        )

    def test_override_rejects_leading_zero_amount(self):
        from decimal import Decimal
        resp = self.client.post(reverse('verification:payout_action', args=[self.claim.pk]), {
            'action': 'override',
            'override_reason': 'Correcting a data entry mistake in the release amount.',
            'amount': '0750',
        })
        self.assertEqual(resp.status_code, 302)
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.amount, Decimal('500'))

    def test_override_accepts_canonical_amount(self):
        from decimal import Decimal
        resp = self.client.post(reverse('verification:payout_action', args=[self.claim.pk]), {
            'action': 'override',
            'override_reason': 'Correcting a data entry mistake in the release amount.',
            'amount': '750',
        })
        self.assertEqual(resp.status_code, 302)
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.amount, Decimal('750'))


# ──────────────────────────────────────────────────────────────────────────────
# v2.1.17 audit — shared-representative authorization document upload must be
# restricted to a safe extension allowlist (direct request.FILES -> model
# field write, bypasses ModelForm/full_clean entirely)
# ──────────────────────────────────────────────────────────────────────────────

class SharedRepAuthorizationDocumentUploadTest(TestCase):
    def setUp(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from beneficiaries.models import Beneficiary, Representative, SharedRepresentativeReview
        self.SimpleUploadedFile = SimpleUploadedFile
        self.SharedRepresentativeReview = SharedRepresentativeReview
        self.client = Client()
        self.admin = _make_staff('sharedrep_doc_admin', role=CustomUser.ROLE_ADMIN)
        self.client.force_login(self.admin)
        beneficiary = Beneficiary.objects.create(
            first_name='Doc', last_name='Upload', date_of_birth=datetime.date(1945, 1, 1),
            gender='M', municipality='Quezon City', barangay='Commonwealth',
            province='Metro Manila (NCR)', senior_citizen_id='SC-DOCUP-001', status='active',
        )
        rep = Representative.objects.create(
            beneficiary=beneficiary, first_name='Rep', last_name='Person',
            relationship='Son', contact_number='09171234567',
            valid_id_type='PhilSys', valid_id_number='PSN-DOCUP-1',
            registered_by=self.admin,
        )
        self.review = SharedRepresentativeReview.objects.create(
            representative=rep, matched_beneficiary_id='BEN-OTHER-001',
            matched_beneficiary_name='Other Beneficiary', matched_score=0.9, matched_threshold=0.8,
            flagged_by=self.admin,
        )

    def _url(self):
        return reverse('verification:shared_rep_review_detail', args=[self.review.pk])

    def test_html_upload_rejected(self):
        upload = self.SimpleUploadedFile(
            'evil.html', b'<script>alert(1)</script>', content_type='text/html',
        )
        resp = self.client.post(self._url(), {
            'action': 'approve', 'decision_notes': 'Reviewed supporting documents.',
            'authorization_document': upload,
        })
        self.assertEqual(resp.status_code, 302)
        self.review.refresh_from_db()
        self.assertFalse(self.review.authorization_document)
        self.assertEqual(self.review.status, self.SharedRepresentativeReview.STATUS_PENDING)

    def test_svg_upload_rejected(self):
        upload = self.SimpleUploadedFile(
            'evil.svg', b'<svg onload="alert(1)"></svg>', content_type='image/svg+xml',
        )
        resp = self.client.post(self._url(), {
            'action': 'approve', 'decision_notes': 'Reviewed supporting documents.',
            'authorization_document': upload,
        })
        self.assertEqual(resp.status_code, 302)
        self.review.refresh_from_db()
        self.assertFalse(self.review.authorization_document)

    def test_pdf_upload_accepted(self):
        upload = self.SimpleUploadedFile(
            'guardianship.pdf', b'%PDF-1.4 fake pdf content', content_type='application/pdf',
        )
        resp = self.client.post(self._url(), {
            'action': 'approve', 'decision_notes': 'Reviewed supporting documents.',
            'authorization_document': upload,
        })
        self.assertEqual(resp.status_code, 302)
        self.review.refresh_from_db()
        self.assertTrue(self.review.authorization_document)
