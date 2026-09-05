"""
BPA-3 — Biometric Performance Metric Engine tests.

Covers: hand-verified three-outcome matrix + final-system formulas, metric
invariants (TAR+FRR+GMRR=1, FAR+TNR+IMRR=1), zero/empty-denominator
behavior (never a fabricated 0%), matcher-vs-final-decision divergence,
beneficiary/representative stratification, presentation-attack three-way
outcome, timing statistics, threshold sweep (reusing the shared
decide_base_outcome), ROC/AUC/EER on small deterministic score sets, claim
recording reliability's "not computable" result, dataset isolation, the
finalized/interim distinction, and query-count stability (no N+1 per
trial).

All fixtures here are SYNTHETIC TEST DATA — never real study observations.
"""
import uuid

from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.utils import timezone

from beneficiaries.models import Beneficiary, Representative
from verification import biometric_analytics as ba
from verification import face_utils
from verification.models import EvaluationDataset, EvaluationTrial, VerificationAttempt

VERIFIED = VerificationAttempt.DECISION_VERIFIED
MANUAL_REVIEW = VerificationAttempt.DECISION_MANUAL_REVIEW
NOT_VERIFIED = VerificationAttempt.DECISION_NOT_VERIFIED
DENIED = VerificationAttempt.DECISION_DENIED

GENUINE = EvaluationTrial.GROUND_TRUTH_GENUINE
IMPOSTOR = EvaluationTrial.GROUND_TRUTH_IMPOSTOR

BONA_FIDE = EvaluationTrial.PRESENTATION_BONA_FIDE
PRINT_PHOTO = EvaluationTrial.PRESENTATION_PRINT_PHOTO
SCREEN_REPLAY = EvaluationTrial.PRESENTATION_SCREEN_REPLAY
OTHER_ATTACK = EvaluationTrial.PRESENTATION_OTHER_ATTACK
NOT_TESTED = EvaluationTrial.PRESENTATION_NOT_TESTED


def _make_dataset(status=EvaluationDataset.STATUS_COMPLETED, **kwargs):
    defaults = dict(name='BPA-3 Test Dataset', protocol_version='v1', status=status)
    if status == EvaluationDataset.STATUS_COMPLETED:
        defaults['completed_at'] = timezone.now()
    defaults.update(kwargs)
    return EvaluationDataset.objects.create(**defaults)


_counter = [0]


def _make_trial(dataset, *, identity_ground_truth, system_decision,
                 presentation_ground_truth=BONA_FIDE,
                 trial_status=EvaluationTrial.TRIAL_STATUS_COMPLETED,
                 matcher_base_decision='__same__', similarity_score=None,
                 participant_code=None, target_identity_code='T-001',
                 target_beneficiary=None, target_representative=None,
                 verification_duration_ms=None, liveness_pathway='',
                 quality_override_applied=False, lookalike_escalation_applied=False,
                 representative_fallback_blocked=False):
    _counter[0] += 1
    if participant_code is None:
        participant_code = f'P-{_counter[0]:04d}'
    if matcher_base_decision == '__same__':
        matcher_base_decision = system_decision
    return EvaluationTrial.objects.create(
        dataset=dataset,
        trial_status=trial_status,
        participant_code=participant_code,
        target_identity_code=target_identity_code,
        identity_ground_truth=identity_ground_truth,
        presentation_ground_truth=presentation_ground_truth,
        system_decision=system_decision,
        matcher_base_decision=matcher_base_decision,
        similarity_score=similarity_score,
        target_beneficiary=target_beneficiary,
        target_representative=target_representative,
        verification_duration_ms=verification_duration_ms,
        liveness_pathway=liveness_pathway,
        quality_override_applied=quality_override_applied,
        lookalike_escalation_applied=lookalike_escalation_applied,
        representative_fallback_blocked=representative_fallback_blocked,
    )


def _make_beneficiary(ben_id='BEN-BPA3-0001', sc_id='SC-BPA3-0001'):
    return Beneficiary.objects.create(
        beneficiary_id=ben_id, first_name='Target', last_name='Identity',
        senior_citizen_id=sc_id, date_of_birth='1945-01-01', gender='F',
        address='123 Test St', barangay='Test Barangay',
        municipality='Quezon City', province='Metro Manila',
    )


def _make_representative(beneficiary):
    return Representative.objects.create(
        beneficiary=beneficiary, first_name='Jose', last_name='Rizal',
        relationship='Son', contact_number='09171234567',
        valid_id_type='SSS', valid_id_number=f'SSS-{uuid.uuid4().hex[:8]}',
    )


