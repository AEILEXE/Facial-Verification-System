"""
BPA-5 — Final Biometric Evaluation Readiness tests.

Covers the three schema/behavior additions made in this checkpoint:

1. Dataset purpose classification (`EvaluationDataset.purpose`) — required at
   creation, and `is_final_study_result` is true only for a COMPLETED dataset
   explicitly purposed as a real research study.
2. Participant withdrawal (`EvaluationTrial.withdrawn` + related fields) —
   President-only, excludes a trial from every biometric metric calculation
   without deleting the row, and never touches operational
   Beneficiary/Representative/VerificationAttempt/ClaimRecord data.
3. The read-only data-quality audit and technical study-readiness
   classification added to `verification/biometric_analytics.py`.

Also covers the liveness-proof-embedding data-minimization change to
`_complete_trial()` (BPA-5 §25).

All fixtures here are SYNTHETIC TEST DATA — never real study observations.
Reuses the BPA-3 dataset/trial factories (`_make_dataset`/`_make_trial`).
"""
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import CustomUser
from beneficiaries.models import Beneficiary, Representative
from logs.models import AuditLog
from verification import biometric_analytics as ba
from verification.models import EvaluationDataset, EvaluationTrial, VerificationAttempt, ClaimRecord
from verification.tests_bpa3 import _make_dataset, _make_trial, _make_beneficiary, _make_representative
from verification.views import _complete_trial

VERIFIED = VerificationAttempt.DECISION_VERIFIED
MANUAL_REVIEW = VerificationAttempt.DECISION_MANUAL_REVIEW
NOT_VERIFIED = VerificationAttempt.DECISION_NOT_VERIFIED
DENIED = VerificationAttempt.DECISION_DENIED

GENUINE = EvaluationTrial.GROUND_TRUTH_GENUINE
IMPOSTOR = EvaluationTrial.GROUND_TRUTH_IMPOSTOR
BONA_FIDE = EvaluationTrial.PRESENTATION_BONA_FIDE


def _make_user(username, role):
    return CustomUser.objects.create_user(
        username=username, password='TestPass123!',
        role=role, employee_id=f'EMP-{username.upper()}',
    )


def _quality_clean_trial(dataset, *, identity_ground_truth, system_decision):
    """A COMPLETED trial with every field the data-quality audit checks for
    (threshold snapshot, similarity score) already set — so a readiness test
    that isn't specifically about data quality doesn't accidentally trip it."""
    trial = _make_trial(dataset, identity_ground_truth=identity_ground_truth, system_decision=system_decision,
                         similarity_score=0.5)
    trial.review_threshold_snapshot = 0.6
    trial.auto_verify_threshold_snapshot = 0.85
    trial.full_clean()
    trial.save()
    return trial


# ─────────────────────────────────────────────────────────────────────────
# Dataset purpose classification (BPA-5 §4/§5/§20)
# ─────────────────────────────────────────────────────────────────────────

class DatasetPurposeTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = _make_user('bpa5admin', CustomUser.ROLE_IT)
        self.client.force_login(self.admin)

    def test_default_purpose_is_qa_synthetic(self):
        dataset = EvaluationDataset.objects.create(name='x', protocol_version='v1')
        self.assertEqual(dataset.purpose, EvaluationDataset.PURPOSE_QA_SYNTHETIC)

    def test_create_view_requires_valid_purpose(self):
        resp = self.client.post(reverse('verification:evaluation_dataset_create'), {
            'name': 'No Purpose Study', 'protocol_version': 'v1',
        })
        self.assertEqual(resp.status_code, 200)  # re-rendered form, not redirected
        self.assertFalse(EvaluationDataset.objects.filter(name='No Purpose Study').exists())

    def test_create_view_accepts_valid_purpose(self):
        resp = self.client.post(reverse('verification:evaluation_dataset_create'), {
            'name': 'Real Study', 'protocol_version': 'v1',
            'purpose': EvaluationDataset.PURPOSE_RESEARCH_STUDY,
        })
        dataset = EvaluationDataset.objects.get(name='Real Study')
        self.assertEqual(dataset.purpose, EvaluationDataset.PURPOSE_RESEARCH_STUDY)
        self.assertRedirects(resp, reverse('verification:evaluation_dataset_detail', args=[dataset.pk]))

    def test_is_final_study_result_requires_both_completed_and_research_purpose(self):
        completed_qa = _make_dataset(status=EvaluationDataset.STATUS_COMPLETED, purpose=EvaluationDataset.PURPOSE_QA_SYNTHETIC)
        completed_research = _make_dataset(status=EvaluationDataset.STATUS_COMPLETED, purpose=EvaluationDataset.PURPOSE_RESEARCH_STUDY)
        collecting_research = _make_dataset(status=EvaluationDataset.STATUS_COLLECTING, purpose=EvaluationDataset.PURPOSE_RESEARCH_STUDY)
        pilot_completed = _make_dataset(status=EvaluationDataset.STATUS_COMPLETED, purpose=EvaluationDataset.PURPOSE_PILOT_CALIBRATION)

        self.assertFalse(completed_qa.is_final_study_result)
        self.assertTrue(completed_research.is_final_study_result)
        self.assertFalse(collecting_research.is_final_study_result)
        self.assertFalse(pilot_completed.is_final_study_result)


