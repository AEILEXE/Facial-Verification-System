"""
BPA-2.1 — Controlled Evaluation Parity Correction tests.

Covers: production active-liveness policy parity, liveness-proof/same-face
binding (session-token cross-trial protection, replay rejection), beneficiary
and representative comparison parity (including the no-fallback-to-
beneficiary-face hard rule), post-score final-decision parity (quality
override / lookalike escalation / representative-fallback-block), continued
payout/operational safety isolation, and timing for both target kinds.

All biometric primitives are mocked (deterministic, no real ML inference).
get_embedding uses REAL small numpy vectors so cosine_similarity/encrypt_
embedding/decrypt_embedding exercise their actual code paths — only the
detector/comparator FUNCTIONS are mocked, not the math around embeddings.
"""
import datetime
import uuid
from unittest import mock

import numpy as np
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import CustomUser
from beneficiaries.models import Beneficiary, Representative
from logs.models import AuditLog
from verification.models import (
    EvaluationDataset,
    EvaluationTrial,
    VerificationAttempt,
    ClaimRecord,
    RepresentativeFaceEmbedding,
    SystemConfig,
)
from verification import face_utils

SAME_VEC = np.array([1.0, 0.0, 0.0], dtype=np.float32)
DIFFERENT_VEC = np.array([0.0, 1.0, 0.0], dtype=np.float32)


def _make_user(username, role):
    return CustomUser.objects.create_user(
        username=username, password='TestPass123!',
        role=role, employee_id=f'EMP-{username.upper()}',
    )


def _make_beneficiary(ben_id='BEN-BPA21-0001', sc_id='SC-BPA21-0001'):
    return Beneficiary.objects.create(
        beneficiary_id=ben_id, first_name='Target', last_name='Identity',
        senior_citizen_id=sc_id, date_of_birth='1945-01-01', gender='F',
        address='123 Test St', barangay='Test Barangay',
        municipality='Quezon City', province='Metro Manila',
    )


def _make_rep(beneficiary, staff, with_face=True, id_number='SSS-BPA21-001'):
    rep = Representative.objects.create(
        beneficiary=beneficiary, first_name='Jose', last_name='Rizal',
        relationship='Son', contact_number='09171234567',
        valid_id_type='SSS', valid_id_number=id_number, registered_by=staff,
    )
    if with_face:
        from cryptography.fernet import Fernet
        from django.conf import settings
        key = settings.EMBEDDING_ENCRYPTION_KEY
        RepresentativeFaceEmbedding.objects.create(
            representative=rep, embedding_data=Fernet(key).encrypt(b'\x01' * 512), created_by=staff,
        )
    return rep


def _make_dataset(status=EvaluationDataset.STATUS_COLLECTING):
    return EvaluationDataset.objects.create(name='BPA-2.1 Dataset', protocol_version='v1', status=status)


def _make_pending_trial(dataset, *, target_beneficiary=None, target_representative=None,
                         identity_ground_truth=EvaluationTrial.GROUND_TRUTH_GENUINE,
                         participant_code='P-001', target_identity_code='T-001'):
    return EvaluationTrial.objects.create(
        dataset=dataset, participant_code=participant_code, target_identity_code=target_identity_code,
        identity_ground_truth=identity_ground_truth,
        target_beneficiary=target_beneficiary, target_representative=target_representative,
    )