# ─────────────────────────────────────────────────────────────────────────
# Rate helper — zero-denominator behavior (BPA-3 §12/§34)
# ─────────────────────────────────────────────────────────────────────────

class RateHelperTest(TestCase):
    def test_zero_denominator_is_none_not_zero_percent(self):
        result = ba._rate(0, 0)
        self.assertIsNone(result['rate'])
        self.assertEqual(result['denominator'], 0)

    def test_nonzero_denominator_computes_fraction(self):
        result = ba._rate(1, 4)
        self.assertEqual(result['rate'], 0.25)
        self.assertEqual(result['numerator'], 1)
        self.assertEqual(result['denominator'], 4)

    def test_numerator_zero_with_positive_denominator_is_zero_not_none(self):
        # A genuinely measured zero (0 out of 4) must render as 0.0, not None —
        # only an empty denominator should be None.
        result = ba._rate(0, 4)
        self.assertEqual(result['rate'], 0.0)


# ─────────────────────────────────────────────────────────────────────────
# Hand-verified 2x3 matrix + final-system formulas (BPA-3 §32/§33)
# ─────────────────────────────────────────────────────────────────────────

class FinalSystemMatrixTest(TestCase):
    """
    GENUINE:  VERIFIED, VERIFIED, MANUAL_REVIEW, NOT_VERIFIED   (n=4)
    IMPOSTOR: NOT_VERIFIED, MANUAL_REVIEW, VERIFIED, DENIED     (n=4)

    By hand:
      TAR  = 2/4 = 0.5      FRR  = 1/4 = 0.25    GMRR = 1/4 = 0.25  (sum=1.0)
      FAR  = 1/4 = 0.25     TNR  = 2/4 = 0.5     IMRR = 1/4 = 0.25  (sum=1.0)
      eligible = 8, auto_decided = 2(gen verified)+1(gen rejected)+1(imp verified)+2(imp rejected) = 6
      Coverage = 6/8 = 0.75
      correct_auto = 2 (gen verified) + 2 (imp rejected) = 4
      Conditional Automatic Accuracy = 4/6 = 0.6666...
      Correct Automatic Outcome Rate = 4/8 = 0.5
    """

    def setUp(self):
        self.dataset = _make_dataset()
        for decision in (VERIFIED, VERIFIED, MANUAL_REVIEW, NOT_VERIFIED):
            _make_trial(self.dataset, identity_ground_truth=GENUINE, system_decision=decision)
        for decision in (NOT_VERIFIED, MANUAL_REVIEW, VERIFIED, DENIED):
            _make_trial(self.dataset, identity_ground_truth=IMPOSTOR, system_decision=decision)

    def test_matrix_cell_counts(self):
        result = ba.get_final_system_performance(self.dataset)
        matrix = result['matrix']
        self.assertEqual(matrix['genuine'], {'verified': 2, 'manual_review': 1, 'rejected': 1, 'total': 4})
        self.assertEqual(matrix['impostor'], {'verified': 1, 'manual_review': 1, 'rejected': 2, 'total': 4})

    def test_genuine_rates(self):
        result = ba.get_final_system_performance(self.dataset)
        self.assertAlmostEqual(result['tar']['rate'], 0.5)
        self.assertAlmostEqual(result['frr']['rate'], 0.25)
        self.assertAlmostEqual(result['gmrr']['rate'], 0.25)

    def test_impostor_rates(self):
        result = ba.get_final_system_performance(self.dataset)
        self.assertAlmostEqual(result['far']['rate'], 0.25)
        self.assertAlmostEqual(result['tnr']['rate'], 0.5)
        self.assertAlmostEqual(result['imrr']['rate'], 0.25)

    def test_coverage_and_conditional_accuracy(self):
        result = ba.get_final_system_performance(self.dataset)
        self.assertAlmostEqual(result['coverage']['rate'], 0.75)
        self.assertAlmostEqual(result['conditional_automatic_accuracy']['rate'], 4 / 6)
        self.assertAlmostEqual(result['correct_automatic_outcome_rate']['rate'], 0.5)

    def test_manual_review_excluded_from_reject_category(self):
        result = ba.get_final_system_performance(self.dataset)
        genuine = result['matrix']['genuine']
        # MANUAL_REVIEW trials must never be double-counted or folded into 'rejected'.
        self.assertEqual(genuine['rejected'], 1)
        self.assertEqual(genuine['manual_review'], 1)
        self.assertEqual(genuine['verified'] + genuine['manual_review'] + genuine['rejected'], genuine['total'])


