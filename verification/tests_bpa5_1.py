"""
BPA-5.1 — Final Readiness Closure tests.

Covers the two closure gaps identified in the BPA-5.1 checkpoint instructions:

1. Finalized-dataset withdrawal (dataset.amended_after_finalization_at):
   withdrawal after a dataset is COMPLETED/ARCHIVED remains ALLOWED (never
   blocked — participant withdrawal is a research-integrity requirement),
   but the dataset is marked amended so no viewer ever sees a stale
   "FINALIZED" label without disclosure. completed_at (the ORIGINAL
   finalization date) is never rewritten.

2. Liveness-proof-embedding retention across EVERY controlled-trial exit
   path, not just the `_complete_trial()` path BPA-5 verified:
   `_abort_trial()` now also clears `liveness_proof_embedding` (covers every
   abort call site, including the ones that fire AFTER stage-1 already
   stored a proof), withdrawal clears it too, and a stale-PENDING-with-proof
   dashboard warning surfaces trials abandoned after stage-1 capture (no
   background cleanup job — out of scope by design, see
   docs/BIOMETRIC-EVALUATION-METHODOLOGY.md).

All fixtures are SYNTHETIC TEST DATA. Reuses the BPA-3 dataset/trial
factories (`_make_dataset`/`_make_trial`).
"""
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from accounts.models import CustomUser
from logs.models import AuditLog
from verification import biometric_analytics as ba
from verification.models import EvaluationDataset, EvaluationTrial, VerificationAttempt
from verification.tests_bpa3 import _make_dataset, _make_trial
from verification.views import _complete_trial, _abort_trial

VERIFIED = VerificationAttempt.DECISION_VERIFIED
NOT_VERIFIED = VerificationAttempt.DECISION_NOT_VERIFIED
DENIED = VerificationAttempt.DECISION_DENIED

GENUINE = EvaluationTrial.GROUND_TRUTH_GENUINE
IMPOSTOR = EvaluationTrial.GROUND_TRUTH_IMPOSTOR


def _make_user(username, role):
    return CustomUser.objects.create_user(
        username=username, password='TestPass123!',
        role=role, employee_id=f'EMP-{username.upper()}',
    )


# ─────────────────────────────────────────────────────────────────────────
# A/B. Finalized-dataset withdrawal — allowed, marks amendment (BPA-5.1 §2/§3)
# ─────────────────────────────────────────────────────────────────────────