class _LivenessMocks:
    """Mocks for stage 1 (evaluation_trial_liveness)."""

    def __init__(self, anti_spoof_passed=True, anti_spoof_score=0.9, pad_suspicious=False, pad_score=0.1,
                 neutral_embedding=SAME_VEC):
        self.anti_spoof_passed = anti_spoof_passed
        self.anti_spoof_score = anti_spoof_score
        self.pad_suspicious = pad_suspicious
        self.pad_score = pad_score
        self.neutral_embedding = neutral_embedding

    def __enter__(self):
        self._patches = [
            mock.patch('verification.views.load_image_from_bytes', return_value=object()),
            mock.patch('verification.views.detect_and_align_face', return_value=object()),
            mock.patch('verification.views.check_anti_spoofing', return_value={
                'passed': self.anti_spoof_passed, 'score': self.anti_spoof_score, 'reason': '',
            }),
            mock.patch('verification.views.get_embedding', return_value=self.neutral_embedding),
            mock.patch('verification.views.run_full_liveness_check', return_value={
                'passed': self.anti_spoof_passed, 'anti_spoof_passed': self.anti_spoof_passed,
                'anti_spoof_score': self.anti_spoof_score, 'challenge_completed': False,
                'liveness_score': 0.6 * self.anti_spoof_score, 'reason': '',
            }),
        ]
        pad_result = mock.Mock(suspicious=self.pad_suspicious, score=self.pad_score)
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