class MetricInvariantTest(TestCase):
    def test_genuine_and_impostor_invariants_hold(self):
        dataset = _make_dataset()
        for decision in (VERIFIED, VERIFIED, MANUAL_REVIEW, NOT_VERIFIED, DENIED):
            _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=decision)
        for decision in (VERIFIED, NOT_VERIFIED, NOT_VERIFIED, MANUAL_REVIEW):
            _make_trial(dataset, identity_ground_truth=IMPOSTOR, system_decision=decision)

        result = ba.get_final_system_performance(dataset)
        self.assertAlmostEqual(result['tar']['rate'] + result['frr']['rate'] + result['gmrr']['rate'], 1.0)
        self.assertAlmostEqual(result['far']['rate'] + result['tnr']['rate'] + result['imrr']['rate'], 1.0)
        self.assertGreaterEqual(result['coverage']['rate'], 0.0)
        self.assertLessEqual(result['coverage']['rate'], 1.0)
        self.assertGreaterEqual(result['conditional_automatic_accuracy']['rate'], 0.0)
        self.assertLessEqual(result['conditional_automatic_accuracy']['rate'], 1.0)
        for cell in (result['matrix']['genuine'].values(), result['matrix']['impostor'].values()):
            for v in cell:
                self.assertGreaterEqual(v, 0)


# ─────────────────────────────────────────────────────────────────────────
# Zero / empty datasets (BPA-3 §34)
# ─────────────────────────────────────────────────────────────────────────

class ZeroEmptyDatasetTest(TestCase):
    def test_empty_dataset_no_crash_all_rates_none(self):
        dataset = _make_dataset()
        result = ba.get_final_system_performance(dataset)
        self.assertIsNone(result['tar']['rate'])
        self.assertIsNone(result['far']['rate'])
        self.assertIsNone(result['coverage']['rate'])
        self.assertIsNone(result['conditional_automatic_accuracy']['rate'])

    def test_only_genuine_trials_impostor_rates_none(self):
        dataset = _make_dataset()
        _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED)
        result = ba.get_final_system_performance(dataset)
        self.assertIsNotNone(result['tar']['rate'])
        self.assertIsNone(result['far']['rate'])
        self.assertIsNone(result['tnr']['rate'])
        self.assertIsNone(result['imrr']['rate'])

    def test_only_impostor_trials_genuine_rates_none(self):
        dataset = _make_dataset()
        _make_trial(dataset, identity_ground_truth=IMPOSTOR, system_decision=NOT_VERIFIED)
        result = ba.get_final_system_performance(dataset)
        self.assertIsNone(result['tar']['rate'])
        self.assertIsNone(result['frr']['rate'])
        self.assertIsNone(result['gmrr']['rate'])
        self.assertIsNotNone(result['tnr']['rate'])

    def test_only_aborted_trials_excluded_entirely(self):
        dataset = _make_dataset()
        EvaluationTrial.objects.create(
            dataset=dataset, participant_code='P-abort', target_identity_code='T-abort',
            identity_ground_truth=GENUINE, trial_status=EvaluationTrial.TRIAL_STATUS_ABORTED,
            system_decision=None,
        )
        result = ba.get_final_system_performance(dataset)
        self.assertEqual(result['eligible_identity_trial_count'], 0)
        self.assertIsNone(result['tar']['rate'])

    def test_only_manual_review_verified_and_rejected_zero(self):
        dataset = _make_dataset()
        _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=MANUAL_REVIEW)
        result = ba.get_final_system_performance(dataset)
        self.assertEqual(result['tar']['numerator'], 0)
        self.assertEqual(result['frr']['numerator'], 0)
        self.assertEqual(result['gmrr']['rate'], 1.0)

    def test_no_matcher_scores_matcher_performance_empty(self):
        dataset = _make_dataset()
        # Bona-fide/completed trial but never reached comparison (pre-comparison denial).
        _make_trial(
            dataset, identity_ground_truth=GENUINE, system_decision=DENIED,
            matcher_base_decision=None, similarity_score=None,
        )
        result = ba.get_matcher_performance(dataset)
        self.assertEqual(result['eligible_identity_trial_count'], 0)
        self.assertIsNone(result['tar']['rate'])

    def test_null_durations_timing_stats_none(self):
        dataset = _make_dataset()
        _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED, verification_duration_ms=None)
        result = ba.get_timing_analytics(dataset)
        self.assertEqual(result['overall']['count'], 0)
        self.assertIsNone(result['overall']['mean'])
        self.assertIsNone(result['overall']['sample_stddev'])

    def test_one_duration_observation_stddev_none(self):
        dataset = _make_dataset()
        _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED, verification_duration_ms=1500)
        result = ba.get_timing_analytics(dataset)
        self.assertEqual(result['overall']['count'], 1)
        self.assertEqual(result['overall']['mean'], 1500)
        self.assertIsNone(result['overall']['sample_stddev'])

    def test_no_attack_trials_zero_totals_no_crash(self):
        # NOTE: presentation-attack gate vs. end-to-end outcome metrics were
        # corrected and moved to verification/tests_bpa3_1.py (BPA-3.1 §4-9).
        dataset = _make_dataset()
        _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED)
        result = ba.get_end_to_end_attack_outcome(dataset)
        self.assertEqual(result['overall']['total'], 0)
        self.assertIsNone(result['overall']['attack_non_acceptance_rate']['rate'])

    def test_roc_one_class_missing_returns_warning(self):
        dataset = _make_dataset()
        _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED, similarity_score=0.9)
        roc = ba.compute_roc(dataset)
        self.assertEqual(roc['points'], [])
        self.assertTrue(any('impostor' in w for w in roc['warnings']))
        auc = ba.compute_auc(dataset)
        self.assertIsNone(auc['auc'])
        eer = ba.compute_eer(dataset)
        self.assertIsNone(eer['eer'])