class FinalizedDatasetWithdrawalTest(TestCase):
    def setUp(self):
        # Technical Administrator — the only role permitted to withdraw a
        # trial under the owner-approved authorization model (FINAL PRE-EXE
        # COMPLETION checkpoint, section 3). President's read-only status is
        # covered separately by test_president_cannot_withdraw_after_finalization.
        self.it = _make_user('bpa51it', CustomUser.ROLE_IT)
        self.client = Client()
        self.client.force_login(self.it)

    def _withdraw(self, trial, reason='participant withdrew post-finalization'):
        return self.client.post(
            reverse('verification:evaluation_trial_withdraw', args=[trial.pk]),
            {'withdrawal_reason': reason},
        )

    def test_collecting_dataset_withdrawal_does_not_mark_amended(self):
        """A. Withdrawal on a still-COLLECTING dataset — no finalized result
        exists yet, so there is nothing to amend."""
        dataset = _make_dataset(status=EvaluationDataset.STATUS_COLLECTING)
        trial = _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED)
        self._withdraw(trial)
        dataset.refresh_from_db()
        self.assertIsNone(dataset.amended_after_finalization_at)
        self.assertFalse(dataset.has_post_finalization_amendment)
        self.assertEqual(dataset.status, EvaluationDataset.STATUS_COLLECTING)

    def test_completed_dataset_withdrawal_allowed_and_marks_amended(self):
        """B. Withdrawal on a COMPLETED dataset is ALLOWED (never blocked)
        and marks the amendment — status/completed_at stay untouched."""
        dataset = _make_dataset(status=EvaluationDataset.STATUS_COMPLETED)
        original_completed_at = dataset.completed_at
        trial = _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED)

        resp = self._withdraw(trial)

        trial.refresh_from_db()
        dataset.refresh_from_db()
        self.assertTrue(trial.withdrawn)  # withdrawal itself was NOT blocked
        self.assertEqual(dataset.status, EvaluationDataset.STATUS_COMPLETED)  # F: status unchanged
        self.assertEqual(dataset.completed_at, original_completed_at)  # original date preserved
        self.assertIsNotNone(dataset.amended_after_finalization_at)  # amendment disclosed
        self.assertTrue(dataset.has_post_finalization_amendment)
        self.assertGreaterEqual(dataset.amended_after_finalization_at, original_completed_at)

    def test_archived_dataset_withdrawal_also_marks_amended(self):
        """ARCHIVED is only reachable via COMPLETED — completed_at is still
        set, so a withdrawal there must also be disclosed as an amendment."""
        dataset = _make_dataset(status=EvaluationDataset.STATUS_ARCHIVED, completed_at=timezone.now())
        trial = _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED)
        self._withdraw(trial)
        dataset.refresh_from_db()
        self.assertEqual(dataset.status, EvaluationDataset.STATUS_ARCHIVED)
        self.assertTrue(dataset.has_post_finalization_amendment)

    def test_is_final_study_result_unchanged_by_amendment(self):
        """F. is_finalized / is_final_study_result are gates on status+purpose
        only — an amendment never silently reclassifies a genuine finalized
        research-study result, it only requires disclosure in the UI."""
        dataset = _make_dataset(status=EvaluationDataset.STATUS_COMPLETED, purpose=EvaluationDataset.PURPOSE_RESEARCH_STUDY)
        trial = _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED)
        self._withdraw(trial)
        dataset.refresh_from_db()
        self.assertTrue(dataset.is_finalized)
        self.assertTrue(dataset.is_final_study_result)
        self.assertTrue(dataset.has_post_finalization_amendment)

    def test_metrics_recompute_excluding_withdrawn_trial_after_finalization(self):
        """C. Analytics immediately excludes the withdrawn trial once
        recomputed, even though the dataset stays COMPLETED throughout."""
        dataset = _make_dataset(status=EvaluationDataset.STATUS_COMPLETED)
        _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED)
        to_withdraw = _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=NOT_VERIFIED)

        before = ba.get_final_system_performance(dataset)
        self.assertEqual(before['matrix']['genuine']['total'], 2)

        self._withdraw(to_withdraw)

        after = ba.get_final_system_performance(dataset)
        self.assertEqual(after['matrix']['genuine']['total'], 1)  # withdrawn trial excluded
        self.assertEqual(after['matrix']['genuine']['verified'], 1)

    def test_audit_trail_reconstructs_post_finalization_amendment(self):
        """E. Both the original finalization date and the fact/timing of the
        post-finalization withdrawal are reconstructable from AuditLog
        alone, independent of the live dataset row."""
        dataset = _make_dataset(status=EvaluationDataset.STATUS_COMPLETED)
        original_completed_at = dataset.completed_at
        trial = _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED)

        self._withdraw(trial)

        withdrawal_log = AuditLog.objects.get(
            action=AuditLog.ACTION_EVALUATION_TRIAL_WITHDRAWN, target_id=str(trial.id),
        )
        self.assertTrue(withdrawal_log.details['dataset_was_finalized_at_withdrawal'])
        self.assertEqual(withdrawal_log.details['dataset_status_at_withdrawal'], EvaluationDataset.STATUS_COMPLETED)
        self.assertEqual(withdrawal_log.details['dataset_completed_at'], original_completed_at.isoformat())

        amendment_log = AuditLog.objects.get(
            action=AuditLog.ACTION_EVALUATION_DATASET_AMENDED, target_id=str(dataset.id),
        )
        self.assertEqual(amendment_log.details['original_completed_at'], original_completed_at.isoformat())
        self.assertEqual(amendment_log.details['withdrawn_trial_id'], str(trial.id))

    def test_unauthorized_roles_cannot_withdraw_after_finalization(self):
        """G. Admin/President/Staff still cannot withdraw a trial even once
        the dataset is finalized — the Technical-Administrator-only gate is
        unaffected by dataset status."""
        dataset = _make_dataset(status=EvaluationDataset.STATUS_COMPLETED)
        for role, name in [
            (CustomUser.ROLE_ADMIN, 'bpa51admin'),
            (CustomUser.ROLE_PRESIDENT, 'bpa51president_denied'),
            (CustomUser.ROLE_STAFF, 'bpa51staff'),
        ]:
            trial = _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED)
            user = _make_user(name, role)
            client = Client()
            client.force_login(user)
            client.post(
                reverse('verification:evaluation_trial_withdraw', args=[trial.pk]),
                {'withdrawal_reason': 'attempted'},
            )
            trial.refresh_from_db()
            dataset.refresh_from_db()
            self.assertFalse(trial.withdrawn)
            self.assertIsNone(dataset.amended_after_finalization_at)

    def test_president_cannot_withdraw_after_finalization(self):
        """H. President is read-only for Biometric Evaluation and may not
        withdraw a trial, even on a finalized dataset."""
        dataset = _make_dataset(status=EvaluationDataset.STATUS_COMPLETED)
        trial = _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED)
        president = _make_user('bpa51president_h', CustomUser.ROLE_PRESIDENT)
        client = Client()
        client.force_login(president)
        client.post(
            reverse('verification:evaluation_trial_withdraw', args=[trial.pk]),
            {'withdrawal_reason': 'attempted by president'},
        )
        trial.refresh_from_db()
        self.assertFalse(trial.withdrawn)

    def test_analytics_and_dataset_pages_visibly_disclose_amendment(self):
        """I. Both the Biometric Performance Analytics page and the dataset
        detail page render the amended-disclosure wording once amended —
        never a plain, unqualified FINALIZED label."""
        dataset = _make_dataset(status=EvaluationDataset.STATUS_COMPLETED, purpose=EvaluationDataset.PURPOSE_RESEARCH_STUDY)
        trial = _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED)
        self._withdraw(trial)

        detail_resp = self.client.get(reverse('verification:evaluation_dataset_detail', args=[dataset.pk]))
        self.assertContains(detail_resp, 'AMENDED AFTER PARTICIPANT WITHDRAWAL')

        analytics_resp = self.client.get(reverse('verification:analytics_biometric_performance') + f'?dataset={dataset.pk}')
        self.assertContains(analytics_resp, 'AMENDED AFTER PARTICIPANT WITHDRAWAL')

    def test_withdrawn_pending_trial_cannot_be_run(self):
        """G (UI-facing): once withdrawn, a still-PENDING trial can no
        longer be run — no misleading Run Trial action succeeds."""
        dataset = _make_dataset(status=EvaluationDataset.STATUS_COLLECTING)
        trial = EvaluationTrial.objects.create(
            dataset=dataset, trial_status=EvaluationTrial.TRIAL_STATUS_PENDING,
            participant_code='P-wd-pending', target_identity_code='T-1', identity_ground_truth=GENUINE,
        )
        self._withdraw(trial)
        trial.refresh_from_db()
        self.assertTrue(trial.withdrawn)

        admin = _make_user('bpa51admin2', CustomUser.ROLE_IT)
        client = Client()
        client.force_login(admin)
        resp = client.get(reverse('verification:evaluation_trial_run', args=[trial.pk]), follow=True)
        self.assertContains(resp, 'withdrawn')
        trial.refresh_from_db()
        self.assertEqual(trial.trial_status, EvaluationTrial.TRIAL_STATUS_PENDING)  # unchanged, not started


