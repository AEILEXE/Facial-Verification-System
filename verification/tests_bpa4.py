"""
BPA-4 — Biometric Performance Analytics UI tests.

Covers: role/permission gating (President/Admin/IT allowed, Staff/anonymous
denied), finalized/interim/archived banner wording, zero-denominator "Not
available" rendering (never a fabricated 0.00%), Manual Review staying
visible as a distinct outcome, matcher-vs-final divergence rendering,
attack-semantics wording (gate vs. end-to-end kept separate), negative
similarity-score handling, ROC empty/valid states, EER wording constraints,
Claim Recording Reliability wording, dataset-selector validation, the
percentage-format convention, and non-regression of the existing Executive/
Operational/Security analytics tabs.

All fixtures here are SYNTHETIC TEST DATA — never real study observations.
Reuses the BPA-3 dataset/trial factories (`_make_dataset`/`_make_trial`).
"""
import uuid

from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import CustomUser
from verification.models import EvaluationDataset, EvaluationTrial, VerificationAttempt
from verification.templatetags.bpa_filters import rate_pct, frac_pct, decimal_or_na
from verification.tests_bpa3 import _make_dataset, _make_trial

VERIFIED = VerificationAttempt.DECISION_VERIFIED
MANUAL_REVIEW = VerificationAttempt.DECISION_MANUAL_REVIEW
NOT_VERIFIED = VerificationAttempt.DECISION_NOT_VERIFIED
DENIED = VerificationAttempt.DECISION_DENIED

GENUINE = EvaluationTrial.GROUND_TRUTH_GENUINE
IMPOSTOR = EvaluationTrial.GROUND_TRUTH_IMPOSTOR

BONA_FIDE = EvaluationTrial.PRESENTATION_BONA_FIDE
PRINT_PHOTO = EvaluationTrial.PRESENTATION_PRINT_PHOTO

PASSIVE_ONLY = EvaluationTrial.LIVENESS_PATHWAY_PASSIVE_ONLY
FAILED_PASSIVE = EvaluationTrial.LIVENESS_PATHWAY_FAILED_PASSIVE

URL = 'verification:analytics_biometric_performance'


def _make_user(username, role):
    return CustomUser.objects.create_user(
        username=username, password='TestPass123!',
        role=role, employee_id=f'EMP-{username.upper()}',
    )


# ─────────────────────────────────────────────────────────────────────────
# Percentage-format convention (BPA-4 §41)
# ─────────────────────────────────────────────────────────────────────────

class PercentageFormatFilterTest(TestCase):
    def test_fraction_rate_dict_renders_as_percent_not_shifted(self):
        # 0.1234 must render "12.34%", never "0.12%".
        self.assertEqual(rate_pct({'numerator': 1234, 'denominator': 10000, 'rate': 0.1234}), '12.34%')

    def test_none_rate_is_not_available_not_zero_percent(self):
        self.assertEqual(rate_pct({'numerator': 0, 'denominator': 0, 'rate': None}), 'Not available')

    def test_measured_zero_rate_is_zero_percent(self):
        self.assertEqual(rate_pct({'numerator': 0, 'denominator': 4, 'rate': 0.0}), '0.00%')

    def test_frac_pct_matches_rate_pct_convention(self):
        self.assertEqual(frac_pct(0.5), '50.00%')
        self.assertEqual(frac_pct(None), 'Not available')

    def test_decimal_or_na(self):
        self.assertEqual(decimal_or_na(0.8834), '0.8834')
        self.assertEqual(decimal_or_na(None), 'Not available')


# ─────────────────────────────────────────────────────────────────────────
# Role / permission gating (BPA-4 §3/§51)
# ─────────────────────────────────────────────────────────────────────────

class AccessControlTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.president = _make_user('bpa4president', CustomUser.ROLE_PRESIDENT)
        self.admin = _make_user('bpa4admin', CustomUser.ROLE_ADMIN)
        self.it = _make_user('bpa4it', CustomUser.ROLE_IT)
        self.staff = _make_user('bpa4staff', CustomUser.ROLE_STAFF)

    def test_president_has_access(self):
        self.client.force_login(self.president)
        resp = self.client.get(reverse(URL))
        self.assertEqual(resp.status_code, 200)

    def test_admin_denied_access(self):
        """v2.1.19 UX pass section 19: Admin gets no Biometric Evaluation
        access at all (a tightening from the original admin-tier gate)."""
        self.client.force_login(self.admin)
        resp = self.client.get(reverse(URL))
        self.assertRedirects(resp, reverse('beneficiaries:dashboard'))

    def test_it_has_access(self):
        self.client.force_login(self.it)
        resp = self.client.get(reverse(URL))
        self.assertEqual(resp.status_code, 200)

    def test_staff_is_denied(self):
        self.client.force_login(self.staff)
        resp = self.client.get(reverse(URL))
        self.assertRedirects(resp, reverse('beneficiaries:dashboard'))

    def test_anonymous_is_redirected_to_login(self):
        resp = self.client.get(reverse(URL))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login', resp.url)


# ─────────────────────────────────────────────────────────────────────────
# Dataset selector validation (BPA-4 §37)
# ─────────────────────────────────────────────────────────────────────────

class DatasetSelectorValidationTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.client.force_login(_make_user('bpa4sel', CustomUser.ROLE_IT))

    def test_no_dataset_selected_shows_selection_state(self):
        _make_dataset(status=EvaluationDataset.STATUS_COLLECTING)
        _make_dataset(status=EvaluationDataset.STATUS_COLLECTING, name='Second')
        resp = self.client.get(reverse(URL))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'No finalized evaluation session')

    def test_malformed_uuid_is_handled_safely(self):
        resp = self.client.get(reverse(URL), {'dataset': 'not-a-uuid'})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'could not be found')

    def test_nonexistent_dataset_uuid_is_handled_safely(self):
        resp = self.client.get(reverse(URL), {'dataset': str(uuid.uuid4())})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'could not be found')

    def test_sole_dataset_is_preselected(self):
        dataset = _make_dataset(status=EvaluationDataset.STATUS_COLLECTING)
        resp = self.client.get(reverse(URL))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, dataset.name)


# ─────────────────────────────────────────────────────────────────────────
# Finalized / interim / archived banner wording (BPA-4 §42)
# ─────────────────────────────────────────────────────────────────────────

class DatasetStatusBannerTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.client.force_login(_make_user('bpa4banner', CustomUser.ROLE_IT))

    def _get(self, dataset):
        return self.client.get(reverse(URL), {'dataset': str(dataset.id)})

    def test_completed_dataset_renders_finalized_wording(self):
        dataset = _make_dataset(status=EvaluationDataset.STATUS_COMPLETED)
        resp = self._get(dataset)
        self.assertContains(resp, 'FINALIZED EVALUATION SESSION')
        self.assertNotContains(resp, 'DATA COLLECTION NOT FINALIZED')

    def test_collecting_dataset_renders_interim_wording(self):
        dataset = _make_dataset(status=EvaluationDataset.STATUS_COLLECTING)
        resp = self._get(dataset)
        self.assertContains(resp, 'INTERIM CONTROLLED-EVALUATION RESULTS')
        self.assertContains(resp, 'DATA COLLECTION NOT FINALIZED')

    def test_draft_dataset_renders_interim_wording(self):
        dataset = _make_dataset(status=EvaluationDataset.STATUS_DRAFT)
        resp = self._get(dataset)
        self.assertContains(resp, 'INTERIM CONTROLLED-EVALUATION RESULTS')

    def test_archived_dataset_renders_archived_wording(self):
        dataset = _make_dataset(status=EvaluationDataset.STATUS_ARCHIVED)
        resp = self._get(dataset)
        self.assertContains(resp, 'ARCHIVED EVALUATION SESSION')
        self.assertNotContains(resp, 'FINALIZED EVALUATION SESSION')


# ─────────────────────────────────────────────────────────────────────────
# Zero-denominator rendering (BPA-4 §43)
# ─────────────────────────────────────────────────────────────────────────

class ZeroDenominatorViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.client.force_login(_make_user('bpa4zero', CustomUser.ROLE_IT))

    def test_no_genuine_trials_renders_tar_frr_not_available(self):
        dataset = _make_dataset()
        _make_trial(dataset, identity_ground_truth=IMPOSTOR, system_decision=NOT_VERIFIED, similarity_score=0.1)
        resp = self.client.get(reverse(URL), {'dataset': str(dataset.id)})
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertIn('Not available', content)
        self.assertNotIn('TAR 0.00%', content)
        self.assertNotIn('FRR 0.00%', content)

    def test_no_impostor_trials_renders_far_tnr_not_available(self):
        dataset = _make_dataset()
        _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED, similarity_score=0.9)
        resp = self.client.get(reverse(URL), {'dataset': str(dataset.id)})
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertIn('Not available', content)


# ─────────────────────────────────────────────────────────────────────────
# Manual Review stays a visible, distinct outcome (BPA-4 §44)
# ─────────────────────────────────────────────────────────────────────────

class ManualReviewViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.client.force_login(_make_user('bpa4mr', CustomUser.ROLE_IT))
        self.dataset = _make_dataset()
        for decision in (VERIFIED, VERIFIED, MANUAL_REVIEW, NOT_VERIFIED):
            _make_trial(self.dataset, identity_ground_truth=GENUINE, system_decision=decision, similarity_score=0.8)
        for decision in (NOT_VERIFIED, MANUAL_REVIEW, VERIFIED, DENIED):
            _make_trial(self.dataset, identity_ground_truth=IMPOSTOR, system_decision=decision, similarity_score=0.5)

    def test_manual_review_appears_and_is_not_called_a_rejection(self):
        resp = self.client.get(reverse(URL), {'dataset': str(self.dataset.id)})
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertIn('Manual Review', content)
        self.assertIn('Manual Review is an intentional safety outcome', content)
        # Matrix counts: genuine manual_review=1, rejected=1 — kept in separate columns.
        self.assertIn('Genuine Manual Review Rate', content)
        self.assertIn('Impostor Manual Review Rate', content)

    def test_coverage_and_conditional_accuracy_reflect_manual_review_exclusion(self):
        from verification import biometric_analytics as ba
        result = ba.get_final_system_performance(self.dataset)
        # 8 eligible, 6 auto-decided (MR excluded from numerator/denominator base of accuracy).
        self.assertEqual(result['coverage']['denominator'], 8)
        self.assertEqual(result['coverage']['numerator'], 6)
        self.assertEqual(result['conditional_automatic_accuracy']['denominator'], 6)


# ─────────────────────────────────────────────────────────────────────────
# Matcher vs. final-system divergence (BPA-4 §45)
# ─────────────────────────────────────────────────────────────────────────

class MatcherVsFinalViewTest(TestCase):
    def test_escalation_case_shows_divergent_matcher_and_final_results(self):
        client = Client()
        client.force_login(_make_user('bpa4matcher', CustomUser.ROLE_IT))
        dataset = _make_dataset()
        # matcher says VERIFIED, final system escalates to MANUAL_REVIEW.
        _make_trial(
            dataset, identity_ground_truth=GENUINE, system_decision=MANUAL_REVIEW,
            matcher_base_decision=VERIFIED, similarity_score=0.85,
            lookalike_escalation_applied=True,
        )
        resp = client.get(reverse(URL), {'dataset': str(dataset.id)})
        self.assertEqual(resp.status_code, 200)

        from verification import biometric_analytics as ba
        matcher_result = ba.get_matcher_performance(dataset)
        final_result = ba.get_final_system_performance(dataset)
        # Matcher cohort saw a VERIFIED (TAR contribution); final cohort saw MANUAL_REVIEW (GMRR contribution).
        self.assertEqual(matcher_result['matrix']['genuine']['verified'], 1)
        self.assertEqual(matcher_result['matrix']['genuine']['manual_review'], 0)
        self.assertEqual(final_result['matrix']['genuine']['verified'], 0)
        self.assertEqual(final_result['matrix']['genuine']['manual_review'], 1)

        content = resp.content.decode()
        self.assertIn('excludes FANS-C', content)
        self.assertIn('Lookalike Escalation Applied', content)