# ─────────────────────────────────────────────────────────────────────────
# Dataset isolation — never silently combine studies (BPA-3 §3)
# ─────────────────────────────────────────────────────────────────────────

class DatasetIsolationTest(TestCase):
    def test_other_dataset_trials_never_leak_in(self):
        dataset_a = _make_dataset(name='Dataset A')
        dataset_b = _make_dataset(name='Dataset B')
        _make_trial(dataset_a, identity_ground_truth=GENUINE, system_decision=VERIFIED)
        for _ in range(5):
            _make_trial(dataset_b, identity_ground_truth=IMPOSTOR, system_decision=VERIFIED)

        result_a = ba.get_final_system_performance(dataset_a)
        self.assertEqual(result_a['genuine_trial_count'], 1)
        self.assertEqual(result_a['impostor_trial_count'], 0)

        info_a = ba.get_dataset_info(dataset_a)
        self.assertEqual(info_a['completed_trial_count'], 1)


# ─────────────────────────────────────────────────────────────────────────
# NOT_TESTED presentation truth excluded from primary cohort (BPA-3 §5)
# ─────────────────────────────────────────────────────────────────────────

class PresentationCohortTest(TestCase):
    def test_not_tested_excluded_from_primary_but_included_in_exploratory(self):
        dataset = _make_dataset()
        _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED, presentation_ground_truth=BONA_FIDE)
        _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED, presentation_ground_truth=NOT_TESTED)

        primary = ba.get_final_system_performance(dataset)
        self.assertEqual(primary['genuine_trial_count'], 1)

        exploratory = ba.get_exploratory_identity_performance(dataset)
        self.assertEqual(exploratory['genuine_trial_count'], 2)
        self.assertIn('EXPLORATORY', exploratory['warning'])

    def test_attack_type_trials_excluded_from_primary_cohort(self):
        dataset = _make_dataset()
        _make_trial(dataset, identity_ground_truth=IMPOSTOR, system_decision=VERIFIED, presentation_ground_truth=PRINT_PHOTO)
        result = ba.get_final_system_performance(dataset)
        self.assertEqual(result['impostor_trial_count'], 0)


# ─────────────────────────────────────────────────────────────────────────
# Matcher vs. final decision divergence (BPA-3 §35 — most important test)
# ─────────────────────────────────────────────────────────────────────────

