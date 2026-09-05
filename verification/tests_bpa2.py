"""
BPA-2 — Controlled Biometric Evaluation Trial Workflow tests.

Covers: access control (admin-tier only), dataset lifecycle, ground-truth
independence from system decision (the false-accept/false-reject/manual-
review representability that makes future FAR/FRR measurement possible),
ZERO operational side effects (no VerificationAttempt, no ClaimRecord, no
change to live Analytics counts), server-authoritative timing, session-token
binding, and audit logging.

All biometric primitives (face detection, anti-spoof, PAD, embedding,
comparison) are mocked — this suite proves the WORKFLOW is correct, not the
ML pipeline itself (already covered elsewhere). No real face images or
impersonation testing are used, per BPA-2 §26.
"""
import datetime
import uuid
from unittest import mock

from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import CustomUser
from beneficiaries.models import Beneficiary
from logs.models import AuditLog
from verification.models import (
    EvaluationDataset,
    EvaluationTrial,
    VerificationAttempt,
    ClaimRecord,
    SystemConfig,
)
from verification import face_utils, views as verification_views


def _make_user(username, role):
    return CustomUser.objects.create_user(
        username=username, password='TestPass123!',
        role=role, employee_id=f'EMP-{username.upper()}',
    )


def _make_beneficiary(ben_id='BEN-BPA2-0001', sc_id='SC-BPA2-0001'):
    return Beneficiary.objects.create(
        beneficiary_id=ben_id,
        first_name='Target',
        last_name='Identity',
        senior_citizen_id=sc_id,
        date_of_birth='1945-01-01',
        gender='F',
        address='123 Test St',
        barangay='Test Barangay',
        municipality='Quezon City',
        province='Metro Manila',
    )


def _make_dataset(status=EvaluationDataset.STATUS_COLLECTING, **kwargs):
    defaults = dict(name='BPA-2 Test Dataset', protocol_version='protocol-v1', status=status)
    defaults.update(kwargs)
    if status == EvaluationDataset.STATUS_COMPLETED and 'completed_at' not in defaults:
        defaults['completed_at'] = timezone.now()
    return EvaluationDataset.objects.create(**defaults)


def _make_pending_trial(dataset, target_beneficiary, identity_ground_truth=EvaluationTrial.GROUND_TRUTH_GENUINE,
                         participant_code='P-001', target_identity_code='T-001'):
    return EvaluationTrial.objects.create(
        dataset=dataset,
        participant_code=participant_code,
        target_identity_code=target_identity_code,
        identity_ground_truth=identity_ground_truth,
        target_beneficiary=target_beneficiary,
    )


class _RunnerMocks:
    """Shared mock plumbing for driving the two-stage runner (BPA-2.1:
    evaluation_trial_liveness then evaluation_trial_run) without any real ML
    dependency. `liveness_passed`/`pad_suspicious` control the stage-1 gate;
    `score`/`comparison_success` control the stage-2 target comparison.

    encrypt_embedding/decrypt_embedding/cosine_similarity are mocked too —
    get_embedding returns a plain sentinel object (not a real vector), which
    is fine everywhere EXCEPT the real crypto/math those three would
    otherwise perform on it. The same-face binding mechanism itself is
    covered separately and thoroughly in tests_bpa2_1.py; here it always
    "passes" (cosine_similarity mocked to 1.0) so these tests stay focused
    on ground-truth/decision/safety-isolation behavior."""

    def __init__(self, score=0.95, liveness_passed=True, pad_suspicious=False, comparison_success=True):
        self.score = score
        self.liveness_passed = liveness_passed
        self.pad_suspicious = pad_suspicious
        self.comparison_success = comparison_success

    def __enter__(self):
        self._patches = [
            mock.patch('verification.views.load_image_from_bytes', return_value=object()),
            mock.patch('verification.views.detect_and_align_face', return_value=object()),
            mock.patch('verification.views.check_anti_spoofing', return_value={
                'passed': self.liveness_passed, 'score': 0.9 if self.liveness_passed else 0.05, 'reason': '',
            }),
            mock.patch('verification.views.run_full_liveness_check', return_value={
                'passed': self.liveness_passed, 'anti_spoof_passed': self.liveness_passed,
                'anti_spoof_score': 0.9 if self.liveness_passed else 0.05, 'challenge_completed': False,
                'liveness_score': 0.54 if self.liveness_passed else 0.03, 'reason': '',
            }),
            mock.patch('verification.views.get_embedding', return_value=object()),
            mock.patch('verification.views.encrypt_embedding', return_value=b'fake-encrypted'),
            mock.patch('verification.views.decrypt_embedding', return_value=object()),
            mock.patch('verification.views.cosine_similarity', return_value=1.0),
            mock.patch('verification.views.compare_with_all_embeddings', return_value={
                'success': self.comparison_success, 'score': self.score,
                'matched_template': 'primary', 'templates_checked': 1, 'all_scores': [],
            }),
            mock.patch('verification.views.compare_with_stored', return_value={
                'success': self.comparison_success, 'score': self.score,
                'matched_template': 'representative_primary', 'templates_checked': 1, 'all_scores': [],
            }),
            mock.patch('verification.views.check_representative_beneficiary_fallback', return_value={
                'blocked': False, 'score': 0.1, 'templates_checked': 1,
            }),
            mock.patch('verification.views.check_lookalike_escalation', return_value={
                'escalate': False, 'checked': 0, 'lookalike_threshold': 0.8, 'top_match': None,
            }),
        ]
        pad_result = mock.Mock(suspicious=self.pad_suspicious, score=0.1 if not self.pad_suspicious else 0.9)
        pad_detector = mock.Mock()
        pad_detector.analyze.return_value = pad_result
        pad_detector.analyze_sequence.return_value = pad_result
        self._patches.append(mock.patch('verification.views.PresentationAttackDetector', return_value=pad_detector))
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()


