"""
BPA-1 — Controlled Biometric Evaluation Data Foundation tests.

These tests cover only the DATA MODEL introduced in BPA-1 (EvaluationDataset,
EvaluationTrial): creation, ground-truth/system-decision independence, the
three-outcome (VERIFIED / MANUAL_REVIEW / NOT_VERIFIED) architecture,
validation, and deletion/retention semantics. No accuracy/FAR/FRR
computation exists yet — that is out of scope until a future BPA checkpoint.

Kept as a dedicated module (not appended to the large verification/tests.py)
per BPA-1 checkpoint instructions.
"""

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from accounts.models import CustomUser
from beneficiaries.models import Beneficiary
from verification.models import (
    EvaluationDataset,
    EvaluationTrial,
    VerificationAttempt,
    SystemConfig,
)


def _make_researcher(username='bpa_researcher'):
    return CustomUser.objects.create_user(
        username=username, password='TestPass123!',
        role=CustomUser.ROLE_ADMIN, employee_id=f'EMP-{username.upper()}',
    )


class EvaluationDatasetModelTest(TestCase):
    """A. Evaluation session/dataset can be created."""

    def test_dataset_can_be_created(self):
        researcher = _make_researcher()
        dataset = EvaluationDataset.objects.create(
            name='Pilot Controlled Evaluation',
            protocol_version='protocol-v1',
            status=EvaluationDataset.STATUS_DRAFT,
            created_by=researcher,
        )
        self.assertIsNotNone(dataset.id)
        self.assertEqual(dataset.status, EvaluationDataset.STATUS_DRAFT)
        self.assertFalse(dataset.is_finalized)

    def test_completed_dataset_requires_completed_at(self):
        dataset = EvaluationDataset(
            name='Missing completed_at',
            protocol_version='protocol-v1',
            status=EvaluationDataset.STATUS_COMPLETED,
        )
        with self.assertRaises(ValidationError):
            dataset.full_clean()

    def test_completed_dataset_with_completed_at_is_finalized(self):
        dataset = EvaluationDataset.objects.create(
            name='Finished study',
            protocol_version='protocol-v1',
            status=EvaluationDataset.STATUS_COMPLETED,
            completed_at=timezone.now(),
        )
        dataset.full_clean()
        self.assertTrue(dataset.is_finalized)


class EvaluationTrialCreationTest(TestCase):
    """B/C. Genuine and impostor evaluation trials can be created."""

    def setUp(self):
        self.dataset = EvaluationDataset.objects.create(
            name='Trial creation dataset',
            protocol_version='protocol-v1',
            status=EvaluationDataset.STATUS_COLLECTING,
        )

    def test_genuine_trial_can_be_created(self):
        trial = EvaluationTrial.objects.create(
            dataset=self.dataset,
            participant_code='P-0001',
            target_identity_code='T-0001',
            identity_ground_truth=EvaluationTrial.GROUND_TRUTH_GENUINE,
        )
        trial.full_clean()
        self.assertEqual(trial.identity_ground_truth, EvaluationTrial.GROUND_TRUTH_GENUINE)
        self.assertEqual(trial.trial_status, EvaluationTrial.TRIAL_STATUS_PENDING)

    def test_impostor_trial_can_be_created(self):
        trial = EvaluationTrial.objects.create(
            dataset=self.dataset,
            participant_code='P-0002',
            target_identity_code='T-0001',
            identity_ground_truth=EvaluationTrial.GROUND_TRUTH_IMPOSTOR,
        )
        trial.full_clean()
        self.assertEqual(trial.identity_ground_truth, EvaluationTrial.GROUND_TRUTH_IMPOSTOR)