# ─────────────────────────────────────────────────────────────────────────
# Participant withdrawal — access control (BPA-5 §21/§27)
# ─────────────────────────────────────────────────────────────────────────

class WithdrawalAccessControlTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.president = _make_user('bpa5president', CustomUser.ROLE_PRESIDENT)
        self.admin = _make_user('bpa5admin', CustomUser.ROLE_ADMIN)
        self.it = _make_user('bpa5it', CustomUser.ROLE_IT)
        self.staff = _make_user('bpa5staff', CustomUser.ROLE_STAFF)
        self.dataset = _make_dataset()
        self.trial = _make_trial(self.dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED)

    def _withdraw(self, user, reason='participant withdrew'):
        self.client.force_login(user)
        return self.client.post(
            reverse('verification:evaluation_trial_withdraw', args=[self.trial.pk]),
            {'withdrawal_reason': reason},
        )

    def test_president_cannot_withdraw(self):
        """Owner-approved policy (FINAL PRE-EXE COMPLETION checkpoint,
        section 3): President is read-only for Biometric Evaluation,
        withdrawal included — only the Technical Administrator may withdraw
        a trial."""
        self._withdraw(self.president)
        self.trial.refresh_from_db()
        self.assertFalse(self.trial.withdrawn)

    def test_admin_cannot_withdraw(self):
        self._withdraw(self.admin)
        self.trial.refresh_from_db()
        self.assertFalse(self.trial.withdrawn)

    def test_it_can_withdraw(self):
        self._withdraw(self.it)
        self.trial.refresh_from_db()
        self.assertTrue(self.trial.withdrawn)

    def test_staff_cannot_withdraw(self):
        self._withdraw(self.staff)
        self.trial.refresh_from_db()
        self.assertFalse(self.trial.withdrawn)


# ─────────────────────────────────────────────────────────────────────────
# Participant withdrawal — semantics (BPA-5 §21/§22/§28)
# ─────────────────────────────────────────────────────────────────────────

class WithdrawalSemanticsTest(TestCase):
    def setUp(self):
        self.client = Client()
        # Technical Administrator — the only role permitted to withdraw a
        # trial (President is read-only; see WithdrawalAccessControlTest).
        self.it = _make_user('bpa5it2', CustomUser.ROLE_IT)
        self.client.force_login(self.it)
        self.dataset = _make_dataset()
        self.trial = _make_trial(self.dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED)

    def test_withdrawal_requires_reason(self):
        resp = self.client.post(reverse('verification:evaluation_trial_withdraw', args=[self.trial.pk]), {})
        self.trial.refresh_from_db()
        self.assertFalse(self.trial.withdrawn)

    def test_withdrawal_sets_all_fields_and_audit_log(self):
        resp = self.client.post(
            reverse('verification:evaluation_trial_withdraw', args=[self.trial.pk]),
            {'withdrawal_reason': 'participant withdrew consent'},
        )
        self.trial.refresh_from_db()
        self.assertTrue(self.trial.withdrawn)
        self.assertEqual(self.trial.withdrawal_reason, 'participant withdrew consent')
        self.assertIsNotNone(self.trial.withdrawn_at)
        self.assertEqual(self.trial.withdrawn_by, self.it)
        self.assertTrue(AuditLog.objects.filter(
            action=AuditLog.ACTION_EVALUATION_TRIAL_WITHDRAWN, target_id=str(self.trial.id),
        ).exists())

    def test_double_withdrawal_is_idempotent_not_erroring(self):
        self.client.post(
            reverse('verification:evaluation_trial_withdraw', args=[self.trial.pk]),
            {'withdrawal_reason': 'first reason'},
        )
        resp = self.client.post(
            reverse('verification:evaluation_trial_withdraw', args=[self.trial.pk]),
            {'withdrawal_reason': 'second reason'},
        )
        self.trial.refresh_from_db()
        self.assertEqual(self.trial.withdrawal_reason, 'first reason')  # not overwritten

    def test_model_clean_requires_reason_when_withdrawn(self):
        trial = EvaluationTrial(
            dataset=self.dataset, participant_code='P-X', target_identity_code='T-X',
            identity_ground_truth=GENUINE, withdrawn=True,
        )
        with self.assertRaises(Exception):
            trial.full_clean()