def _post_run(client, trial, extra=None):
    """Drives the full two-stage runner (BPA-2.1) and returns stage 2's
    response, for callers that only care about the end-to-end outcome."""
    trial.refresh_from_db()
    if not trial.liveness_proof_embedding and trial.trial_status == EvaluationTrial.TRIAL_STATUS_PENDING:
        client.get(reverse('verification:evaluation_trial_run', args=[trial.pk]))
        trial.refresh_from_db()
        client.post(reverse('verification:evaluation_trial_liveness', args=[trial.pk]), {
            'session_token': str(trial.evaluation_session_token),
            'neutral_image': 'data:image/jpeg;base64,AAAA',
        })
        trial.refresh_from_db()
        if trial.trial_status != EvaluationTrial.TRIAL_STATUS_PENDING:
            # Liveness itself denied/aborted the trial — nothing left to submit.
            return None
    data = {'session_token': str(trial.evaluation_session_token), 'image': 'data:image/jpeg;base64,AAAA'}
    if extra:
        data.update(extra)
    return client.post(reverse('verification:evaluation_trial_run', args=[trial.pk]), data)


class EvaluationAccessControlTest(TestCase):
    """
    §20/§9, updated by the v2.1.19 UX pass section 19: Biometric Evaluation
    is Technical Administrator + President only. Plain Admin is denied too
    now — a deliberate tightening from the original "admin-tier" (President/
    Admin/IT) gate; see docs/TECHNICAL-ADMINISTRATOR-ROLE-MODEL.md.
    """

    def setUp(self):
        self.staff = _make_user('bpa2_staff', CustomUser.ROLE_STAFF)
        self.admin = _make_user('bpa2_admin', CustomUser.ROLE_ADMIN)
        self.it = _make_user('bpa2_it', CustomUser.ROLE_IT)
        self.president = _make_user('bpa2_president', CustomUser.ROLE_PRESIDENT)
        self.client = Client()

    def test_staff_denied_dataset_list(self):
        self.client.force_login(self.staff)
        resp = self.client.get(reverse('verification:evaluation_dataset_list'))
        self.assertRedirects(resp, reverse('beneficiaries:dashboard'))

    def test_staff_denied_dataset_create(self):
        self.client.force_login(self.staff)
        resp = self.client.post(reverse('verification:evaluation_dataset_create'), {
            'name': 'x', 'protocol_version': 'v1',
        })
        self.assertRedirects(resp, reverse('beneficiaries:dashboard'))
        self.assertEqual(EvaluationDataset.objects.count(), 0)

    def test_admin_denied_dataset_list(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('verification:evaluation_dataset_list'))
        self.assertRedirects(resp, reverse('beneficiaries:dashboard'))

    def test_it_allowed_dataset_list(self):
        self.client.force_login(self.it)
        resp = self.client.get(reverse('verification:evaluation_dataset_list'))
        self.assertEqual(resp.status_code, 200)

    def test_president_allowed_dataset_list(self):
        """President keeps read-only oversight of Biometric Evaluation."""
        self.client.force_login(self.president)
        resp = self.client.get(reverse('verification:evaluation_dataset_list'))
        self.assertEqual(resp.status_code, 200)

    def test_president_denied_dataset_create(self):
        """Owner-approved policy (FINAL PRE-EXE COMPLETION checkpoint,
        section 3): President is read-only for Biometric Evaluation — write
        actions (create/start/finalize/archive a dataset; set up/run/abort/
        withdraw a trial) are restricted to the Technical Administrator.
        A direct POST must be denied server-side, not merely hidden in the UI."""
        self.client.force_login(self.president)
        resp = self.client.post(reverse('verification:evaluation_dataset_create'), {
            'name': 'x', 'protocol_version': 'v1', 'purpose': EvaluationDataset.PURPOSE_QA_SYNTHETIC,
        })
        self.assertEqual(EvaluationDataset.objects.count(), 0)

    def test_president_denied_dataset_start(self):
        self.client.force_login(self.president)
        dataset = _make_dataset(status=EvaluationDataset.STATUS_DRAFT)
        resp = self.client.post(reverse('verification:evaluation_dataset_start', args=[dataset.pk]))
        dataset.refresh_from_db()
        self.assertEqual(dataset.status, EvaluationDataset.STATUS_DRAFT)

    def test_staff_denied_trial_run(self):
        dataset = _make_dataset()
        beneficiary = _make_beneficiary()
        trial = _make_pending_trial(dataset, beneficiary)
        self.client.force_login(self.staff)
        resp = self.client.get(reverse('verification:evaluation_trial_run', args=[trial.pk]))
        self.assertRedirects(resp, reverse('beneficiaries:dashboard'))
        trial.refresh_from_db()
        self.assertIsNone(trial.evaluation_session_token)