# ─────────────────────────────────────────────────────────────────────────
# Attack semantics: gate vs. end-to-end kept distinct (BPA-4 §46)
# ─────────────────────────────────────────────────────────────────────────

class AttackSemanticsViewTest(TestCase):
    def test_gate_passed_but_final_not_verified_is_not_claimed_as_pad_detection(self):
        client = Client()
        client.force_login(_make_user('bpa4attack', CustomUser.ROLE_IT))
        dataset = _make_dataset()
        # PRINT_PHOTO attack: gate PASSES (liveness_pathway recorded as passed),
        # matcher rejects, final system NOT_VERIFIED.
        _make_trial(
            dataset, identity_ground_truth=IMPOSTOR, system_decision=NOT_VERIFIED,
            presentation_ground_truth=PRINT_PHOTO, liveness_pathway=PASSIVE_ONLY,
            similarity_score=0.1,
        )
        resp = client.get(reverse(URL), {'dataset': str(dataset.id)})
        self.assertEqual(resp.status_code, 200)

        from verification import biometric_analytics as ba
        gate = ba.get_presentation_attack_gate_metrics(dataset)
        e2e = ba.get_end_to_end_attack_outcome(dataset)
        # Gate: passed (not blocked).
        self.assertEqual(gate['overall_attack']['gate_passed_count'], 1)
        self.assertEqual(gate['overall_attack']['gate_blocked_count'], 0)
        # End-to-end: intercepted (non-acceptance), not accepted.
        self.assertEqual(e2e['overall']['automatically_intercepted'], 1)
        self.assertEqual(e2e['overall']['incorrectly_automatically_accepted'], 0)

        content = resp.content.decode()
        self.assertIn('not equivalent to PAD detection', content)


# ─────────────────────────────────────────────────────────────────────────
# Negative similarity score handling (BPA-4 §47)
# ─────────────────────────────────────────────────────────────────────────

class NegativeScoreUIViewTest(TestCase):
    def test_negative_score_is_not_clamped_or_hidden(self):
        client = Client()
        client.force_login(_make_user('bpa4neg', CustomUser.ROLE_IT))
        dataset = _make_dataset()
        _make_trial(dataset, identity_ground_truth=IMPOSTOR, system_decision=NOT_VERIFIED, similarity_score=-0.20)
        _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED, similarity_score=0.9)
        resp = client.get(reverse(URL), {'dataset': str(dataset.id)})
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        # score_domain must be the full cosine domain, not [0,1].
        self.assertIn('-1.0', content)
        # The chart payload (json_script) must carry the negative-score bin data verbatim.
        self.assertIn('"score_domain"', content)


# ─────────────────────────────────────────────────────────────────────────
# ROC empty / valid states (BPA-4 §48)
# ─────────────────────────────────────────────────────────────────────────

class RocViewStateTest(TestCase):
    def test_no_impostor_scores_shows_roc_unavailable(self):
        client = Client()
        client.force_login(_make_user('bpa4rocA', CustomUser.ROLE_IT))
        dataset = _make_dataset()
        _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED, similarity_score=0.9)
        resp = client.get(reverse(URL), {'dataset': str(dataset.id)})
        content = resp.content.decode()
        self.assertIn('No impostor scores available', content)

    def test_both_classes_present_shows_chart_and_auc(self):
        client = Client()
        client.force_login(_make_user('bpa4rocB', CustomUser.ROLE_IT))
        dataset = _make_dataset()
        _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED, similarity_score=0.9)
        _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED, similarity_score=0.85)
        _make_trial(dataset, identity_ground_truth=IMPOSTOR, system_decision=NOT_VERIFIED, similarity_score=0.2)
        _make_trial(dataset, identity_ground_truth=IMPOSTOR, system_decision=NOT_VERIFIED, similarity_score=0.1)
        resp = client.get(reverse(URL), {'dataset': str(dataset.id)})
        content = resp.content.decode()
        self.assertIn('id="bpaRocChart"', content)
        self.assertNotIn('both genuine and impostor score samples are required', content)

        from verification import biometric_analytics as ba
        auc_result = ba.compute_auc(dataset)
        self.assertIsNotNone(auc_result['auc'])