# ─────────────────────────────────────────────────────────────────────────
# Withdrawal — metric exclusion (BPA-5 §22) and operational isolation (§21)
# ─────────────────────────────────────────────────────────────────────────

class WithdrawalMetricExclusionTest(TestCase):
    def test_withdrawn_trial_excluded_from_final_system_performance(self):
        dataset = _make_dataset()
        _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED)
        withdrawn_trial = _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=NOT_VERIFIED)
        withdrawn_trial.withdrawn = True
        withdrawn_trial.withdrawal_reason = 'withdrew'
        withdrawn_trial.withdrawn_at = timezone.now()
        withdrawn_trial.full_clean()
        withdrawn_trial.save()

        result = ba.get_final_system_performance(dataset)
        # Only the non-withdrawn VERIFIED genuine trial counts.
        self.assertEqual(result['matrix']['genuine']['total'], 1)
        self.assertEqual(result['matrix']['genuine']['verified'], 1)
        self.assertEqual(result['matrix']['genuine']['rejected'], 0)

    def test_dataset_info_reports_withdrawn_count_transparently(self):
        dataset = _make_dataset()
        _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED)
        withdrawn_trial = _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=NOT_VERIFIED)
        withdrawn_trial.withdrawn = True
        withdrawn_trial.withdrawal_reason = 'withdrew'
        withdrawn_trial.full_clean()
        withdrawn_trial.save()

        info = ba.get_dataset_info(dataset)
        self.assertEqual(info['total_trial_count'], 2)  # includes withdrawn — never hidden
        self.assertEqual(info['withdrawn_trial_count'], 1)

    def test_withdrawn_pending_trial_does_not_block_finalization_or_readiness(self):
        """A trial withdrawn BEFORE it ever ran stays trial_status=PENDING —
        it must not permanently block evaluation_dataset_finalize or
        get_study_readiness the way a genuinely unresolved pending trial
        does (BPA-5 edge case)."""
        dataset = _make_dataset(status=EvaluationDataset.STATUS_COLLECTING, purpose=EvaluationDataset.PURPOSE_RESEARCH_STUDY)
        _quality_clean_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED)
        _quality_clean_trial(dataset, identity_ground_truth=IMPOSTOR, system_decision=NOT_VERIFIED)
        pending = EvaluationTrial.objects.create(
            dataset=dataset, trial_status=EvaluationTrial.TRIAL_STATUS_PENDING,
            participant_code='P-withdrawn-pending', target_identity_code='T-1', identity_ground_truth=GENUINE,
        )
        pending.withdrawn = True
        pending.withdrawal_reason = 'withdrew before trial was run'
        pending.full_clean()
        pending.save()

        admin = _make_user('bpa5admin2', CustomUser.ROLE_IT)
        client = Client()
        client.force_login(admin)
        resp = client.post(reverse('verification:evaluation_dataset_finalize', args=[dataset.pk]))
        dataset.refresh_from_db()
        self.assertEqual(dataset.status, EvaluationDataset.STATUS_COMPLETED)

        readiness = ba.get_study_readiness(dataset)
        self.assertEqual(readiness['status'], 'READY_WITH_LIMITATIONS')  # withdrawn-trial limitation only
        self.assertFalse(any('PENDING' in r for r in readiness['reasons_not_ready']))

    def test_withdrawal_does_not_affect_operational_records(self):
        beneficiary = _make_beneficiary()
        dataset = _make_dataset()
        trial = _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED, target_beneficiary=beneficiary)

        va_count_before = VerificationAttempt.objects.count()
        claim_count_before = ClaimRecord.objects.count()

        it = _make_user('bpa5it3', CustomUser.ROLE_IT)
        client = Client()
        client.force_login(it)
        client.post(
            reverse('verification:evaluation_trial_withdraw', args=[trial.pk]),
            {'withdrawal_reason': 'participant withdrew'},
        )
        trial.refresh_from_db()
        self.assertTrue(trial.withdrawn)  # confirm the withdrawal actually happened

        self.assertEqual(VerificationAttempt.objects.count(), va_count_before)
        self.assertEqual(ClaimRecord.objects.count(), claim_count_before)
        beneficiary.refresh_from_db()  # still exists, untouched
        self.assertEqual(Beneficiary.objects.filter(pk=beneficiary.pk).count(), 1)