class EvaluationDatasetLifecycleTest(TestCase):
    """§6/§7 — draft -> collecting -> completed -> archived, with completeness guards."""

    def setUp(self):
        self.admin = _make_user('bpa2_lifecycle_admin', CustomUser.ROLE_IT)
        self.client = Client()
        self.client.force_login(self.admin)
        self.beneficiary = _make_beneficiary()

    def test_create_dataset_is_draft(self):
        resp = self.client.post(reverse('verification:evaluation_dataset_create'), {
            'name': 'Study A', 'protocol_version': 'proto-1', 'description': 'x',
            'purpose': EvaluationDataset.PURPOSE_QA_SYNTHETIC,
        })
        dataset = EvaluationDataset.objects.get(name='Study A')
        self.assertEqual(dataset.status, EvaluationDataset.STATUS_DRAFT)
        self.assertEqual(dataset.created_by, self.admin)

    def test_finalize_blocked_with_zero_trials(self):
        dataset = _make_dataset()
        resp = self.client.post(reverse('verification:evaluation_dataset_finalize', args=[dataset.pk]))
        dataset.refresh_from_db()
        self.assertEqual(dataset.status, EvaluationDataset.STATUS_COLLECTING)

    def test_finalize_blocked_with_pending_trial(self):
        dataset = _make_dataset()
        _make_pending_trial(dataset, self.beneficiary)
        self.client.post(reverse('verification:evaluation_dataset_finalize', args=[dataset.pk]))
        dataset.refresh_from_db()
        self.assertEqual(dataset.status, EvaluationDataset.STATUS_COLLECTING)

    def test_finalize_succeeds_once_all_trials_resolved(self):
        dataset = _make_dataset()
        trial = _make_pending_trial(dataset, self.beneficiary)
        self.client.post(reverse('verification:evaluation_trial_abort', args=[trial.pk]))
        resp = self.client.post(reverse('verification:evaluation_dataset_finalize', args=[dataset.pk]))
        dataset.refresh_from_db()
        self.assertEqual(dataset.status, EvaluationDataset.STATUS_COMPLETED)
        self.assertIsNotNone(dataset.completed_at)

    def test_trial_cannot_be_created_when_dataset_not_collecting(self):
        dataset = _make_dataset(status=EvaluationDataset.STATUS_DRAFT)
        resp = self.client.post(reverse('verification:evaluation_trial_setup', args=[dataset.pk]), {
            'action': 'create_trial',
            'participant_code': 'P-1', 'target_identity_code': 'T-1',
            'identity_ground_truth': EvaluationTrial.GROUND_TRUTH_GENUINE,
        })
        self.assertEqual(EvaluationTrial.objects.count(), 0)

    def test_archive_only_from_completed(self):
        dataset = _make_dataset()  # collecting
        self.client.post(reverse('verification:evaluation_dataset_archive', args=[dataset.pk]))
        dataset.refresh_from_db()
        self.assertEqual(dataset.status, EvaluationDataset.STATUS_COLLECTING)  # unchanged