# ─────────────────────────────────────────────────────────────────────────
# Liveness-proof retention across every controlled-trial exit path (§7/§10)
# ─────────────────────────────────────────────────────────────────────────

class LivenessProofRetentionTest(TestCase):
    def setUp(self):
        self.president = _make_user('bpa51president2', CustomUser.ROLE_PRESIDENT)

    def test_aborted_before_proof_captured_stays_null(self):
        """Abort path exercised before stage-1 ever ran — proof was never
        set, must stay null (harmless no-op through the new clearing line)."""
        dataset = _make_dataset(status=EvaluationDataset.STATUS_COLLECTING)
        trial = EvaluationTrial.objects.create(
            dataset=dataset, trial_status=EvaluationTrial.TRIAL_STATUS_PENDING,
            participant_code='P-1', target_identity_code='T-1', identity_ground_truth=GENUINE,
        )
        _abort_trial(trial, self.president, reason='no image captured')
        trial.refresh_from_db()
        self.assertIsNone(trial.liveness_proof_embedding)
        self.assertEqual(trial.trial_status, EvaluationTrial.TRIAL_STATUS_ABORTED)

    def test_aborted_after_proof_captured_clears_proof(self):
        """THE regression this checkpoint exists to close: BPA-5 only
        verified _complete_trial() clears the proof. Every _abort_trial()
        call site that fires AFTER stage-1 already stored a proof (e.g. a
        stage-2 final-frame failure) left the encrypted embedding on the row
        forever. _abort_trial must now clear it too."""
        dataset = _make_dataset(status=EvaluationDataset.STATUS_COLLECTING)
        trial = EvaluationTrial.objects.create(
            dataset=dataset, trial_status=EvaluationTrial.TRIAL_STATUS_PENDING,
            participant_code='P-2', target_identity_code='T-2', identity_ground_truth=GENUINE,
            liveness_proof_embedding=b'\x01\x02\x03\x04', liveness_captured_at=timezone.now(),
        )
        _abort_trial(trial, self.president, reason='final-frame face detection failed')
        trial.refresh_from_db()
        self.assertIsNone(trial.liveness_proof_embedding)
        self.assertEqual(trial.trial_status, EvaluationTrial.TRIAL_STATUS_ABORTED)

    def test_operator_initiated_abort_view_clears_proof(self):
        """The explicit operator "Abort Trial" action (evaluation_trial_abort)
        on a PENDING trial that already captured stage-1 proof must clear it
        — minimum safe policy for an abandoned-then-explicitly-aborted trial."""
        dataset = _make_dataset(status=EvaluationDataset.STATUS_COLLECTING)
        trial = EvaluationTrial.objects.create(
            dataset=dataset, trial_status=EvaluationTrial.TRIAL_STATUS_PENDING,
            participant_code='P-3', target_identity_code='T-3', identity_ground_truth=GENUINE,
            liveness_proof_embedding=b'\x05\x06\x07\x08', liveness_captured_at=timezone.now(),
        )
        admin = _make_user('bpa51admin3', CustomUser.ROLE_IT)
        client = Client()
        client.force_login(admin)
        client.post(reverse('verification:evaluation_trial_abort', args=[trial.pk]))
        trial.refresh_from_db()
        self.assertEqual(trial.trial_status, EvaluationTrial.TRIAL_STATUS_ABORTED)
        self.assertIsNone(trial.liveness_proof_embedding)

    def test_withdrawn_trial_proof_cleared(self):
        """A PENDING trial withdrawn after stage-1 capture must not keep the
        encrypted proof around — it can never legitimately resume."""
        dataset = _make_dataset(status=EvaluationDataset.STATUS_COLLECTING)
        trial = EvaluationTrial.objects.create(
            dataset=dataset, trial_status=EvaluationTrial.TRIAL_STATUS_PENDING,
            participant_code='P-4', target_identity_code='T-4', identity_ground_truth=GENUINE,
            liveness_proof_embedding=b'\x09\x0a\x0b\x0c', liveness_captured_at=timezone.now(),
        )
        it = _make_user('bpa51it4', CustomUser.ROLE_IT)
        client = Client()
        client.force_login(it)
        client.post(
            reverse('verification:evaluation_trial_withdraw', args=[trial.pk]),
            {'withdrawal_reason': 'participant withdrew mid-capture'},
        )
        trial.refresh_from_db()
        self.assertTrue(trial.withdrawn)
        self.assertIsNone(trial.liveness_proof_embedding)

    def test_completed_trial_proof_cleared_via_complete_trial(self):
        """Baseline (already true since BPA-5, re-verified here for
        completeness of the exit-path matrix): COMPLETED clears the proof."""
        dataset = _make_dataset(status=EvaluationDataset.STATUS_COLLECTING)
        trial = EvaluationTrial.objects.create(
            dataset=dataset, trial_status=EvaluationTrial.TRIAL_STATUS_PENDING,
            participant_code='P-5', target_identity_code='T-5', identity_ground_truth=GENUINE,
            liveness_proof_embedding=b'\x0d\x0e\x0f\x10',
        )
        _complete_trial(trial, self.president, decision=VERIFIED, score=0.9, threshold=0.6, auto_verify_threshold=0.85)
        trial.refresh_from_db()
        self.assertIsNone(trial.liveness_proof_embedding)

    def test_technical_failure_reaching_denied_clears_proof(self):
        """A DENIED same-face-mismatch outcome (_complete_trial, not
        _abort_trial) is a legitimate terminal decision, not a technical
        failure — confirms the proof does not survive that path either."""
        dataset = _make_dataset(status=EvaluationDataset.STATUS_COLLECTING)
        trial = EvaluationTrial.objects.create(
            dataset=dataset, trial_status=EvaluationTrial.TRIAL_STATUS_PENDING,
            participant_code='P-6', target_identity_code='T-6', identity_ground_truth=GENUINE,
            liveness_proof_embedding=b'\x11\x12\x13\x14',
        )
        _complete_trial(trial, self.president, decision=DENIED, score=None, threshold=0.6, auto_verify_threshold=0.85)
        trial.refresh_from_db()
        self.assertIsNone(trial.liveness_proof_embedding)

    def test_active_pending_trial_that_may_resume_keeps_proof(self):
        """Documents the deliberate exception: a genuinely active PENDING
        trial (proof just captured, not yet run, not withdrawn/aborted) must
        keep its proof — same-face binding still needs it for stage 2."""
        dataset = _make_dataset(status=EvaluationDataset.STATUS_COLLECTING)
        trial = EvaluationTrial.objects.create(
            dataset=dataset, trial_status=EvaluationTrial.TRIAL_STATUS_PENDING,
            participant_code='P-7', target_identity_code='T-7', identity_ground_truth=GENUINE,
            liveness_proof_embedding=b'\x15\x16\x17\x18', liveness_captured_at=timezone.now(),
        )
        trial.refresh_from_db()
        self.assertIsNotNone(trial.liveness_proof_embedding)
        self.assertEqual(trial.trial_status, EvaluationTrial.TRIAL_STATUS_PENDING)

    def test_no_raw_embedding_ever_written_to_auditlog(self):
        """No AuditLog.details for any evaluation-trial action ever carries
        the raw/encrypted embedding bytes."""
        dataset = _make_dataset(status=EvaluationDataset.STATUS_COLLECTING)
        trial = EvaluationTrial.objects.create(
            dataset=dataset, trial_status=EvaluationTrial.TRIAL_STATUS_PENDING,
            participant_code='P-8', target_identity_code='T-8', identity_ground_truth=GENUINE,
            liveness_proof_embedding=b'\x19\x1a\x1b\x1c', liveness_captured_at=timezone.now(),
        )
        _abort_trial(trial, self.president, reason='technical failure')
        for log in AuditLog.objects.filter(target_id=str(trial.id)):
            details_str = str(log.details)
            self.assertNotIn('liveness_proof_embedding', details_str)
            self.assertNotIn('\\x19\\x1a\\x1b\\x1c', details_str)