class MatcherVsFinalDecisionTest(TestCase):
    def test_lookalike_escalation_diverges_matcher_from_final(self):
        dataset = _make_dataset()
        # Matcher said VERIFIED, but a lookalike-escalation safety rule forced
        # the FINAL decision to MANUAL_REVIEW.
        _make_trial(
            dataset, identity_ground_truth=GENUINE, system_decision=MANUAL_REVIEW,
            matcher_base_decision=VERIFIED, similarity_score=0.9,
            lookalike_escalation_applied=True,
        )
        # A second, unaffected trial for a non-degenerate denominator.
        _make_trial(
            dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED,
            matcher_base_decision=VERIFIED, similarity_score=0.95,
        )

        matcher = ba.get_matcher_performance(dataset)
        final = ba.get_final_system_performance(dataset)

        # Matcher counted BOTH as verified.
        self.assertEqual(matcher['matrix']['genuine']['verified'], 2)
        self.assertAlmostEqual(matcher['tar']['rate'], 1.0)

        # Final system counted only one as verified; the escalated one is manual review.
        self.assertEqual(final['matrix']['genuine']['verified'], 1)
        self.assertEqual(final['matrix']['genuine']['manual_review'], 1)
        self.assertAlmostEqual(final['tar']['rate'], 0.5)

        self.assertNotEqual(matcher['tar']['rate'], final['tar']['rate'])

    def test_delta_reports_escalation_reason_and_transition(self):
        dataset = _make_dataset()
        _make_trial(
            dataset, identity_ground_truth=GENUINE, system_decision=MANUAL_REVIEW,
            matcher_base_decision=VERIFIED, similarity_score=0.9,
            lookalike_escalation_applied=True,
        )
        delta = ba.get_matcher_to_final_delta(dataset)
        self.assertEqual(delta['transitions'].get(f'{VERIFIED}_to_{MANUAL_REVIEW}'), 1)
        self.assertEqual(delta['escalation_reasons']['lookalike_escalation_applied'], 1)
        self.assertEqual(delta['changed_count'], 1)

    def test_decide_base_outcome_is_the_shared_function(self):
        # Same object-identity guarantee style as BPA-2's parity test.
        self.assertIs(ba.face_utils.decide_base_outcome, face_utils.decide_base_outcome)


# ─────────────────────────────────────────────────────────────────────────
# Beneficiary vs. representative stratification (BPA-3 §36)
# ─────────────────────────────────────────────────────────────────────────

class TargetTypeStratificationTest(TestCase):
    def test_subgroups_are_computed_separately(self):
        beneficiary = _make_beneficiary()
        representative = _make_representative(beneficiary)
        dataset = _make_dataset()
        _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED, target_beneficiary=beneficiary)
        _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=NOT_VERIFIED, target_beneficiary=beneficiary)
        _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED, target_representative=representative)

        result = ba.get_target_type_stratification(dataset)
        self.assertEqual(result['beneficiary']['genuine_trial_count'], 2)
        self.assertAlmostEqual(result['beneficiary']['tar']['rate'], 0.5)
        self.assertEqual(result['representative']['genuine_trial_count'], 1)
        self.assertAlmostEqual(result['representative']['tar']['rate'], 1.0)
        self.assertIn('significance', result['note'])


# ─────────────────────────────────────────────────────────────────────────
# Presentation-attack END-TO-END outcome (BPA-3 §37).
#
# NOTE (BPA-3.1): BPA-3's original get_presentation_attack_metrics() combined
# a bona-fide "pass rate" that was incorrectly restricted to GENUINE identity
# with an attack-outcome breakdown that was actually end-to-end (system_
# decision), not PAD-gate-specific. Both defects were corrected in BPA-3.1
# (see verification/tests_bpa3_1.py) — this class now only covers the
# corrected get_end_to_end_attack_outcome() three-way split.
# ─────────────────────────────────────────────────────────────────────────

class EndToEndAttackOutcomeTest(TestCase):
    def setUp(self):
        self.dataset = _make_dataset()
        _make_trial(self.dataset, identity_ground_truth=IMPOSTOR, system_decision=DENIED, presentation_ground_truth=PRINT_PHOTO)
        _make_trial(self.dataset, identity_ground_truth=IMPOSTOR, system_decision=MANUAL_REVIEW, presentation_ground_truth=PRINT_PHOTO)
        _make_trial(self.dataset, identity_ground_truth=IMPOSTOR, system_decision=VERIFIED, presentation_ground_truth=SCREEN_REPLAY)
        _make_trial(self.dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED, presentation_ground_truth=BONA_FIDE)

    def test_per_attack_type_breakdown_preserved_not_collapsed(self):
        result = ba.get_end_to_end_attack_outcome(self.dataset)
        print_photo = result['per_attack_type'][PRINT_PHOTO]
        self.assertEqual(print_photo['total'], 2)
        self.assertEqual(print_photo['automatically_intercepted'], 1)
        self.assertEqual(print_photo['routed_to_manual_review'], 1)
        self.assertEqual(print_photo['incorrectly_automatically_accepted'], 0)

        screen_replay = result['per_attack_type'][SCREEN_REPLAY]
        self.assertEqual(screen_replay['total'], 1)
        self.assertEqual(screen_replay['incorrectly_automatically_accepted'], 1)

        other_attack = result['per_attack_type'][OTHER_ATTACK]
        self.assertEqual(other_attack['total'], 0)
        self.assertIsNone(other_attack['attack_non_acceptance_rate']['rate'])

    def test_overall_attack_outcome_aggregates_all_types(self):
        result = ba.get_end_to_end_attack_outcome(self.dataset)
        overall = result['overall']
        self.assertEqual(overall['total'], 3)
        self.assertEqual(overall['automatically_intercepted'], 1)
        self.assertEqual(overall['routed_to_manual_review'], 1)
        self.assertEqual(overall['incorrectly_automatically_accepted'], 1)
        self.assertIn('not a PAD detection rate', result['label'])