# ─────────────────────────────────────────────────────────────────────────
# Data-quality audit (BPA-5 §33)
# ─────────────────────────────────────────────────────────────────────────

class DataQualityAuditTest(TestCase):
    def test_clean_dataset_has_no_issues(self):
        dataset = _make_dataset()
        _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED, similarity_score=0.9,
                    verification_duration_ms=1000)
        # Give it a threshold snapshot so the completeness check is satisfied.
        trial = EvaluationTrial.objects.first()
        trial.review_threshold_snapshot = 0.6
        trial.auto_verify_threshold_snapshot = 0.85
        trial.full_clean()
        trial.save()

        report = ba.get_dataset_quality_report(dataset)
        self.assertEqual(report['issue_count'], 0)
        self.assertFalse(report['has_issues'])

    def test_detects_missing_participant_code(self):
        dataset = _make_dataset()
        trial = _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED)
        EvaluationTrial.objects.filter(pk=trial.pk).update(participant_code='')

        report = ba.get_dataset_quality_report(dataset)
        self.assertEqual(report['checks']['missing_participant_code'], 1)
        self.assertTrue(report['has_issues'])

    def test_detects_matcher_decision_with_null_similarity(self):
        dataset = _make_dataset()
        trial = _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED, similarity_score=0.9)
        EvaluationTrial.objects.filter(pk=trial.pk).update(similarity_score=None)

        report = ba.get_dataset_quality_report(dataset)
        self.assertEqual(report['checks']['matcher_decision_with_null_similarity'], 1)

    def test_detects_completed_without_threshold_snapshot(self):
        dataset = _make_dataset()
        _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED)
        # _make_trial never sets threshold snapshots — expect the check to fire.
        report = ba.get_dataset_quality_report(dataset)
        self.assertEqual(report['checks']['completed_without_threshold_snapshot'], 1)

    def test_detects_pending_trial_with_unresolved_target(self):
        dataset = _make_dataset(status=EvaluationDataset.STATUS_COLLECTING)
        EvaluationTrial.objects.create(
            dataset=dataset, trial_status=EvaluationTrial.TRIAL_STATUS_PENDING,
            participant_code='P-1', target_identity_code='T-1', identity_ground_truth=GENUINE,
        )
        report = ba.get_dataset_quality_report(dataset)
        self.assertEqual(report['checks']['pending_with_unresolved_target'], 1)

    def test_withdrawn_trial_excluded_from_quality_audit(self):
        dataset = _make_dataset()
        trial = _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED)
        EvaluationTrial.objects.filter(pk=trial.pk).update(participant_code='', withdrawn=True, withdrawal_reason='w')
        report = ba.get_dataset_quality_report(dataset)
        self.assertEqual(report['checks']['missing_participant_code'], 0)


# ─────────────────────────────────────────────────────────────────────────
# Study readiness classification (BPA-5 §34/§42)
# ─────────────────────────────────────────────────────────────────────────