# ─────────────────────────────────────────────────────────────────────────
# Abandoned PENDING trials with a captured proof — dashboard warning (§9)
# ─────────────────────────────────────────────────────────────────────────

class StalePendingProofWarningTest(TestCase):
    def test_stale_pending_trial_with_proof_is_flagged(self):
        dataset = _make_dataset(status=EvaluationDataset.STATUS_COLLECTING)
        stale = EvaluationTrial.objects.create(
            dataset=dataset, trial_status=EvaluationTrial.TRIAL_STATUS_PENDING,
            participant_code='P-stale', target_identity_code='T-1', identity_ground_truth=GENUINE,
            liveness_proof_embedding=b'\x01\x02',
        )
        EvaluationTrial.objects.filter(pk=stale.pk).update(
            liveness_captured_at=timezone.now() - timedelta(hours=48),
        )
        stale_qs = ba.get_stale_pending_trials_with_proof(dataset)
        self.assertEqual(stale_qs.count(), 1)
        self.assertEqual(stale_qs.first().pk, stale.pk)

    def test_recently_captured_pending_trial_not_flagged(self):
        """An active pending trial that may legitimately resume (captured
        moments ago) must NOT be flagged as stale."""
        dataset = _make_dataset(status=EvaluationDataset.STATUS_COLLECTING)
        EvaluationTrial.objects.create(
            dataset=dataset, trial_status=EvaluationTrial.TRIAL_STATUS_PENDING,
            participant_code='P-fresh', target_identity_code='T-2', identity_ground_truth=GENUINE,
            liveness_proof_embedding=b'\x03\x04', liveness_captured_at=timezone.now(),
        )
        self.assertEqual(ba.get_stale_pending_trials_with_proof(dataset).count(), 0)

    def test_withdrawn_stale_trial_not_flagged(self):
        """Already withdrawn (and therefore already proof-cleared) — must
        not double-count as a retention risk."""
        dataset = _make_dataset(status=EvaluationDataset.STATUS_COLLECTING)
        trial = EvaluationTrial.objects.create(
            dataset=dataset, trial_status=EvaluationTrial.TRIAL_STATUS_PENDING,
            participant_code='P-wd', target_identity_code='T-3', identity_ground_truth=GENUINE,
            withdrawn=True, withdrawal_reason='w', withdrawn_at=timezone.now(),
        )
        EvaluationTrial.objects.filter(pk=trial.pk).update(
            liveness_captured_at=timezone.now() - timedelta(hours=48),
        )
        self.assertEqual(ba.get_stale_pending_trials_with_proof(dataset).count(), 0)

    def test_completed_trial_not_flagged_regardless_of_age(self):
        dataset = _make_dataset(status=EvaluationDataset.STATUS_COLLECTING)
        trial = _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED)
        EvaluationTrial.objects.filter(pk=trial.pk).update(
            liveness_captured_at=timezone.now() - timedelta(hours=48),
        )
        self.assertEqual(ba.get_stale_pending_trials_with_proof(dataset).count(), 0)

    def test_dataset_detail_view_surfaces_stale_warning(self):
        dataset = _make_dataset(status=EvaluationDataset.STATUS_COLLECTING)
        stale = EvaluationTrial.objects.create(
            dataset=dataset, trial_status=EvaluationTrial.TRIAL_STATUS_PENDING,
            participant_code='P-stale2', target_identity_code='T-4', identity_ground_truth=GENUINE,
            liveness_proof_embedding=b'\x01\x02',
        )
        EvaluationTrial.objects.filter(pk=stale.pk).update(
            liveness_captured_at=timezone.now() - timedelta(hours=48),
        )
        admin = _make_user('bpa51admin4', CustomUser.ROLE_IT)
        client = Client()
        client.force_login(admin)
        resp = client.get(reverse('verification:evaluation_dataset_detail', args=[dataset.pk]))
        self.assertContains(resp, 'stale PENDING trial')