# ─────────────────────────────────────────────────────────────────────────
# Timing analytics (BPA-3 §38)
# ─────────────────────────────────────────────────────────────────────────

class TimingAnalyticsTest(TestCase):
    def test_exact_statistics_for_1000_2000_3000(self):
        dataset = _make_dataset()
        for ms in (1000, 2000, 3000):
            _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED, verification_duration_ms=ms)

        result = ba.get_timing_analytics(dataset)
        overall = result['overall']
        self.assertEqual(overall['count'], 3)
        self.assertEqual(overall['mean'], 2000.0)
        self.assertEqual(overall['median'], 2000.0)
        self.assertEqual(overall['min'], 1000)
        self.assertEqual(overall['max'], 3000)
        self.assertAlmostEqual(overall['sample_stddev'], 1000.0)
        self.assertEqual(result['metric_name'], 'Controlled Verification Elapsed Time')

    def test_grouping_by_system_decision(self):
        dataset = _make_dataset()
        _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED, verification_duration_ms=1000)
        _make_trial(dataset, identity_ground_truth=IMPOSTOR, system_decision=NOT_VERIFIED, verification_duration_ms=5000)

        result = ba.get_timing_analytics(dataset, group_by='system_decision')
        self.assertEqual(result['groups'][VERIFIED]['count'], 1)
        self.assertEqual(result['groups'][VERIFIED]['mean'], 1000)
        self.assertEqual(result['groups'][NOT_VERIFIED]['mean'], 5000)
        self.assertEqual(result['groups'][MANUAL_REVIEW]['count'], 0)


# ─────────────────────────────────────────────────────────────────────────
# Score distributions
# ─────────────────────────────────────────────────────────────────────────

class ScoreDistributionTest(TestCase):
    def test_genuine_and_impostor_distributions_separated(self):
        dataset = _make_dataset()
        for score in (0.9, 0.95):
            _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED, similarity_score=score)
        for score in (0.1, 0.2):
            _make_trial(dataset, identity_ground_truth=IMPOSTOR, system_decision=NOT_VERIFIED, similarity_score=score)

        result = ba.get_score_distributions(dataset)
        self.assertEqual(result['genuine']['count'], 2)
        self.assertAlmostEqual(result['genuine']['mean'], 0.925)
        self.assertEqual(result['impostor']['count'], 2)
        self.assertAlmostEqual(result['impostor']['mean'], 0.15)
        self.assertIsNotNone(result['genuine']['histogram'])

    def test_empty_distribution_no_crash(self):
        dataset = _make_dataset()
        result = ba.get_score_distributions(dataset)
        self.assertEqual(result['genuine']['count'], 0)
        self.assertIsNone(result['genuine']['histogram'])


# ─────────────────────────────────────────────────────────────────────────
# Threshold sweep (BPA-3 §22-23) — reuses decide_base_outcome
# ─────────────────────────────────────────────────────────────────────────