class GroundTruthRecordedBeforeSystemResultTest(TestCase):
    """§8/§11 — ground truth is locked at setup, before any biometric processing."""

    def setUp(self):
        self.admin = _make_user('bpa2_gt_admin', CustomUser.ROLE_IT)
        self.client = Client()
        self.client.force_login(self.admin)
        self.dataset = _make_dataset()
        self.beneficiary = _make_beneficiary()

    def test_trial_created_pending_with_no_system_result_yet(self):
        resp = self.client.post(reverse('verification:evaluation_trial_setup', args=[self.dataset.pk]), {
            'action': 'create_trial',
            'participant_code': 'P-IMPOSTOR', 'target_identity_code': 'T-0001',
            'identity_ground_truth': EvaluationTrial.GROUND_TRUTH_IMPOSTOR,
            'presentation_ground_truth': EvaluationTrial.PRESENTATION_NOT_TESTED,
            'target_beneficiary_id': str(self.beneficiary.pk),
        })
        trial = EvaluationTrial.objects.get(participant_code='P-IMPOSTOR')
        self.assertEqual(trial.trial_status, EvaluationTrial.TRIAL_STATUS_PENDING)
        self.assertIsNone(trial.system_decision)
        self.assertIsNone(trial.similarity_score)
        self.assertEqual(trial.identity_ground_truth, EvaluationTrial.GROUND_TRUTH_IMPOSTOR)


class SafetyIsolationTest(TestCase):
    """§4/§5/§25 — a controlled trial NEVER creates VerificationAttempt/ClaimRecord
    and NEVER changes operational Analytics counts, regardless of outcome."""

    def setUp(self):
        self.admin = _make_user('bpa2_safety_admin', CustomUser.ROLE_IT)
        self.client = Client()
        self.client.force_login(self.admin)
        self.dataset = _make_dataset()
        self.beneficiary = _make_beneficiary()

    def _run_trial(self, ground_truth, score):
        trial = _make_pending_trial(self.dataset, self.beneficiary, identity_ground_truth=ground_truth)
        with _RunnerMocks(score=score):
            _post_run(self.client, trial)
        trial.refresh_from_db()
        return trial

    def test_no_verification_attempt_or_claim_created_for_false_accept(self):
        va_before = VerificationAttempt.objects.count()
        claim_before = ClaimRecord.objects.count()

        # IMPOSTOR ground truth + a high score that the shared decision helper
        # will call VERIFIED -> this is exactly a controlled false accept.
        trial = self._run_trial(EvaluationTrial.GROUND_TRUTH_IMPOSTOR, score=0.95)

        self.assertEqual(trial.system_decision, VerificationAttempt.DECISION_VERIFIED)
        self.assertEqual(VerificationAttempt.objects.count(), va_before)
        self.assertEqual(ClaimRecord.objects.count(), claim_before)

    def test_operational_analytics_counts_unchanged_after_trials(self):
        from verification.analytics import get_executive_metrics
        before = get_executive_metrics()
        va_before = VerificationAttempt.objects.count()

        self._run_trial(EvaluationTrial.GROUND_TRUTH_GENUINE, score=0.95)   # VERIFIED
        self._run_trial(EvaluationTrial.GROUND_TRUTH_GENUINE, score=0.10)   # NOT_VERIFIED
        self._run_trial(EvaluationTrial.GROUND_TRUTH_IMPOSTOR, score=0.95)  # false accept

        after = get_executive_metrics()
        self.assertEqual(VerificationAttempt.objects.count(), va_before)
        self.assertEqual(before['total_verifications'], after['total_verifications'])
        self.assertEqual(before['verified_count'], after['verified_count'])
        self.assertEqual(before['manual_review_count'], after['manual_review_count'])
        self.assertEqual(before['claims_count'], after['claims_count'])