class GroundTruthIndependenceTest(TestCase):
    """D. Identity ground truth must be representable independently of system
    decision — this is what makes false-reject and false-accept representable."""

    def setUp(self):
        self.dataset = EvaluationDataset.objects.create(
            name='Ground truth independence dataset',
            protocol_version='protocol-v1',
        )

    def test_genuine_presenter_rejected_is_representable(self):
        """GENUINE + NOT_VERIFIED = false reject."""
        trial = EvaluationTrial.objects.create(
            dataset=self.dataset,
            participant_code='P-GEN-REJECT',
            target_identity_code='T-0001',
            identity_ground_truth=EvaluationTrial.GROUND_TRUTH_GENUINE,
            trial_status=EvaluationTrial.TRIAL_STATUS_COMPLETED,
            system_decision=VerificationAttempt.DECISION_NOT_VERIFIED,
            similarity_score=0.40,
        )
        trial.full_clean()
        self.assertEqual(trial.identity_ground_truth, EvaluationTrial.GROUND_TRUTH_GENUINE)
        self.assertEqual(trial.system_decision, VerificationAttempt.DECISION_NOT_VERIFIED)

    def test_impostor_presenter_verified_is_representable(self):
        """IMPOSTOR + VERIFIED = false accept."""
        trial = EvaluationTrial.objects.create(
            dataset=self.dataset,
            participant_code='P-IMP-ACCEPT',
            target_identity_code='T-0001',
            identity_ground_truth=EvaluationTrial.GROUND_TRUTH_IMPOSTOR,
            trial_status=EvaluationTrial.TRIAL_STATUS_COMPLETED,
            system_decision=VerificationAttempt.DECISION_VERIFIED,
            similarity_score=0.91,
        )
        trial.full_clean()
        self.assertEqual(trial.identity_ground_truth, EvaluationTrial.GROUND_TRUTH_IMPOSTOR)
        self.assertEqual(trial.system_decision, VerificationAttempt.DECISION_VERIFIED)


class ThreeOutcomeArchitectureTest(TestCase):
    """E. Manual Review must remain representable independently for both
    genuine and impostor ground truth — never silently folded into failure."""

    def setUp(self):
        self.dataset = EvaluationDataset.objects.create(
            name='Three-outcome dataset',
            protocol_version='protocol-v1',
        )

    def test_genuine_manual_review_representable(self):
        trial = EvaluationTrial.objects.create(
            dataset=self.dataset,
            participant_code='P-GEN-MR',
            target_identity_code='T-0001',
            identity_ground_truth=EvaluationTrial.GROUND_TRUTH_GENUINE,
            trial_status=EvaluationTrial.TRIAL_STATUS_COMPLETED,
            system_decision=VerificationAttempt.DECISION_MANUAL_REVIEW,
            similarity_score=0.79,
        )
        trial.full_clean()
        self.assertEqual(trial.system_decision, VerificationAttempt.DECISION_MANUAL_REVIEW)

    def test_impostor_manual_review_representable(self):
        trial = EvaluationTrial.objects.create(
            dataset=self.dataset,
            participant_code='P-IMP-MR',
            target_identity_code='T-0001',
            identity_ground_truth=EvaluationTrial.GROUND_TRUTH_IMPOSTOR,
            trial_status=EvaluationTrial.TRIAL_STATUS_COMPLETED,
            system_decision=VerificationAttempt.DECISION_MANUAL_REVIEW,
            similarity_score=0.81,
        )
        trial.full_clean()
        self.assertEqual(trial.system_decision, VerificationAttempt.DECISION_MANUAL_REVIEW)

    def test_all_six_ground_truth_by_decision_combinations_are_representable(self):
        decisions = [
            VerificationAttempt.DECISION_VERIFIED,
            VerificationAttempt.DECISION_MANUAL_REVIEW,
            VerificationAttempt.DECISION_NOT_VERIFIED,
        ]
        truths = [EvaluationTrial.GROUND_TRUTH_GENUINE, EvaluationTrial.GROUND_TRUTH_IMPOSTOR]
        created = 0
        for truth in truths:
            for decision in decisions:
                trial = EvaluationTrial.objects.create(
                    dataset=self.dataset,
                    participant_code=f'P-{truth}-{decision}',
                    target_identity_code='T-0001',
                    identity_ground_truth=truth,
                    trial_status=EvaluationTrial.TRIAL_STATUS_COMPLETED,
                    system_decision=decision,
                    similarity_score=0.5,
                )
                trial.full_clean()
                created += 1
        self.assertEqual(created, 6)


class NullableMeasurementFieldsTest(TestCase):
    """F/G. similarity_score and verification_duration_ms may be NULL when
    no comparison/measurement occurred — NULL and 0.0 are different meanings."""

    def setUp(self):
        self.dataset = EvaluationDataset.objects.create(
            name='Nullable fields dataset',
            protocol_version='protocol-v1',
        )

    def test_similarity_score_null_when_liveness_gate_blocks_comparison(self):
        trial = EvaluationTrial.objects.create(
            dataset=self.dataset,
            participant_code='P-BLOCKED',
            target_identity_code='T-0001',
            identity_ground_truth=EvaluationTrial.GROUND_TRUTH_IMPOSTOR,
            presentation_ground_truth=EvaluationTrial.PRESENTATION_PRINT_PHOTO,
            trial_status=EvaluationTrial.TRIAL_STATUS_COMPLETED,
            system_decision=VerificationAttempt.DECISION_DENIED,
            similarity_score=None,
            liveness_passed=False,
        )
        trial.full_clean()
        self.assertIsNone(trial.similarity_score)

    def test_duration_null_before_bpa2_instrumentation(self):
        trial = EvaluationTrial.objects.create(
            dataset=self.dataset,
            participant_code='P-NO-TIMING',
            target_identity_code='T-0001',
            identity_ground_truth=EvaluationTrial.GROUND_TRUTH_GENUINE,
        )
        trial.full_clean()
        self.assertIsNone(trial.verification_duration_ms)