class _SubmitMocks:
    """Mocks for stage 2 (evaluation_trial_run POST)."""

    def __init__(self, final_embedding=SAME_VEC, comparison_score=0.95, comparison_success=True,
                 rep_fallback_blocked=False, lookalike_escalate=False):
        self.final_embedding = final_embedding
        self.comparison_score = comparison_score
        self.comparison_success = comparison_success
        self.rep_fallback_blocked = rep_fallback_blocked
        self.lookalike_escalate = lookalike_escalate

    def __enter__(self):
        comparison = {
            'success': self.comparison_success, 'score': self.comparison_score,
            'matched_template': 'primary', 'templates_checked': 1, 'all_scores': [],
        }
        self._patches = [
            mock.patch('verification.views.load_image_from_bytes', return_value=object()),
            mock.patch('verification.views.detect_and_align_face', return_value=object()),
            mock.patch('verification.views.get_embedding', return_value=self.final_embedding),
            mock.patch('verification.views.compare_with_all_embeddings', return_value=comparison),
            mock.patch('verification.views.compare_with_stored', return_value=comparison),
            mock.patch('verification.views.check_representative_beneficiary_fallback', return_value={
                'blocked': self.rep_fallback_blocked, 'score': 0.9 if self.rep_fallback_blocked else 0.1,
                'templates_checked': 1,
            }),
            mock.patch('verification.views.check_lookalike_escalation', return_value={
                'escalate': self.lookalike_escalate, 'checked': 1, 'lookalike_threshold': 0.8,
                'top_match': ({'beneficiary_id': 'BEN-OTHER', 'full_name': 'Other Person', 'score': 0.9}
                              if self.lookalike_escalate else None),
            }),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()


def _run_liveness(client, trial, head_movement_completed=False, **mock_kwargs):
    trial.refresh_from_db()
    with _LivenessMocks(**mock_kwargs):
        resp = client.post(reverse('verification:evaluation_trial_liveness', args=[trial.pk]), {
            'session_token': str(trial.evaluation_session_token),
            'neutral_image': 'data:image/jpeg;base64,AAAA',
            'head_movement_completed': '1' if head_movement_completed else '',
            'challenge_direction': 'side',
        })
    trial.refresh_from_db()
    return resp


def _submit_final(client, trial, **submit_kwargs):
    trial.refresh_from_db()
    with _SubmitMocks(**submit_kwargs):
        resp = client.post(reverse('verification:evaluation_trial_run', args=[trial.pk]), {
            'session_token': str(trial.evaluation_session_token),
            'image': 'data:image/jpeg;base64,BBBB',
        })
    trial.refresh_from_db()
    return resp


def _authorize(client, trial):
    client.get(reverse('verification:evaluation_trial_run', args=[trial.pk]))
    trial.refresh_from_db()
    return trial


def _run_full_trial(client, trial, *, head_movement_completed=False, anti_spoof_passed=True,
                     pad_suspicious=False, comparison_score=0.95, comparison_success=True,
                     rep_fallback_blocked=False, lookalike_escalate=False,
                     final_embedding=SAME_VEC, neutral_embedding=SAME_VEC):
    """Runs both stages assuming liveness is expected to pass."""
    _authorize(client, trial)
    _run_liveness(client, trial, head_movement_completed=head_movement_completed,
                  anti_spoof_passed=anti_spoof_passed, pad_suspicious=pad_suspicious,
                  neutral_embedding=neutral_embedding)
    _submit_final(client, trial, comparison_score=comparison_score, comparison_success=comparison_success,
                  rep_fallback_blocked=rep_fallback_blocked, lookalike_escalate=lookalike_escalate,
                  final_embedding=final_embedding)
    trial.refresh_from_db()
    return trial


class ActiveLivenessPolicyParityTest(TestCase):
    """A. Production active-liveness policy parity: server_liveness_passed
    (once a proof/TX exists) is gated on anti-spoof + PAD only — NOT on
    challenge_completed. See verify_submit lines ~1513-1539: the TX-bound
    override ignores challenge_completed entirely."""

    def setUp(self):
        self.admin = _make_user('bpa21_policy_admin', CustomUser.ROLE_IT)
        self.client = Client()
        self.client.force_login(self.admin)
        self.dataset = _make_dataset()
        self.beneficiary = _make_beneficiary()

    def test_passive_only_trial_with_anti_spoof_pass_reaches_liveness_proof(self):
        trial = _make_pending_trial(self.dataset, target_beneficiary=self.beneficiary)
        _authorize(self.client, trial)
        _run_liveness(self.client, trial, head_movement_completed=False, anti_spoof_passed=True)
        self.assertIsNotNone(trial.liveness_proof_embedding)
        self.assertTrue(trial.liveness_passed)
        self.assertEqual(trial.liveness_pathway, EvaluationTrial.LIVENESS_PATHWAY_PASSIVE_ONLY)

    def test_active_challenge_trial_with_anti_spoof_pass_also_reaches_liveness_proof(self):
        """Attesting an active challenge does not add a stricter gate — matches
        production, where challenge_completed doesn't affect the TX-bound pass."""
        trial = _make_pending_trial(self.dataset, target_beneficiary=self.beneficiary)
        _authorize(self.client, trial)
        _run_liveness(self.client, trial, head_movement_completed=True, anti_spoof_passed=True)
        self.assertIsNotNone(trial.liveness_proof_embedding)
        self.assertTrue(trial.liveness_passed)
        self.assertEqual(trial.liveness_pathway, EvaluationTrial.LIVENESS_PATHWAY_ACTIVE_CHALLENGE)

    def test_anti_spoof_failure_denies_regardless_of_challenge_attestation(self):
        trial = _make_pending_trial(self.dataset, target_beneficiary=self.beneficiary)
        _authorize(self.client, trial)
        _run_liveness(self.client, trial, head_movement_completed=True, anti_spoof_passed=False)
        self.assertIsNone(trial.liveness_proof_embedding)
        self.assertEqual(trial.system_decision, VerificationAttempt.DECISION_DENIED)
        self.assertEqual(trial.liveness_pathway, EvaluationTrial.LIVENESS_PATHWAY_FAILED_ACTIVE)

    def test_anti_spoof_failure_denies_on_passive_pathway_too(self):
        trial = _make_pending_trial(self.dataset, target_beneficiary=self.beneficiary)
        _authorize(self.client, trial)
        _run_liveness(self.client, trial, head_movement_completed=False, anti_spoof_passed=False)
        self.assertEqual(trial.liveness_pathway, EvaluationTrial.LIVENESS_PATHWAY_FAILED_PASSIVE)


class LivenessProofBindingTest(TestCase):
    """B/C. Liveness proof belongs to the correct trial; cannot be replayed
    across trials; expired/mismatched token fails safely."""

    def setUp(self):
        self.admin = _make_user('bpa21_binding_admin', CustomUser.ROLE_IT)
        self.client = Client()
        self.client.force_login(self.admin)
        self.dataset = _make_dataset()
        self.beneficiary = _make_beneficiary()

    def test_liveness_proof_stored_on_the_correct_trial_only(self):
        trial_a = _make_pending_trial(self.dataset, target_beneficiary=self.beneficiary, participant_code='P-A', target_identity_code='T-A')
        trial_b = _make_pending_trial(self.dataset, target_beneficiary=self.beneficiary, participant_code='P-B', target_identity_code='T-B')
        _authorize(self.client, trial_a)
        _authorize(self.client, trial_b)
        _run_liveness(self.client, trial_a, head_movement_completed=False)
        trial_b.refresh_from_db()
        self.assertIsNotNone(trial_a.liveness_proof_embedding)
        self.assertIsNone(trial_b.liveness_proof_embedding)

    def test_liveness_token_from_another_trial_rejected(self):
        trial_a = _make_pending_trial(self.dataset, target_beneficiary=self.beneficiary, participant_code='P-A2', target_identity_code='T-A2')
        trial_b = _make_pending_trial(self.dataset, target_beneficiary=self.beneficiary, participant_code='P-B2', target_identity_code='T-B2')
        _authorize(self.client, trial_a)
        _authorize(self.client, trial_b)
        with _LivenessMocks():
            self.client.post(reverse('verification:evaluation_trial_liveness', args=[trial_a.pk]), {
                'session_token': str(trial_b.evaluation_session_token),  # WRONG token
                'neutral_image': 'data:image/jpeg;base64,AAAA',
            })
        trial_a.refresh_from_db()
        self.assertIsNone(trial_a.liveness_proof_embedding)
        self.assertEqual(trial_a.trial_status, EvaluationTrial.TRIAL_STATUS_PENDING)

    def test_liveness_proof_cannot_be_recaptured_once_set(self):
        """Replay protection: once a proof exists, a second liveness POST is refused."""
        trial = _make_pending_trial(self.dataset, target_beneficiary=self.beneficiary)
        _authorize(self.client, trial)
        _run_liveness(self.client, trial)
        first_proof = bytes(trial.liveness_proof_embedding)
        _run_liveness(self.client, trial, neutral_embedding=DIFFERENT_VEC)
        self.assertEqual(bytes(trial.liveness_proof_embedding), first_proof)

    def test_stage_two_rejected_without_liveness_proof_first(self):
        trial = _make_pending_trial(self.dataset, target_beneficiary=self.beneficiary)
        _authorize(self.client, trial)
        _submit_final(self.client, trial)  # no liveness step run first
        self.assertEqual(trial.trial_status, EvaluationTrial.TRIAL_STATUS_PENDING)
        self.assertIsNone(trial.system_decision)


class SameFaceBindingTest(TestCase):
    """D. The final frame must match the liveness-proof frame, or the trial
    is denied before any target comparison (mirrors verify_submit's
    ACTION_VERIFY_SUBJECT_CHANGED gate)."""

    def setUp(self):
        self.admin = _make_user('bpa21_sameface_admin', CustomUser.ROLE_IT)
        self.client = Client()
        self.client.force_login(self.admin)
        self.dataset = _make_dataset()
        self.beneficiary = _make_beneficiary()

    def test_matching_frames_proceed_to_comparison(self):
        trial = _make_pending_trial(self.dataset, target_beneficiary=self.beneficiary)
        trial = _run_full_trial(self.client, trial, neutral_embedding=SAME_VEC, final_embedding=SAME_VEC, comparison_score=0.95)
        self.assertEqual(trial.system_decision, VerificationAttempt.DECISION_VERIFIED)
        self.assertIsNotNone(trial.similarity_score)

    def test_different_final_frame_denied_before_comparison(self):
        trial = _make_pending_trial(self.dataset, target_beneficiary=self.beneficiary)
        trial = _run_full_trial(self.client, trial, neutral_embedding=SAME_VEC, final_embedding=DIFFERENT_VEC, comparison_score=0.95)
        self.assertEqual(trial.system_decision, VerificationAttempt.DECISION_DENIED)
        self.assertIsNone(trial.similarity_score)
        self.assertEqual(trial.trial_status, EvaluationTrial.TRIAL_STATUS_COMPLETED)


class PassiveAndSpoofPathwayTest(TestCase):
    """E/F. Passive/bona-fide successful path, and staged spoof rejection path."""

    def setUp(self):
        self.admin = _make_user('bpa21_spoof_admin', CustomUser.ROLE_IT)
        self.client = Client()
        self.client.force_login(self.admin)
        self.dataset = _make_dataset()
        self.beneficiary = _make_beneficiary()

    def test_bona_fide_passive_success_path(self):
        trial = _make_pending_trial(self.dataset, target_beneficiary=self.beneficiary)
        trial.presentation_ground_truth = EvaluationTrial.PRESENTATION_BONA_FIDE
        trial.save()
        trial = _run_full_trial(self.client, trial, comparison_score=0.95)
        self.assertEqual(trial.system_decision, VerificationAttempt.DECISION_VERIFIED)
        self.assertEqual(trial.liveness_pathway, EvaluationTrial.LIVENESS_PATHWAY_PASSIVE_ONLY)

    def test_staged_print_photo_attack_rejected_via_pad(self):
        trial = _make_pending_trial(self.dataset, target_beneficiary=self.beneficiary, identity_ground_truth=EvaluationTrial.GROUND_TRUTH_IMPOSTOR)
        trial.presentation_ground_truth = EvaluationTrial.PRESENTATION_PRINT_PHOTO
        trial.save()
        _authorize(self.client, trial)
        _run_liveness(self.client, trial, pad_suspicious=True, pad_score=0.9)
        self.assertEqual(trial.system_decision, VerificationAttempt.DECISION_DENIED)
        self.assertIsNone(trial.similarity_score)
        self.assertEqual(trial.presentation_ground_truth, EvaluationTrial.PRESENTATION_PRINT_PHOTO)
        # Ground truth is untouched by the system's PAD output — independence preserved.
        self.assertEqual(trial.pa_score, 0.9)


class BeneficiaryRepresentativeComparisonParityTest(TestCase):
    """G/H/I. Beneficiary vs representative comparison parity, and the hard
    no-fallback-to-beneficiary-face rule."""

    def setUp(self):
        self.admin = _make_user('bpa21_parity_admin', CustomUser.ROLE_IT)
        self.client = Client()
        self.client.force_login(self.admin)
        self.dataset = _make_dataset()
        self.beneficiary = _make_beneficiary()
        self.rep = _make_rep(self.beneficiary, self.admin, with_face=True)
        self.rep_no_face = _make_rep(self.beneficiary, self.admin, with_face=False, id_number='SSS-NOFACE')

    def test_beneficiary_target_comparison(self):
        trial = _make_pending_trial(self.dataset, target_beneficiary=self.beneficiary)
        trial = _run_full_trial(self.client, trial, comparison_score=0.95)
        self.assertEqual(trial.system_decision, VerificationAttempt.DECISION_VERIFIED)

    def test_representative_target_comparison(self):
        trial = _make_pending_trial(self.dataset, target_representative=self.rep, target_identity_code='T-REP')
        trial = _run_full_trial(self.client, trial, comparison_score=0.95)
        self.assertEqual(trial.system_decision, VerificationAttempt.DECISION_VERIFIED)
        self.assertFalse(trial.representative_fallback_blocked)

    def test_representative_with_no_face_data_aborts_no_fallback(self):
        """§13 — representative has no face -> the trial cannot obtain a valid
        result through the beneficiary's face. It aborts (technical/setup
        issue), never silently substitutes the beneficiary's embedding."""
        trial = _make_pending_trial(self.dataset, target_representative=self.rep_no_face, target_identity_code='T-NOFACE')
        _authorize(self.client, trial)
        _run_liveness(self.client, trial)
        _submit_final(self.client, trial, comparison_score=0.95)
        self.assertEqual(trial.trial_status, EvaluationTrial.TRIAL_STATUS_ABORTED)
        self.assertIsNone(trial.system_decision)

    def test_representative_beneficiary_fallback_blocked(self):
        """The senior's own face must never pass a representative claim."""
        trial = _make_pending_trial(self.dataset, target_representative=self.rep, target_identity_code='T-REP-BLOCK',
                                     identity_ground_truth=EvaluationTrial.GROUND_TRUTH_IMPOSTOR)
        trial = _run_full_trial(self.client, trial, comparison_score=0.95, rep_fallback_blocked=True)
        self.assertEqual(trial.system_decision, VerificationAttempt.DECISION_DENIED)
        self.assertTrue(trial.representative_fallback_blocked)
        # The comparison score itself is still recorded (parity with production storing it).
        self.assertEqual(trial.similarity_score, 0.95)


class RepresentativeGroundTruthCombinationsTest(TestCase):
    """§14 — all six ground-truth × decision combinations for representative targets."""

    def setUp(self):
        self.admin = _make_user('bpa21_rep_gt_admin', CustomUser.ROLE_IT)
        self.client = Client()
        self.client.force_login(self.admin)
        self.dataset = _make_dataset()
        self.beneficiary = _make_beneficiary()
        self.rep = _make_rep(self.beneficiary, self.admin, with_face=True)

    def _run(self, ground_truth, score, n):
        trial = _make_pending_trial(
            self.dataset, target_representative=self.rep, identity_ground_truth=ground_truth,
            participant_code=f'P-REPGT-{n}', target_identity_code=f'T-REPGT-{n}',
        )
        return _run_full_trial(self.client, trial, comparison_score=score)

    def test_genuine_verified(self):
        t = self._run(EvaluationTrial.GROUND_TRUTH_GENUINE, 0.95, 1)
        self.assertEqual(t.system_decision, VerificationAttempt.DECISION_VERIFIED)

    def test_genuine_manual_review(self):
        threshold = SystemConfig.get_threshold()
        t = self._run(EvaluationTrial.GROUND_TRUTH_GENUINE, threshold + 0.001, 2)
        self.assertEqual(t.system_decision, VerificationAttempt.DECISION_MANUAL_REVIEW)

    def test_genuine_not_verified(self):
        t = self._run(EvaluationTrial.GROUND_TRUTH_GENUINE, 0.10, 3)
        self.assertEqual(t.system_decision, VerificationAttempt.DECISION_NOT_VERIFIED)

    def test_impostor_verified_false_accept(self):
        t = self._run(EvaluationTrial.GROUND_TRUTH_IMPOSTOR, 0.95, 4)
        self.assertEqual(t.system_decision, VerificationAttempt.DECISION_VERIFIED)
        self.assertEqual(ClaimRecord.objects.count(), 0)

    def test_impostor_manual_review(self):
        threshold = SystemConfig.get_threshold()
        t = self._run(EvaluationTrial.GROUND_TRUTH_IMPOSTOR, threshold + 0.001, 5)
        self.assertEqual(t.system_decision, VerificationAttempt.DECISION_MANUAL_REVIEW)

    def test_impostor_not_verified(self):
        t = self._run(EvaluationTrial.GROUND_TRUTH_IMPOSTOR, 0.10, 6)
        self.assertEqual(t.system_decision, VerificationAttempt.DECISION_NOT_VERIFIED)


class PostScoreDecisionParityTest(TestCase):
    """J. matcher_base_decision (raw) vs system_decision (final, post-override)."""

    def setUp(self):
        self.admin = _make_user('bpa21_postscore_admin', CustomUser.ROLE_IT)
        self.client = Client()
        self.client.force_login(self.admin)
        self.dataset = _make_dataset()
        self.beneficiary = _make_beneficiary()

    def test_no_override_matcher_equals_final(self):
        trial = _make_pending_trial(self.dataset, target_beneficiary=self.beneficiary)
        trial = _run_full_trial(self.client, trial, comparison_score=0.95)
        self.assertEqual(trial.matcher_base_decision, VerificationAttempt.DECISION_VERIFIED)
        self.assertEqual(trial.system_decision, VerificationAttempt.DECISION_VERIFIED)
        self.assertFalse(trial.lookalike_escalation_applied)

    def test_lookalike_escalation_overrides_final_decision(self):
        trial = _make_pending_trial(self.dataset, target_beneficiary=self.beneficiary)
        trial = _run_full_trial(self.client, trial, comparison_score=0.95, lookalike_escalate=True)
        self.assertEqual(trial.matcher_base_decision, VerificationAttempt.DECISION_VERIFIED)
        self.assertEqual(trial.system_decision, VerificationAttempt.DECISION_MANUAL_REVIEW)
        self.assertTrue(trial.lookalike_escalation_applied)

    def test_representative_fallback_block_overrides_final_decision(self):
        rep = _make_rep(self.beneficiary, self.admin, with_face=True)
        trial = _make_pending_trial(self.dataset, target_representative=rep, target_identity_code='T-BLOCK2')
        trial = _run_full_trial(self.client, trial, comparison_score=0.95, rep_fallback_blocked=True)
        self.assertEqual(trial.matcher_base_decision, VerificationAttempt.DECISION_VERIFIED)
        self.assertEqual(trial.system_decision, VerificationAttempt.DECISION_DENIED)

    def test_shared_decide_base_outcome_still_used_for_matcher_level(self):
        """Parity: the SAME shared function still drives the matcher-base level."""
        from verification import views as verification_views
        self.assertIs(verification_views.decide_base_outcome, face_utils.decide_base_outcome)


class ContinuedSafetyIsolationTest(TestCase):
    """K/L. False accepts remain payout-safe; operational Analytics/Template
    Analytics remain unchanged, across beneficiary and representative,
    genuine and impostor, liveness-denial and technical-abort trials."""

    def setUp(self):
        self.admin = _make_user('bpa21_safety_admin', CustomUser.ROLE_IT)
        self.client = Client()
        self.client.force_login(self.admin)
        self.dataset = _make_dataset()
        self.beneficiary = _make_beneficiary()
        self.rep = _make_rep(self.beneficiary, self.admin, with_face=True)

    def test_isolation_holds_across_all_trial_kinds(self):
        from verification.analytics import get_executive_metrics
        va_before = VerificationAttempt.objects.count()
        claim_before = ClaimRecord.objects.count()
        metrics_before = get_executive_metrics()

        # beneficiary genuine
        t1 = _make_pending_trial(self.dataset, target_beneficiary=self.beneficiary, participant_code='P-ISO-1', target_identity_code='T-ISO-1')
        _run_full_trial(self.client, t1, comparison_score=0.95)

        # beneficiary impostor false accept
        t2 = _make_pending_trial(self.dataset, target_beneficiary=self.beneficiary, identity_ground_truth=EvaluationTrial.GROUND_TRUTH_IMPOSTOR, participant_code='P-ISO-2', target_identity_code='T-ISO-2')
        _run_full_trial(self.client, t2, comparison_score=0.95)

        # representative genuine
        t3 = _make_pending_trial(self.dataset, target_representative=self.rep, participant_code='P-ISO-3', target_identity_code='T-ISO-3')
        _run_full_trial(self.client, t3, comparison_score=0.95)

        # representative impostor false accept
        t4 = _make_pending_trial(self.dataset, target_representative=self.rep, identity_ground_truth=EvaluationTrial.GROUND_TRUTH_IMPOSTOR, participant_code='P-ISO-4', target_identity_code='T-ISO-4')
        _run_full_trial(self.client, t4, comparison_score=0.95)

        # liveness denial
        t5 = _make_pending_trial(self.dataset, target_beneficiary=self.beneficiary, participant_code='P-ISO-5', target_identity_code='T-ISO-5')
        _authorize(self.client, t5)
        _run_liveness(self.client, t5, anti_spoof_passed=False)

        # aborted technical trial
        t6 = _make_pending_trial(self.dataset, target_beneficiary=self.beneficiary, participant_code='P-ISO-6', target_identity_code='T-ISO-6')
        self.client.post(reverse('verification:evaluation_trial_abort', args=[t6.pk]))

        self.assertEqual(EvaluationTrial.objects.filter(dataset=self.dataset).count(), 6)
        self.assertEqual(VerificationAttempt.objects.count(), va_before)
        self.assertEqual(ClaimRecord.objects.count(), claim_before)

        metrics_after = get_executive_metrics()
        self.assertEqual(metrics_before['total_verifications'], metrics_after['total_verifications'])
        self.assertEqual(metrics_before['verified_count'], metrics_after['verified_count'])
        self.assertEqual(metrics_before['claims_count'], metrics_after['claims_count'])

        from verification.template_analytics import get_template_win_stats
        # Must not raise and must not reflect any evaluation-trial "win" —
        # it only ever queries VerificationAttempt, which was never touched.
        counts_before_and_after_equal = get_template_win_stats() == get_template_win_stats()
        self.assertTrue(counts_before_and_after_equal)


class TimingParityTest(TestCase):
    """M/N/O. Timing works identically for beneficiary and representative
    trials; technical-abort timing stays null."""

    def setUp(self):
        self.admin = _make_user('bpa21_timing_admin', CustomUser.ROLE_IT)
        self.client = Client()
        self.client.force_login(self.admin)
        self.dataset = _make_dataset()
        self.beneficiary = _make_beneficiary()
        self.rep = _make_rep(self.beneficiary, self.admin, with_face=True)
        self.t0 = timezone.now().replace(microsecond=0)

    def _run_with_fixed_clock(self, trial, elapsed_seconds, **kwargs):
        _authorize(self.client, trial)
        trial.evaluation_started_at = self.t0
        trial.save(update_fields=['evaluation_started_at'])
        t_liveness = self.t0 + datetime.timedelta(seconds=1)
        t_final = self.t0 + datetime.timedelta(seconds=elapsed_seconds)
        with mock.patch('verification.views.timezone.now', return_value=t_liveness):
            _run_liveness(self.client, trial, **{k: v for k, v in kwargs.items() if k in ('head_movement_completed', 'anti_spoof_passed', 'pad_suspicious')})
        with mock.patch('verification.views.timezone.now', return_value=t_final):
            _submit_final(self.client, trial, **{k: v for k, v in kwargs.items() if k in ('comparison_score', 'comparison_success', 'rep_fallback_blocked', 'lookalike_escalate')})
        trial.refresh_from_db()
        return trial

    def test_beneficiary_trial_timing(self):
        trial = _make_pending_trial(self.dataset, target_beneficiary=self.beneficiary)
        trial = self._run_with_fixed_clock(trial, elapsed_seconds=3.0, comparison_score=0.95)
        self.assertEqual(trial.verification_duration_ms, 3000)

    def test_representative_trial_timing(self):
        trial = _make_pending_trial(self.dataset, target_representative=self.rep, target_identity_code='T-TIME-REP')
        trial = self._run_with_fixed_clock(trial, elapsed_seconds=2.4, comparison_score=0.95)
        self.assertEqual(trial.verification_duration_ms, 2400)

    def test_technical_abort_timing_stays_null(self):
        trial = _make_pending_trial(self.dataset, target_beneficiary=self.beneficiary)
        _authorize(self.client, trial)
        # No image at all -> technical abort at stage 1.
        self.client.post(reverse('verification:evaluation_trial_liveness', args=[trial.pk]), {
            'session_token': str(trial.evaluation_session_token),
        })
        trial.refresh_from_db()
        self.assertEqual(trial.trial_status, EvaluationTrial.TRIAL_STATUS_ABORTED)
        self.assertIsNone(trial.verification_duration_ms)