class GroundTruthDecisionCombinationsTest(TestCase):
    """§26/§27/§28 — false accept, false reject, and manual review (both ground
    truths) are all safely representable, with zero side effects each time."""

    def setUp(self):
        self.admin = _make_user('bpa2_combo_admin', CustomUser.ROLE_IT)
        self.client = Client()
        self.client.force_login(self.admin)
        self.dataset = _make_dataset()
        self.beneficiary = _make_beneficiary()

    def _run(self, ground_truth, score):
        trial = _make_pending_trial(self.dataset, self.beneficiary, identity_ground_truth=ground_truth)
        self.client.get(reverse('verification:evaluation_trial_run', args=[trial.pk]))
        trial.refresh_from_db()
        with _RunnerMocks(score=score):
            _post_run(self.client, trial)
        trial.refresh_from_db()
        return trial

    def test_false_accept_impostor_verified(self):
        trial = self._run(EvaluationTrial.GROUND_TRUTH_IMPOSTOR, score=0.95)
        self.assertEqual(trial.identity_ground_truth, EvaluationTrial.GROUND_TRUTH_IMPOSTOR)
        self.assertEqual(trial.system_decision, VerificationAttempt.DECISION_VERIFIED)
        self.assertEqual(trial.trial_status, EvaluationTrial.TRIAL_STATUS_COMPLETED)
        self.assertEqual(ClaimRecord.objects.count(), 0)

    def test_false_reject_genuine_not_verified(self):
        trial = self._run(EvaluationTrial.GROUND_TRUTH_GENUINE, score=0.10)
        self.assertEqual(trial.identity_ground_truth, EvaluationTrial.GROUND_TRUTH_GENUINE)
        self.assertEqual(trial.system_decision, VerificationAttempt.DECISION_NOT_VERIFIED)
        self.assertEqual(ClaimRecord.objects.count(), 0)

    def test_genuine_manual_review(self):
        threshold = SystemConfig.get_threshold()
        trial = self._run(EvaluationTrial.GROUND_TRUTH_GENUINE, score=threshold + 0.001)
        self.assertEqual(trial.system_decision, VerificationAttempt.DECISION_MANUAL_REVIEW)

    def test_impostor_manual_review(self):
        threshold = SystemConfig.get_threshold()
        trial = self._run(EvaluationTrial.GROUND_TRUTH_IMPOSTOR, score=threshold + 0.001)
        self.assertEqual(trial.system_decision, VerificationAttempt.DECISION_MANUAL_REVIEW)
        self.assertEqual(trial.identity_ground_truth, EvaluationTrial.GROUND_TRUTH_IMPOSTOR)


class PreComparisonDenialTest(TestCase):
    """§12 — a liveness/PAD gate failure denies BEFORE comparison: similarity_score
    stays NULL, liveness/PAD outputs are retained, trial is still COMPLETED."""

    def setUp(self):
        self.admin = _make_user('bpa2_denial_admin', CustomUser.ROLE_IT)
        self.client = Client()
        self.client.force_login(self.admin)
        self.dataset = _make_dataset()
        self.beneficiary = _make_beneficiary()

    def test_anti_spoof_failure_denies_before_comparison(self):
        trial = _make_pending_trial(self.dataset, self.beneficiary)
        self.client.get(reverse('verification:evaluation_trial_run', args=[trial.pk]))
        trial.refresh_from_db()
        with _RunnerMocks(liveness_passed=False):
            _post_run(self.client, trial)
        trial.refresh_from_db()
        self.assertEqual(trial.system_decision, VerificationAttempt.DECISION_DENIED)
        self.assertIsNone(trial.similarity_score)
        self.assertEqual(trial.trial_status, EvaluationTrial.TRIAL_STATUS_COMPLETED)
        self.assertFalse(trial.liveness_passed)
        self.assertIsNotNone(trial.liveness_score)

    def test_pad_suspicious_denies_before_comparison(self):
        trial = _make_pending_trial(self.dataset, self.beneficiary)
        self.client.get(reverse('verification:evaluation_trial_run', args=[trial.pk]))
        trial.refresh_from_db()
        with _RunnerMocks(pad_suspicious=True):
            _post_run(self.client, trial)
        trial.refresh_from_db()
        self.assertEqual(trial.system_decision, VerificationAttempt.DECISION_DENIED)
        self.assertIsNone(trial.similarity_score)


class NoResolvedTargetAbortsTest(TestCase):
    def setUp(self):
        self.admin = _make_user('bpa2_notarget_admin', CustomUser.ROLE_IT)
        self.client = Client()
        self.client.force_login(self.admin)
        self.dataset = _make_dataset()

    def test_trial_with_no_target_identity_aborts_not_completes(self):
        trial = EvaluationTrial.objects.create(
            dataset=self.dataset,
            participant_code='P-NOTARGET', target_identity_code='T-UNRESOLVED',
            identity_ground_truth=EvaluationTrial.GROUND_TRUTH_GENUINE,
        )
        self.client.get(reverse('verification:evaluation_trial_run', args=[trial.pk]))
        trial.refresh_from_db()
        with _RunnerMocks(comparison_success=False):
            _post_run(self.client, trial)
        trial.refresh_from_db()
        self.assertEqual(trial.trial_status, EvaluationTrial.TRIAL_STATUS_ABORTED)
        self.assertIsNone(trial.system_decision)
        self.assertIsNone(trial.verification_duration_ms)