# ─────────────────────────────────────────────────────────────────────────
# EER wording constraints (BPA-4 §49)
# ─────────────────────────────────────────────────────────────────────────

class EerWordingTest(TestCase):
    def test_eer_wording_present_and_no_optimal_or_recommended_language(self):
        client = Client()
        client.force_login(_make_user('bpa4eer', CustomUser.ROLE_IT))
        dataset = _make_dataset()
        _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED, similarity_score=0.9)
        _make_trial(dataset, identity_ground_truth=IMPOSTOR, system_decision=NOT_VERIFIED, similarity_score=0.1)
        resp = client.get(reverse(URL), {'dataset': str(dataset.id)})
        content = resp.content.decode()
        self.assertIn('Estimated Equal Error Rate', content)
        self.assertNotIn('Optimal Threshold', content)
        self.assertNotIn('Recommended Threshold', content)


# ─────────────────────────────────────────────────────────────────────────
# Claim Recording Reliability wording (BPA-4 §50)
# ─────────────────────────────────────────────────────────────────────────

class ClaimRecordingReliabilityWordingTest(TestCase):
    def test_shown_as_not_computable_never_a_bare_percentage(self):
        client = Client()
        client.force_login(_make_user('bpa4claim', CustomUser.ROLE_IT))
        dataset = _make_dataset()
        resp = client.get(reverse(URL), {'dataset': str(dataset.id)})
        content = resp.content.decode()
        self.assertIn('Not retrospectively computable', content)
        self.assertNotIn('Claim Recording Reliability: 0%', content)
        self.assertNotIn('Claim Recording Reliability: 100%', content)


# ─────────────────────────────────────────────────────────────────────────
# Threshold sweep — no "optimal"/"recommended" claim (BPA-4 §17-18)
# ─────────────────────────────────────────────────────────────────────────

class ThresholdSweepWordingTest(TestCase):
    def test_no_optimal_or_recommended_language_in_sweep_section(self):
        client = Client()
        client.force_login(_make_user('bpa4sweep', CustomUser.ROLE_IT))
        dataset = _make_dataset()
        _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED, similarity_score=0.9)
        _make_trial(dataset, identity_ground_truth=IMPOSTOR, system_decision=NOT_VERIFIED, similarity_score=0.1)
        resp = client.get(reverse(URL), {'dataset': str(dataset.id)})
        content = resp.content.decode()
        self.assertIn('Observed Trade-off', content)
        self.assertNotIn('Optimal', content)
        self.assertNotIn('Best', content)
        self.assertNotIn('Recommended 0.70', content)


# ─────────────────────────────────────────────────────────────────────────
# Existing Analytics regression (BPA-4 §52)
# ─────────────────────────────────────────────────────────────────────────

class ExistingAnalyticsRegressionTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.client.force_login(_make_user('bpa4regress', CustomUser.ROLE_IT))

    def test_executive_operational_security_still_render(self):
        for name in ('verification:analytics_executive', 'verification:analytics_operational', 'verification:analytics_security'):
            resp = self.client.get(reverse(name))
            self.assertEqual(resp.status_code, 200, f'{name} should still render 200')

    def test_biometric_tab_appears_in_nav_between_operational_and_security(self):
        resp = self.client.get(reverse('verification:analytics_operational'))
        content = resp.content.decode()
        op_idx = content.index('bi-graph-up me-1')
        bio_idx = content.index('bi-fingerprint me-1')
        sec_idx = content.index('bi-shield-exclamation me-1')
        self.assertTrue(op_idx < bio_idx < sec_idx)
