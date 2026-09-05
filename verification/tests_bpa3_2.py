"""
BPA-3.2 — Cosine similarity schema alignment tests.

Covers: EvaluationTrial.similarity_score's stored validation domain now
matching the true cosine-similarity domain [-1, 1] (face_utils.cosine_similarity
is never clamped), instead of the stale [0, 1] domain left over from BPA-3.1.

Scope is deliberately narrow — this checkpoint changes ONE field's validators.
It does not touch thresholds, VerificationAttempt, or the analytics formulas
(those are covered by tests_bpa3.py / tests_bpa3_1.py and are proven unchanged
here only insofar as a negative-score trial flows through them correctly).

All fixtures here are SYNTHETIC TEST DATA — never real study observations.
"""
from django.core.exceptions import ValidationError
from django.test import TestCase, Client

from accounts.models import CustomUser
from verification import biometric_analytics as ba
from verification.models import EvaluationDataset, EvaluationTrial, VerificationAttempt, ClaimRecord

from verification.tests_bpa2 import (
    _make_user, _make_beneficiary, _make_dataset as _make_dataset_bpa2,
    _make_pending_trial, _RunnerMocks, _post_run,
)
from verification.tests_bpa3 import _make_dataset, _make_trial

GENUINE = EvaluationTrial.GROUND_TRUTH_GENUINE
IMPOSTOR = EvaluationTrial.GROUND_TRUTH_IMPOSTOR


# ─────────────────────────────────────────────────────────────────────────
# §6 — Negative-score model validation (direct full_clean)
# ─────────────────────────────────────────────────────────────────────────

class SimilarityScoreDomainValidationTest(TestCase):
    """The stored validation domain must be the true cosine-similarity range
    [-1, 1], not the stale [0, 1] left over from BPA-3.1."""

    def setUp(self):
        self.dataset = EvaluationDataset.objects.create(
            name='BPA-3.2 validation dataset',
            protocol_version='protocol-v1',
        )

    def _trial(self, score, code='P-SCORE'):
        return EvaluationTrial(
            dataset=self.dataset,
            participant_code=code,
            target_identity_code='T-0001',
            identity_ground_truth=GENUINE,
            similarity_score=score,
        )

    def test_negative_one_valid(self):
        self._trial(-1.0).full_clean()

    def test_negative_point_two_valid(self):
        self._trial(-0.20).full_clean()

    def test_zero_valid(self):
        self._trial(0.0).full_clean()

    def test_positive_one_valid(self):
        self._trial(1.0).full_clean()

    def test_below_negative_one_rejected(self):
        with self.assertRaises(ValidationError):
            self._trial(-1.0001).full_clean()

    def test_above_one_rejected(self):
        with self.assertRaises(ValidationError):
            self._trial(1.0001).full_clean()

    def test_null_valid_when_comparison_not_reached(self):
        self._trial(None).full_clean()


# ─────────────────────────────────────────────────────────────────────────
# §7 — Controlled evaluation workflow persists a legitimate negative score
# ─────────────────────────────────────────────────────────────────────────

class NegativeScoreControlledWorkflowTest(TestCase):
    """A synthetic matcher result of -0.20 (e.g. two genuinely dissimilar
    embeddings) must survive the real trial-completion path — including its
    full_clean() call in views._complete_trial — without a ValidationError,
    and without ever creating an operational VerificationAttempt/ClaimRecord."""

    def setUp(self):
        self.admin = _make_user('bpa32_admin', CustomUser.ROLE_IT)
        self.client = Client()
        self.client.force_login(self.admin)
        self.dataset = _make_dataset_bpa2()
        self.beneficiary = _make_beneficiary()

    def test_negative_score_persists_without_validation_error(self):
        va_before = VerificationAttempt.objects.count()
        claim_before = ClaimRecord.objects.count()

        trial = _make_pending_trial(self.dataset, self.beneficiary, identity_ground_truth=GENUINE)
        with _RunnerMocks(score=-0.20):
            resp = _post_run(self.client, trial)
        self.assertIsNotNone(resp, 'liveness stage unexpectedly denied/aborted the trial')

        trial.refresh_from_db()
        self.assertEqual(trial.trial_status, EvaluationTrial.TRIAL_STATUS_COMPLETED)
        self.assertEqual(trial.similarity_score, -0.20)

        # -0.20 is far below review_band (threshold * 0.85) under the
        # positive-valued operating thresholds -> NOT_VERIFIED, not a crash.
        self.assertEqual(trial.system_decision, VerificationAttempt.DECISION_NOT_VERIFIED)

        self.assertEqual(VerificationAttempt.objects.count(), va_before)
        self.assertEqual(ClaimRecord.objects.count(), claim_before)


# ─────────────────────────────────────────────────────────────────────────
# §8 — Analytics end-to-end: schema -> storage -> analytics for a negative score
# ─────────────────────────────────────────────────────────────────────────

class NegativeScoreAnalyticsEndToEndTest(TestCase):
    """A trial saved through the real full_clean()+save() path (not just
    .objects.create(), which bypasses validators) with similarity_score=-0.20
    must be accepted, persisted, and surfaced unclamped by the score
    distribution and ROC engines."""

    def setUp(self):
        self.dataset = _make_dataset()

    def test_full_clean_save_persist_and_appear_in_distribution_and_roc(self):
        trial = EvaluationTrial(
            dataset=self.dataset,
            participant_code='P-NEG-E2E',
            target_identity_code='T-0001',
            identity_ground_truth=GENUINE,
            presentation_ground_truth=EvaluationTrial.PRESENTATION_BONA_FIDE,
            trial_status=EvaluationTrial.TRIAL_STATUS_COMPLETED,
            system_decision=VerificationAttempt.DECISION_NOT_VERIFIED,
            matcher_base_decision=VerificationAttempt.DECISION_NOT_VERIFIED,
            similarity_score=-0.20,
        )
        trial.full_clean()
        trial.save()

        # A positive-score counterpart so the distribution/ROC have both a
        # genuine and impostor observation to work with.
        _make_trial(
            self.dataset, identity_ground_truth=IMPOSTOR,
            system_decision=VerificationAttempt.DECISION_VERIFIED,
            similarity_score=0.95,
        )

        stored = EvaluationTrial.objects.get(pk=trial.pk)
        self.assertEqual(stored.similarity_score, -0.20)

        distributions = ba.get_score_distributions(self.dataset, bins=10)
        self.assertIn(-0.20, distributions['genuine']['values'])
        self.assertEqual(distributions['score_domain'], (-1.0, 1.0))

        histogram = distributions['genuine']['histogram']
        # width = 2.0/10 = 0.2; -0.20 -> idx 4, never the same bin as a
        # small positive score and never silently clamped to bin 0.
        self.assertEqual(histogram['counts'][4], 1)
        self.assertEqual(sum(histogram['counts']), 1)

        roc = ba.compute_roc(self.dataset)
        self.assertEqual(roc['warnings'], [])
        self.assertEqual(roc['n_genuine'], 1)
        self.assertEqual(roc['n_impostor'], 1)
        thresholds = [p['threshold'] for p in roc['points']]
        self.assertIn(-0.20, thresholds)