class ModelUnavailableAbortsEvaluationTest(TestCase):
    """
    Release-blocker fix (FaceNet fail-closed): if the real FaceNet model is
    unavailable when the evaluation runner needs an embedding (liveness
    capture or final-frame comparison), the trial must ABORT with a
    truthful technical-readiness error — never fabricate a similarity
    score, FAR/FRR-relevant decision, or ROC/EER-usable result from a
    mock/random embedding. get_embedding() raising FaceNetUnavailableError
    (instead of ever returning a mock model — see face_utils.py) is what
    makes this possible; these tests confirm the evaluation view layer
    honours that raise rather than swallowing it into a fabricated result.
    """

    def setUp(self):
        self.admin = _make_user('bpa2_modelunavail_admin', CustomUser.ROLE_IT)
        self.client = Client()
        self.client.force_login(self.admin)
        self.dataset = _make_dataset()
        self.beneficiary = _make_beneficiary()

    def _unavailable_embedding_patch(self):
        return mock.patch(
            'verification.views.get_embedding',
            side_effect=face_utils.FaceNetUnavailableError(face_utils._SAFE_UNAVAILABLE_MESSAGE),
        )

    def test_liveness_embedding_unavailable_aborts_trial(self):
        """Stage 1 (liveness/neutral-frame proof capture)."""
        trial = _make_pending_trial(self.dataset, self.beneficiary)
        self.client.get(reverse('verification:evaluation_trial_run', args=[trial.pk]))
        trial.refresh_from_db()

        with _RunnerMocks():
            with self._unavailable_embedding_patch():
                self.client.post(
                    reverse('verification:evaluation_trial_liveness', args=[trial.pk]),
                    {
                        'session_token': str(trial.evaluation_session_token),
                        'neutral_image': 'data:image/jpeg;base64,AAAA',
                    },
                )
        trial.refresh_from_db()
        self.assertEqual(trial.trial_status, EvaluationTrial.TRIAL_STATUS_ABORTED)
        self.assertIsNone(trial.system_decision)
        self.assertIsNone(trial.similarity_score)
        self.assertIsNone(trial.liveness_proof_embedding)

    def test_final_frame_embedding_unavailable_aborts_trial(self):
        """Stage 2 (final-frame target comparison) — stage 1 succeeds normally
        first, then the model becomes unavailable for the final frame."""
        trial = _make_pending_trial(self.dataset, self.beneficiary)
        self.client.get(reverse('verification:evaluation_trial_run', args=[trial.pk]))
        trial.refresh_from_db()

        with _RunnerMocks():
            self.client.post(
                reverse('verification:evaluation_trial_liveness', args=[trial.pk]),
                {
                    'session_token': str(trial.evaluation_session_token),
                    'neutral_image': 'data:image/jpeg;base64,AAAA',
                },
            )
        trial.refresh_from_db()
        self.assertEqual(trial.trial_status, EvaluationTrial.TRIAL_STATUS_PENDING)
        self.assertIsNotNone(trial.liveness_proof_embedding)

        with _RunnerMocks():
            with self._unavailable_embedding_patch():
                self.client.post(
                    reverse('verification:evaluation_trial_run', args=[trial.pk]),
                    {
                        'session_token': str(trial.evaluation_session_token),
                        'image': 'data:image/jpeg;base64,AAAA',
                    },
                )
        trial.refresh_from_db()
        self.assertEqual(trial.trial_status, EvaluationTrial.TRIAL_STATUS_ABORTED)
        self.assertIsNone(trial.system_decision)
        self.assertIsNone(trial.similarity_score)
        self.assertIsNone(trial.verification_duration_ms)
        # The liveness proof must not survive an aborted trial (data
        # minimization — mirrors _abort_trial's docstring).
        self.assertIsNone(trial.liveness_proof_embedding)


class EvaluationDecisionParityTest(TestCase):
    """§18 — production (verify_submit) and the evaluation trial workflow use
    the literal SAME shared function object, not two independently-maintained
    copies that could silently drift."""

    def test_views_module_uses_the_shared_face_utils_function(self):
        self.assertIs(verification_views.decide_base_outcome, face_utils.decide_base_outcome)

    def test_decision_zone_boundaries(self):
        threshold, auto = 0.75, 0.88
        cases = [
            (0.88, 'verified'), (0.99, 'verified'),
            (0.87, 'manual_review'), (0.75, 'manual_review'),
            (0.74, 'manual_review'), (0.6375, 'manual_review'),  # review_band = 0.75*0.85
            (0.63, 'not_verified'), (0.0, 'not_verified'),
        ]
        for score, expected in cases:
            self.assertEqual(
                face_utils.decide_base_outcome(score, threshold, auto), expected,
                f'score={score} expected {expected}',
            )