class DurationValidationTest(TestCase):
    """H. Negative duration must be rejected."""

    def setUp(self):
        self.dataset = EvaluationDataset.objects.create(
            name='Duration validation dataset',
            protocol_version='protocol-v1',
        )

    def test_negative_duration_rejected_by_full_clean(self):
        trial = EvaluationTrial(
            dataset=self.dataset,
            participant_code='P-NEG-DUR',
            target_identity_code='T-0001',
            identity_ground_truth=EvaluationTrial.GROUND_TRUTH_GENUINE,
            verification_duration_ms=-1,
        )
        with self.assertRaises(ValidationError):
            trial.full_clean()

    def test_negative_duration_rejected_by_db_constraint(self):
        trial = EvaluationTrial(
            dataset=self.dataset,
            participant_code='P-NEG-DUR-DB',
            target_identity_code='T-0001',
            identity_ground_truth=EvaluationTrial.GROUND_TRUTH_GENUINE,
            verification_duration_ms=-5,
        )
        with self.assertRaises(Exception):
            trial.save()

    def test_zero_duration_is_valid(self):
        # BPA-2 (verification/models.py EvaluationTrial.clean()) tightened this:
        # verification_duration_ms is only meaningful once trial_status is
        # COMPLETED (a PENDING/ABORTED trial must never carry a fabricated
        # duration), so a zero-duration trial must also be COMPLETED with a
        # system_decision to pass validation.
        trial = EvaluationTrial.objects.create(
            dataset=self.dataset,
            participant_code='P-ZERO-DUR',
            target_identity_code='T-0001',
            identity_ground_truth=EvaluationTrial.GROUND_TRUTH_GENUINE,
            trial_status=EvaluationTrial.TRIAL_STATUS_COMPLETED,
            system_decision=VerificationAttempt.DECISION_VERIFIED,
            verification_duration_ms=0,
        )
        trial.full_clean()
        self.assertEqual(trial.verification_duration_ms, 0)


class TrialValidationTest(TestCase):
    """I. Invalid choice/value validation behaves correctly."""

    def setUp(self):
        self.dataset = EvaluationDataset.objects.create(
            name='Validation dataset',
            protocol_version='protocol-v1',
        )

    def test_blank_participant_code_rejected(self):
        trial = EvaluationTrial(
            dataset=self.dataset,
            participant_code='   ',
            target_identity_code='T-0001',
            identity_ground_truth=EvaluationTrial.GROUND_TRUTH_GENUINE,
        )
        with self.assertRaises(ValidationError):
            trial.full_clean()

    def test_blank_target_identity_code_rejected(self):
        trial = EvaluationTrial(
            dataset=self.dataset,
            participant_code='P-0001',
            target_identity_code='',
            identity_ground_truth=EvaluationTrial.GROUND_TRUTH_GENUINE,
        )
        with self.assertRaises(ValidationError):
            trial.full_clean()

    def test_invalid_identity_ground_truth_choice_rejected(self):
        trial = EvaluationTrial(
            dataset=self.dataset,
            participant_code='P-0001',
            target_identity_code='T-0001',
            identity_ground_truth='not_a_real_choice',
        )
        with self.assertRaises(ValidationError):
            trial.full_clean()

    def test_similarity_score_out_of_range_rejected(self):
        trial = EvaluationTrial(
            dataset=self.dataset,
            participant_code='P-0001',
            target_identity_code='T-0001',
            identity_ground_truth=EvaluationTrial.GROUND_TRUTH_GENUINE,
            similarity_score=1.5,
        )
        with self.assertRaises(ValidationError):
            trial.full_clean()

    def test_completed_trial_without_system_decision_rejected(self):
        trial = EvaluationTrial(
            dataset=self.dataset,
            participant_code='P-0001',
            target_identity_code='T-0001',
            identity_ground_truth=EvaluationTrial.GROUND_TRUTH_GENUINE,
            trial_status=EvaluationTrial.TRIAL_STATUS_COMPLETED,
        )
        with self.assertRaises(ValidationError):
            trial.full_clean()

    def test_pending_trial_without_system_decision_is_valid(self):
        trial = EvaluationTrial(
            dataset=self.dataset,
            participant_code='P-0001',
            target_identity_code='T-0001',
            identity_ground_truth=EvaluationTrial.GROUND_TRUTH_GENUINE,
            trial_status=EvaluationTrial.TRIAL_STATUS_PENDING,
        )
        trial.full_clean()  # must not raise