class ThresholdSweepTest(TestCase):
    def test_sweep_matches_direct_decide_base_outcome_calls(self):
        dataset = _make_dataset()
        scores = [0.95, 0.80, 0.70, 0.50]
        for i, score in enumerate(scores):
            _make_trial(
                dataset, identity_ground_truth=GENUINE if i % 2 == 0 else IMPOSTOR,
                system_decision=VERIFIED, similarity_score=score,
            )

        candidate = (0.75, 0.88)
        sweep = ba.run_threshold_sweep(dataset, [candidate])
        row = sweep['results'][0]

        expected_counts = {'genuine_verified': 0, 'genuine_manual_review': 0, 'genuine_rejected': 0,
                            'impostor_verified': 0, 'impostor_manual_review': 0, 'impostor_rejected': 0}
        for i, score in enumerate(scores):
            identity = GENUINE if i % 2 == 0 else IMPOSTOR
            decision = face_utils.decide_base_outcome(score, *candidate)
            prefix = 'genuine' if identity == GENUINE else 'impostor'
            bucket = {VERIFIED: 'verified', MANUAL_REVIEW: 'manual_review', NOT_VERIFIED: 'rejected'}[decision]
            expected_counts[f'{prefix}_{bucket}'] += 1

        self.assertEqual(row['counts'], expected_counts)

    def test_sweep_returns_one_row_per_candidate_policy(self):
        dataset = _make_dataset()
        _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED, similarity_score=0.9)
        sweep = ba.run_threshold_sweep(dataset, [(0.75, 0.88), (0.6, 0.95)])
        self.assertEqual(len(sweep['results']), 2)
        self.assertIn('COUNTERFACTUAL', sweep['limitation'])


# ─────────────────────────────────────────────────────────────────────────
# ROC / AUC / EER (BPA-3 §39)
# ─────────────────────────────────────────────────────────────────────────

class RocAucEerTest(TestCase):
    def _make_scores(self, dataset, genuine_scores, impostor_scores):
        for s in genuine_scores:
            _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED, similarity_score=s)
        for s in impostor_scores:
            _make_trial(dataset, identity_ground_truth=IMPOSTOR, system_decision=NOT_VERIFIED, similarity_score=s)

    def test_perfect_separation_auc_is_one_eer_is_zero(self):
        dataset = _make_dataset()
        self._make_scores(dataset, [0.9, 0.95], [0.1, 0.2])

        auc = ba.compute_auc(dataset)
        self.assertAlmostEqual(auc['auc'], 1.0, places=6)

        eer = ba.compute_eer(dataset)
        self.assertAlmostEqual(eer['eer'], 0.0, delta=1e-6)

    def test_overlapping_scores_eer_near_half(self):
        dataset = _make_dataset()
        self._make_scores(dataset, [0.5, 0.7], [0.4, 0.6])
        eer = ba.compute_eer(dataset)
        self.assertAlmostEqual(eer['eer'], 0.5, delta=0.01)
        self.assertAlmostEqual(eer['threshold'], 0.6, delta=0.01)

    def test_tied_scores_eer_near_half_chance_level(self):
        dataset = _make_dataset()
        self._make_scores(dataset, [0.6, 0.6], [0.6, 0.6])
        eer = ba.compute_eer(dataset)
        self.assertAlmostEqual(eer['eer'], 0.5, delta=0.01)

    def test_one_class_missing_all_none_with_warnings(self):
        dataset = _make_dataset()
        self._make_scores(dataset, [0.9, 0.95], [])
        roc = ba.compute_roc(dataset)
        self.assertEqual(roc['points'], [])
        self.assertEqual(ba.compute_auc(dataset)['auc'], None)
        self.assertEqual(ba.compute_eer(dataset)['eer'], None)

    def test_roc_curve_endpoints(self):
        dataset = _make_dataset()
        self._make_scores(dataset, [0.9, 0.95], [0.1, 0.2])
        roc = ba.compute_roc(dataset)
        first, last = roc['points'][0], roc['points'][-1]
        self.assertAlmostEqual(first['tpr'], 0.0)
        self.assertAlmostEqual(first['fpr'], 0.0)
        self.assertAlmostEqual(last['tpr'], 1.0)
        self.assertAlmostEqual(last['fpr'], 1.0)

    def test_deterministic_repeatable(self):
        dataset = _make_dataset()
        self._make_scores(dataset, [0.9, 0.6, 0.75], [0.3, 0.55, 0.5])
        auc1 = ba.compute_auc(dataset)['auc']
        auc2 = ba.compute_auc(dataset)['auc']
        self.assertEqual(auc1, auc2)


# ─────────────────────────────────────────────────────────────────────────
# Claim Recording Reliability — not fabricated (BPA-3 §28)
# ─────────────────────────────────────────────────────────────────────────

class ClaimRecordingReliabilityTest(TestCase):
    def test_reports_not_computable_with_reason_and_recommendation(self):
        result = ba.get_claim_recording_reliability()
        self.assertFalse(result['computable'])
        self.assertTrue(result['reason'])
        self.assertTrue(result['recommendation'])


# ─────────────────────────────────────────────────────────────────────────
# Finalized vs. interim dataset (BPA-3 §30)
# ─────────────────────────────────────────────────────────────────────────