class EvaluationTimingTest(TestCase):
    """§29 — deterministic, server-authoritative CONTROLLED VERIFICATION ELAPSED
    TIME. All clocks are fixed constants (never wall-clock speed dependent)."""

    def setUp(self):
        self.admin = _make_user('bpa2_timing_admin', CustomUser.ROLE_IT)
        self.client = Client()
        self.client.force_login(self.admin)
        self.dataset = _make_dataset()
        self.beneficiary = _make_beneficiary()
        self.t0 = timezone.now().replace(microsecond=0)

    def _run_with_fixed_clock(self, trial, elapsed_seconds, **runner_kwargs):
        trial.evaluation_started_at = self.t0
        trial.evaluation_session_token = uuid.uuid4()
        trial.save(update_fields=['evaluation_started_at', 'evaluation_session_token'])
        t1 = self.t0 + datetime.timedelta(seconds=elapsed_seconds)
        with mock.patch('verification.views.timezone.now', return_value=t1), _RunnerMocks(**runner_kwargs):
            _post_run(self.client, trial)
        trial.refresh_from_db()
        return trial

    def test_verified_trial_duration_matches_fixed_clock_delta(self):
        trial = _make_pending_trial(self.dataset, self.beneficiary)
        trial = self._run_with_fixed_clock(trial, elapsed_seconds=2.5, score=0.95)
        self.assertEqual(trial.system_decision, VerificationAttempt.DECISION_VERIFIED)
        self.assertEqual(trial.verification_duration_ms, 2500)

    def test_manual_review_trial_duration_matches_fixed_clock_delta(self):
        threshold = SystemConfig.get_threshold()
        trial = _make_pending_trial(self.dataset, self.beneficiary)
        trial = self._run_with_fixed_clock(trial, elapsed_seconds=1.2, score=threshold + 0.001)
        self.assertEqual(trial.system_decision, VerificationAttempt.DECISION_MANUAL_REVIEW)
        self.assertEqual(trial.verification_duration_ms, 1200)

    def test_not_verified_trial_duration_matches_fixed_clock_delta(self):
        trial = _make_pending_trial(self.dataset, self.beneficiary)
        trial = self._run_with_fixed_clock(trial, elapsed_seconds=0.8, score=0.10)
        self.assertEqual(trial.system_decision, VerificationAttempt.DECISION_NOT_VERIFIED)
        self.assertEqual(trial.verification_duration_ms, 800)

    def test_denied_before_comparison_duration_and_null_similarity(self):
        trial = _make_pending_trial(self.dataset, self.beneficiary)
        trial = self._run_with_fixed_clock(trial, elapsed_seconds=0.4, liveness_passed=False)
        self.assertEqual(trial.system_decision, VerificationAttempt.DECISION_DENIED)
        self.assertIsNone(trial.similarity_score)
        self.assertEqual(trial.verification_duration_ms, 400)

    def test_duration_never_negative_on_clock_skew(self):
        """If evaluation_started_at is somehow after the completion timestamp
        (clock skew), duration must clamp to 0, never go negative."""
        trial = _make_pending_trial(self.dataset, self.beneficiary)
        trial = self._run_with_fixed_clock(trial, elapsed_seconds=-5, score=0.95)
        self.assertEqual(trial.verification_duration_ms, 0)

    def test_pending_trial_has_no_fabricated_duration(self):
        trial = _make_pending_trial(self.dataset, self.beneficiary)
        self.assertIsNone(trial.verification_duration_ms)

    def test_aborted_trial_has_no_fabricated_duration(self):
        trial = _make_pending_trial(self.dataset, self.beneficiary)
        self.client.post(reverse('verification:evaluation_trial_abort', args=[trial.pk]))
        trial.refresh_from_db()
        self.assertEqual(trial.trial_status, EvaluationTrial.TRIAL_STATUS_ABORTED)
        self.assertIsNone(trial.verification_duration_ms)

    def test_session_token_mismatch_cannot_attach_to_wrong_trial(self):
        trial_a = _make_pending_trial(self.dataset, self.beneficiary, participant_code='P-A', target_identity_code='T-A')
        trial_b = _make_pending_trial(self.dataset, self.beneficiary, participant_code='P-B', target_identity_code='T-B')
        self.client.get(reverse('verification:evaluation_trial_run', args=[trial_a.pk]))
        self.client.get(reverse('verification:evaluation_trial_run', args=[trial_b.pk]))
        trial_a.refresh_from_db()
        trial_b.refresh_from_db()
        self.assertNotEqual(trial_a.evaluation_session_token, trial_b.evaluation_session_token)

        # Complete trial A's own liveness stage correctly first, so this
        # exercises the STAGE-2 token check specifically (see also
        # tests_bpa2_1.py for the equivalent stage-1 cross-trial test).
        with _RunnerMocks(score=0.95):
            self.client.post(reverse('verification:evaluation_trial_liveness', args=[trial_a.pk]), {
                'session_token': str(trial_a.evaluation_session_token),
                'neutral_image': 'data:image/jpeg;base64,AAAA',
            })
        trial_a.refresh_from_db()
        self.assertIsNotNone(trial_a.liveness_proof_embedding)

        # Attempt to submit trial B's token against trial A's final-submit endpoint.
        with _RunnerMocks(score=0.95):
            self.client.post(
                reverse('verification:evaluation_trial_run', args=[trial_a.pk]),
                {'session_token': str(trial_b.evaluation_session_token), 'image': 'data:image/jpeg;base64,AAAA'},
            )
        trial_a.refresh_from_db()
        self.assertEqual(trial_a.trial_status, EvaluationTrial.TRIAL_STATUS_PENDING)
        self.assertIsNone(trial_a.system_decision)

    def test_get_runner_page_does_not_reissue_token_or_reset_clock(self):
        """A page refresh (second GET) must not reset the server-authoritative
        start time — the timer must survive the whole capture flow."""
        trial = _make_pending_trial(self.dataset, self.beneficiary)
        self.client.get(reverse('verification:evaluation_trial_run', args=[trial.pk]))
        trial.refresh_from_db()
        first_token = trial.evaluation_session_token
        first_started_at = trial.evaluation_started_at

        self.client.get(reverse('verification:evaluation_trial_run', args=[trial.pk]))
        trial.refresh_from_db()
        self.assertEqual(trial.evaluation_session_token, first_token)
        self.assertEqual(trial.evaluation_started_at, first_started_at)