class ThresholdSnapshotTest(TestCase):
    """J. Threshold snapshots remain stored even if SystemConfig changes later —
    historical reproducibility."""

    def setUp(self):
        self.dataset = EvaluationDataset.objects.create(
            name='Threshold snapshot dataset',
            protocol_version='protocol-v1',
        )

    def test_snapshot_survives_systemconfig_change(self):
        trial = EvaluationTrial.objects.create(
            dataset=self.dataset,
            participant_code='P-THRESH',
            target_identity_code='T-0001',
            identity_ground_truth=EvaluationTrial.GROUND_TRUTH_GENUINE,
            review_threshold_snapshot=0.75,
            auto_verify_threshold_snapshot=0.88,
        )
        trial.full_clean()

        # SystemConfig changes AFTER the trial was recorded — must not affect
        # the already-stored snapshot on this trial.
        SystemConfig.set_value('verification_threshold', '0.65')
        SystemConfig.set_value('auto_verify_threshold', '0.95')

        trial.refresh_from_db()
        self.assertEqual(trial.review_threshold_snapshot, 0.75)
        self.assertEqual(trial.auto_verify_threshold_snapshot, 0.88)


class DeletionRetentionSemanticsTest(TestCase):
    """K. Deletion behavior for linked VerificationAttempt/user/dataset is as intended:
    research data must not disappear because an unrelated operational record is removed."""

    def setUp(self):
        self.researcher = _make_researcher('bpa_del_researcher')
        self.dataset = EvaluationDataset.objects.create(
            name='Deletion semantics dataset',
            protocol_version='protocol-v1',
            created_by=self.researcher,
        )
        self.beneficiary = Beneficiary.objects.create(
            beneficiary_id='BEN-BPA-0001',
            first_name='Test',
            last_name='Beneficiary',
            senior_citizen_id='SC-BPA-0001',
            date_of_birth='1950-01-01',
            gender='F',
            address='123 Test St',
            barangay='Test Barangay',
            municipality='Quezon City',
            province='Metro Manila',
        )
        self.attempt = VerificationAttempt.objects.create(
            beneficiary=self.beneficiary,
            performed_by=self.researcher,
            decision=VerificationAttempt.DECISION_VERIFIED,
            similarity_score=0.9,
        )
        self.trial = EvaluationTrial.objects.create(
            dataset=self.dataset,
            participant_code='P-DEL',
            target_identity_code='T-DEL',
            identity_ground_truth=EvaluationTrial.GROUND_TRUTH_GENUINE,
            trial_status=EvaluationTrial.TRIAL_STATUS_COMPLETED,
            system_decision=VerificationAttempt.DECISION_VERIFIED,
            similarity_score=0.9,
            verification_attempt=self.attempt,
            created_by=self.researcher,
        )

    def test_deleting_verification_attempt_preserves_trial(self):
        self.attempt.delete()
        self.trial.refresh_from_db()
        self.assertIsNone(self.trial.verification_attempt)
        # Snapshot fields recorded independently of the attempt are untouched.
        self.assertEqual(self.trial.system_decision, VerificationAttempt.DECISION_VERIFIED)
        self.assertEqual(self.trial.similarity_score, 0.9)

    def test_deleting_created_by_user_preserves_trial_and_dataset(self):
        self.researcher.delete()
        self.trial.refresh_from_db()
        self.dataset.refresh_from_db()
        self.assertIsNone(self.trial.created_by)
        self.assertIsNone(self.dataset.created_by)
        self.assertEqual(self.trial.participant_code, 'P-DEL')

    def test_deleting_dataset_cascades_to_its_trials(self):
        trial_id = self.trial.id
        self.dataset.delete()
        self.assertFalse(EvaluationTrial.objects.filter(id=trial_id).exists())