class StudyReadinessTest(TestCase):
    def test_ready_when_completed_research_study_both_classes_no_pending_no_issues(self):
        dataset = _make_dataset(status=EvaluationDataset.STATUS_COMPLETED, purpose=EvaluationDataset.PURPOSE_RESEARCH_STUDY)
        _quality_clean_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED)
        _quality_clean_trial(dataset, identity_ground_truth=IMPOSTOR, system_decision=NOT_VERIFIED)

        readiness = ba.get_study_readiness(dataset)
        self.assertEqual(readiness['status'], 'READY')
        self.assertEqual(readiness['reasons_not_ready'], [])
        self.assertEqual(readiness['reasons_limitation'], [])

    def test_not_ready_when_dataset_not_completed(self):
        dataset = _make_dataset(status=EvaluationDataset.STATUS_COLLECTING, purpose=EvaluationDataset.PURPOSE_RESEARCH_STUDY)
        readiness = ba.get_study_readiness(dataset)
        self.assertEqual(readiness['status'], 'NOT_READY')

    def test_not_ready_when_zero_trials(self):
        dataset = _make_dataset(status=EvaluationDataset.STATUS_COMPLETED, purpose=EvaluationDataset.PURPOSE_RESEARCH_STUDY)
        readiness = ba.get_study_readiness(dataset)
        self.assertEqual(readiness['status'], 'NOT_READY')

    def test_not_ready_when_pending_trial_exists(self):
        dataset = _make_dataset(status=EvaluationDataset.STATUS_COMPLETED, purpose=EvaluationDataset.PURPOSE_RESEARCH_STUDY)
        _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED)
        EvaluationTrial.objects.create(
            dataset=dataset, trial_status=EvaluationTrial.TRIAL_STATUS_PENDING,
            participant_code='P-pending', target_identity_code='T-pending', identity_ground_truth=IMPOSTOR,
        )
        readiness = ba.get_study_readiness(dataset)
        self.assertEqual(readiness['status'], 'NOT_READY')

    def test_ready_with_limitations_when_purpose_not_research_study(self):
        dataset = _make_dataset(status=EvaluationDataset.STATUS_COMPLETED, purpose=EvaluationDataset.PURPOSE_QA_SYNTHETIC)
        _quality_clean_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED)
        _quality_clean_trial(dataset, identity_ground_truth=IMPOSTOR, system_decision=NOT_VERIFIED)
        readiness = ba.get_study_readiness(dataset)
        self.assertEqual(readiness['status'], 'READY_WITH_LIMITATIONS')

    def test_ready_with_limitations_when_missing_impostor_class(self):
        dataset = _make_dataset(status=EvaluationDataset.STATUS_COMPLETED, purpose=EvaluationDataset.PURPOSE_RESEARCH_STUDY)
        _quality_clean_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED)
        readiness = ba.get_study_readiness(dataset)
        self.assertEqual(readiness['status'], 'READY_WITH_LIMITATIONS')
        self.assertTrue(any('IMPOSTOR' in r for r in readiness['reasons_limitation']))

    def test_aborted_trials_never_treated_as_pending_or_biometric_failures(self):
        """An ABORTED trial (technical failure) must not block readiness the
        way a PENDING trial does, and never enters the identity-performance
        matrix at all (BPA-3 §3 unchanged by this checkpoint)."""
        dataset = _make_dataset(status=EvaluationDataset.STATUS_COMPLETED, purpose=EvaluationDataset.PURPOSE_RESEARCH_STUDY)
        _quality_clean_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED)
        _quality_clean_trial(dataset, identity_ground_truth=IMPOSTOR, system_decision=NOT_VERIFIED)
        EvaluationTrial.objects.create(
            dataset=dataset, trial_status=EvaluationTrial.TRIAL_STATUS_ABORTED,
            participant_code='P-aborted', target_identity_code='T-aborted', identity_ground_truth=GENUINE,
        )
        readiness = ba.get_study_readiness(dataset)
        self.assertEqual(readiness['status'], 'READY')
        matrix = ba.get_final_system_performance(dataset)['matrix']
        self.assertEqual(matrix['genuine']['total'], 1)  # aborted trial not counted


# ─────────────────────────────────────────────────────────────────────────
# Liveness-proof-embedding data minimization (BPA-5 §25)
# ─────────────────────────────────────────────────────────────────────────

class LivenessProofMinimizationTest(TestCase):
    def test_liveness_proof_embedding_cleared_on_completion(self):
        dataset = _make_dataset(status=EvaluationDataset.STATUS_COLLECTING)
        trial = EvaluationTrial.objects.create(
            dataset=dataset, trial_status=EvaluationTrial.TRIAL_STATUS_PENDING,
            participant_code='P-1', target_identity_code='T-1', identity_ground_truth=GENUINE,
            liveness_proof_embedding=b'\x01\x02\x03\x04',
        )
        user = _make_user('bpa5president4', CustomUser.ROLE_PRESIDENT)
        _complete_trial(
            trial, user,
            decision=VERIFIED, score=0.9, threshold=0.6, auto_verify_threshold=0.85,
        )
        trial.refresh_from_db()
        self.assertIsNone(trial.liveness_proof_embedding)
        self.assertEqual(trial.trial_status, EvaluationTrial.TRIAL_STATUS_COMPLETED)

    def test_liveness_proof_embedding_clear_is_safe_noop_when_never_set(self):
        dataset = _make_dataset(status=EvaluationDataset.STATUS_COLLECTING)
        trial = EvaluationTrial.objects.create(
            dataset=dataset, trial_status=EvaluationTrial.TRIAL_STATUS_PENDING,
            participant_code='P-2', target_identity_code='T-2', identity_ground_truth=GENUINE,
        )
        user = _make_user('bpa5president5', CustomUser.ROLE_PRESIDENT)
        _complete_trial(
            trial, user,
            decision=DENIED, score=None, threshold=0.6, auto_verify_threshold=0.85,
        )
        trial.refresh_from_db()
        self.assertIsNone(trial.liveness_proof_embedding)