class EvaluationAuditLogTest(TestCase):
    """§21 — evaluation actions are auditable via existing AuditLog conventions,
    and are NEVER recorded as a live verification/claim/fraud event."""

    def setUp(self):
        self.admin = _make_user('bpa2_audit_admin', CustomUser.ROLE_IT)
        self.client = Client()
        self.client.force_login(self.admin)
        self.beneficiary = _make_beneficiary()

    def test_dataset_and_trial_lifecycle_actions_logged(self):
        resp = self.client.post(reverse('verification:evaluation_dataset_create'), {
            'name': 'Audit Study', 'protocol_version': 'v1',
            'purpose': EvaluationDataset.PURPOSE_QA_SYNTHETIC,
        })
        dataset = EvaluationDataset.objects.get(name='Audit Study')
        self.assertTrue(AuditLog.objects.filter(
            action=AuditLog.ACTION_EVALUATION_DATASET_CREATED, target_id=str(dataset.id),
        ).exists())

        self.client.post(reverse('verification:evaluation_dataset_start', args=[dataset.pk]))
        self.assertTrue(AuditLog.objects.filter(
            action=AuditLog.ACTION_EVALUATION_DATASET_STARTED, target_id=str(dataset.id),
        ).exists())

        trial = _make_pending_trial(dataset, self.beneficiary)
        self.client.get(reverse('verification:evaluation_trial_run', args=[trial.pk]))
        trial.refresh_from_db()
        with _RunnerMocks(score=0.95):
            _post_run(self.client, trial)
        self.assertTrue(AuditLog.objects.filter(
            action=AuditLog.ACTION_EVALUATION_TRIAL_COMPLETED, target_id=str(trial.id),
        ).exists())

    def test_evaluation_trial_completion_is_not_logged_as_live_verify_or_claim(self):
        dataset = _make_dataset()
        trial = _make_pending_trial(dataset, self.beneficiary)
        self.client.get(reverse('verification:evaluation_trial_run', args=[trial.pk]))
        trial.refresh_from_db()
        with _RunnerMocks(score=0.95):
            _post_run(self.client, trial)
        self.assertFalse(AuditLog.objects.filter(
            action=AuditLog.ACTION_VERIFY, target_id=str(trial.id),
        ).exists())
        self.assertFalse(AuditLog.objects.filter(action=AuditLog.ACTION_CLAIM).exists())

    def test_trial_abort_logged(self):
        dataset = _make_dataset()
        trial = _make_pending_trial(dataset, self.beneficiary)
        self.client.post(reverse('verification:evaluation_trial_abort', args=[trial.pk]))
        self.assertTrue(AuditLog.objects.filter(
            action=AuditLog.ACTION_EVALUATION_TRIAL_ABORTED, target_id=str(trial.id),
        ).exists())