class FinalizationNoticeTest(TestCase):
    def test_collecting_dataset_is_interim_with_warning(self):
        dataset = _make_dataset(status=EvaluationDataset.STATUS_COLLECTING)
        notice = ba._finalization_notice(dataset)
        self.assertFalse(notice['is_finalized_dataset'])
        self.assertIn('NOT FINALIZED', notice['warning'])

    def test_completed_dataset_is_finalized_no_warning(self):
        dataset = _make_dataset(status=EvaluationDataset.STATUS_COMPLETED)
        notice = ba._finalization_notice(dataset)
        self.assertTrue(notice['is_finalized_dataset'])
        self.assertIsNone(notice['warning'])

    def test_report_carries_finalization_flag(self):
        dataset = _make_dataset(status=EvaluationDataset.STATUS_COLLECTING)
        report = ba.build_metric_report(dataset)
        self.assertFalse(report['is_finalized_dataset'])
        self.assertIsNotNone(report['warning'])


# ─────────────────────────────────────────────────────────────────────────
# Liveness pathway breakdown — attested, not machine-verified (BPA-3 §16)
# ─────────────────────────────────────────────────────────────────────────

class LivenessPathwayBreakdownTest(TestCase):
    def test_breakdown_grouped_by_pathway_and_limitation_present(self):
        dataset = _make_dataset()
        _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED,
                    liveness_pathway=EvaluationTrial.LIVENESS_PATHWAY_ACTIVE_CHALLENGE)
        _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED,
                    liveness_pathway=EvaluationTrial.LIVENESS_PATHWAY_PASSIVE_ONLY)

        result = ba.get_liveness_pathway_breakdown(dataset)
        self.assertEqual(result['breakdown'][EvaluationTrial.LIVENESS_PATHWAY_ACTIVE_CHALLENGE]['total'], 1)
        self.assertEqual(result['breakdown'][EvaluationTrial.LIVENESS_PATHWAY_PASSIVE_ONLY]['total'], 1)
        self.assertIn('RESEARCHER-ATTESTED', result['active_challenge_attestation_limitation'])
        self.assertIn('Machine-Verified Active Liveness Accuracy', result['active_challenge_attestation_limitation'])


# ─────────────────────────────────────────────────────────────────────────
# Participant vs. trial counts (BPA-3 §14)
# ─────────────────────────────────────────────────────────────────────────

class ParticipantTrialCountsTest(TestCase):
    def test_repeated_participant_counted_once(self):
        dataset = _make_dataset()
        _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED, participant_code='SAME')
        _make_trial(dataset, identity_ground_truth=IMPOSTOR, system_decision=NOT_VERIFIED, participant_code='SAME')
        _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED, participant_code='OTHER')

        result = ba.get_participant_and_trial_counts(dataset)
        self.assertEqual(result['unique_participant_count'], 2)
        self.assertEqual(result['total_trial_count'], 3)
        self.assertEqual(result['completed_trial_count'], 3)

    def test_attack_trial_count(self):
        dataset = _make_dataset()
        _make_trial(dataset, identity_ground_truth=IMPOSTOR, system_decision=DENIED, presentation_ground_truth=PRINT_PHOTO)
        _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED, presentation_ground_truth=BONA_FIDE)
        result = ba.get_participant_and_trial_counts(dataset)
        self.assertEqual(result['attack_trial_count'], 1)


# ─────────────────────────────────────────────────────────────────────────
# Query-count stability — no per-trial N+1 (BPA-3 §40)
# ─────────────────────────────────────────────────────────────────────────

class QueryCountPerformanceTest(TestCase):
    def _build_dataset(self, n):
        dataset = _make_dataset()
        for i in range(n):
            identity = GENUINE if i % 2 == 0 else IMPOSTOR
            decision = [VERIFIED, MANUAL_REVIEW, NOT_VERIFIED][i % 3]
            _make_trial(
                dataset, identity_ground_truth=identity, system_decision=decision,
                similarity_score=0.5 + (i % 10) / 20.0, verification_duration_ms=1000 + i,
            )
        return dataset

    def test_query_count_does_not_scale_with_trial_count(self):
        small_dataset = self._build_dataset(5)
        large_dataset = self._build_dataset(50)

        with CaptureQueriesContext(connection) as small_ctx:
            ba.build_metric_report(small_dataset)
        with CaptureQueriesContext(connection) as large_ctx:
            ba.build_metric_report(large_dataset)

        self.assertEqual(len(small_ctx.captured_queries), len(large_ctx.captured_queries))
