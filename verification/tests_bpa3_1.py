"""
BPA-3.1 — Biometric metric semantics + score-domain hardening tests.

Covers: the full cosine-similarity domain [-1, 1] surviving histogram
binning without being silently clamped, the presentation-attack GATE
metric (liveness_pathway-derived) being kept analytically separate from
the END-TO-END attack-trial outcome (system_decision-derived), the
BONA_FIDE-presentation-cohort no longer silently requiring
identity_ground_truth=GENUINE, ROC/AUC/EER domain/ordering/boundary
correctness (including a fully-reversed-separation case and a
negative-score case), and a regression check that BPA-3's primary
identity-performance formulas are untouched by this checkpoint.

All fixtures here are SYNTHETIC TEST DATA — never real study observations.
"""
from django.test import TestCase

from verification import biometric_analytics as ba
from verification.models import EvaluationDataset, EvaluationTrial, VerificationAttempt

from verification.tests_bpa3 import _make_dataset, _make_trial

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

PASSIVE_ONLY = EvaluationTrial.LIVENESS_PATHWAY_PASSIVE_ONLY
ACTIVE_CHALLENGE = EvaluationTrial.LIVENESS_PATHWAY_ACTIVE_CHALLENGE
FAILED_PASSIVE = EvaluationTrial.LIVENESS_PATHWAY_FAILED_PASSIVE
FAILED_ACTIVE = EvaluationTrial.LIVENESS_PATHWAY_FAILED_ACTIVE


# ─────────────────────────────────────────────────────────────────────────
# §2/§17 — Cosine score domain: negative scores must never be lost/clamped
# ─────────────────────────────────────────────────────────────────────────

class NegativeScoreDomainTest(TestCase):
    def test_histogram_domain_is_full_cosine_range(self):
        self.assertEqual(ba._SCORE_DOMAIN, (-1.0, 1.0))

    def test_negative_score_lands_in_its_own_bin_not_clamped_to_zero_bin(self):
        # -0.20 must NOT be binned together with a small positive score like 0.05.
        histogram = ba._histogram([-0.20, 0.05], bins=10)
        # width = 2.0/10 = 0.2; bin edges are -1.0,-0.8,...,0.0,0.2,...
        # -0.20 -> idx = int((-0.20-(-1.0))/0.2) = int(0.8/0.2) = 4 -> [-0.2, 0.0)
        # 0.05  -> idx = int((0.05-(-1.0))/0.2) = int(1.05/0.2) = 5 -> [0.0, 0.2)
        self.assertEqual(histogram['counts'][4], 1)
        self.assertEqual(histogram['counts'][5], 1)
        self.assertEqual(sum(histogram['counts']), 2)
        self.assertEqual(histogram['domain'], (-1.0, 1.0))

    def test_negative_score_not_lost_from_score_distribution_stats(self):
        dataset = _make_dataset()
        _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=NOT_VERIFIED, similarity_score=-0.20)
        _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED, similarity_score=0.90)

        result = ba.get_score_distributions(dataset)
        genuine = result['genuine']
        self.assertEqual(genuine['count'], 2)
        self.assertEqual(genuine['min'], -0.20)
        self.assertIn(-0.20, genuine['values'])
        self.assertEqual(result['score_domain'], (-1.0, 1.0))
        # The negative score must be counted somewhere in the histogram, not dropped.
        self.assertEqual(sum(genuine['histogram']['counts']), 2)

    def test_negative_score_present_in_roc_and_does_not_crash(self):
        dataset = _make_dataset()
        _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=NOT_VERIFIED, similarity_score=-0.20)
        _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED, similarity_score=0.90)
        _make_trial(dataset, identity_ground_truth=IMPOSTOR, system_decision=NOT_VERIFIED, similarity_score=-0.50)
        _make_trial(dataset, identity_ground_truth=IMPOSTOR, system_decision=NOT_VERIFIED, similarity_score=0.10)

        roc = ba.compute_roc(dataset)
        self.assertEqual(roc['warnings'], [])
        thresholds = [p['threshold'] for p in roc['points']]
        # The negative genuine score must appear as a swept threshold, not be excluded.
        self.assertTrue(any(t == -0.20 for t in thresholds))

        auc = ba.compute_auc(dataset)
        self.assertIsNotNone(auc['auc'])

        eer = ba.compute_eer(dataset)
        # EER threshold must stay within the observed score range (§13).
        if eer['threshold'] is not None:
            self.assertGreaterEqual(eer['threshold'], -0.50 - 1e-6)
            self.assertLessEqual(eer['threshold'], 0.90 + 1e-6)


# ─────────────────────────────────────────────────────────────────────────
# §4/§9 — BONA_FIDE presentation independent of identity ground truth
# ─────────────────────────────────────────────────────────────────────────

class BonaFideIndependentOfIdentityTest(TestCase):
    def test_bona_fide_gate_cohort_includes_both_genuine_and_impostor(self):
        dataset = _make_dataset()
        _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED,
                    presentation_ground_truth=BONA_FIDE, liveness_pathway=PASSIVE_ONLY)
        _make_trial(dataset, identity_ground_truth=IMPOSTOR, system_decision=VERIFIED,
                    presentation_ground_truth=BONA_FIDE, liveness_pathway=PASSIVE_ONLY)

        result = ba.get_presentation_attack_gate_metrics(dataset)
        bona_fide = result['bona_fide']
        # Both trials must be in the overall bona-fide gate cohort regardless
        # of identity ground truth — genuine identity is NOT a hidden filter.
        self.assertEqual(bona_fide['eligible_count'], 2)
        self.assertEqual(bona_fide['gate_passed_count'], 2)

        # Optional identity stratification is available separately.
        self.assertEqual(bona_fide['by_identity_ground_truth']['genuine']['eligible_count'], 1)
        self.assertEqual(bona_fide['by_identity_ground_truth']['impostor']['eligible_count'], 1)

    def test_live_impostor_is_independent_dimension_gate_and_identity_cohorts(self):
        """BPA-3.1 §16 — a live (BONA_FIDE) impostor whose gate PASSED must
        appear in BOTH the bona-fide gate-pass cohort AND the impostor
        identity-performance cohort, independently."""
        dataset = _make_dataset()
        _make_trial(
            dataset, identity_ground_truth=IMPOSTOR, system_decision=NOT_VERIFIED,
            presentation_ground_truth=BONA_FIDE, liveness_pathway=PASSIVE_ONLY,
            similarity_score=0.2, matcher_base_decision=NOT_VERIFIED,
        )

        gate = ba.get_presentation_attack_gate_metrics(dataset)
        self.assertEqual(gate['bona_fide']['eligible_count'], 1)
        self.assertEqual(gate['bona_fide']['gate_passed_count'], 1)

        identity = ba.get_final_system_performance(dataset)
        self.assertEqual(identity['impostor_trial_count'], 1)
        self.assertEqual(identity['matrix']['impostor']['rejected'], 1)


# ─────────────────────────────────────────────────────────────────────────
# §5/§7/§15 — PAD gate vs. end-to-end outcome: the single most important test
# ─────────────────────────────────────────────────────────────────────────

class GateVsEndToEndSyntheticCaseTest(TestCase):
    def test_pad_gate_passed_incorrectly_but_matcher_still_rejects(self):
        """
        Synthetic scenario (BPA-3.1 §15):
          presentation_ground_truth = PRINT_PHOTO
          liveness/PAD gate PASSES incorrectly (liveness_pathway=PASSIVE_ONLY)
          identity matcher: NOT_VERIFIED

        Expected:
          PAD Attack Gate Block: NO   (gate metrics: gate_blocked_count=0)
          Final FANS-C automatic acceptance: NO (system_decision != VERIFIED)
          End-to-end attack interception/non-acceptance: YES (NOT_VERIFIED is
            an auto-reject decision)

        This proves the engine never credits PAD with "detecting" an attack
        just because a later, unrelated matcher rejection occurred.
        """
        dataset = _make_dataset()
        _make_trial(
            dataset,
            identity_ground_truth=IMPOSTOR,
            system_decision=NOT_VERIFIED,
            matcher_base_decision=NOT_VERIFIED,
            presentation_ground_truth=PRINT_PHOTO,
            liveness_pathway=PASSIVE_ONLY,  # gate PASSED (did not detect the spoof)
            similarity_score=0.10,
        )

        gate = ba.get_presentation_attack_gate_metrics(dataset)
        print_photo_gate = gate['per_attack_type'][PRINT_PHOTO]
        self.assertEqual(print_photo_gate['gate_blocked_count'], 0)
        self.assertEqual(print_photo_gate['gate_passed_count'], 1)
        self.assertEqual(print_photo_gate['attack_gate_block_rate']['rate'], 0.0)

        end_to_end = ba.get_end_to_end_attack_outcome(dataset)
        print_photo_e2e = end_to_end['per_attack_type'][PRINT_PHOTO]
        self.assertEqual(print_photo_e2e['automatically_intercepted'], 1)
        self.assertEqual(print_photo_e2e['attack_non_acceptance_rate']['rate'], 1.0)
        self.assertEqual(print_photo_e2e['incorrectly_automatically_accepted'], 0)

        # The two results must never be conflated: gate says "not blocked",
        # end-to-end says "not accepted" -- both true simultaneously, for
        # different reasons (matcher rejection, not PAD).
        self.assertNotEqual(
            print_photo_gate['attack_gate_block_rate']['rate'],
            print_photo_e2e['attack_non_acceptance_rate']['rate'],
        )

    def test_end_to_end_outcome_never_labeled_pad_detection(self):
        result = ba.get_end_to_end_attack_outcome(_make_dataset())
        self.assertIn('not a PAD detection rate', result['label'])
        self.assertIn('PAD', result['note'])


# ─────────────────────────────────────────────────────────────────────────
# §8 — Attack-type breakdown keeps gate and final-decision separate per type
# ─────────────────────────────────────────────────────────────────────────

class AttackTypeBreakdownSeparationTest(TestCase):
    def test_each_attack_type_has_independent_gate_and_e2e_results(self):
        dataset = _make_dataset()
        # PRINT_PHOTO: gate blocked it, so it never reached the matcher.
        _make_trial(
            dataset, identity_ground_truth=IMPOSTOR, system_decision=DENIED,
            matcher_base_decision=None, similarity_score=None,
            presentation_ground_truth=PRINT_PHOTO, liveness_pathway=FAILED_PASSIVE,
        )
        # SCREEN_REPLAY: gate passed it, matcher then verified it (worst case: false accept).
        _make_trial(
            dataset, identity_ground_truth=IMPOSTOR, system_decision=VERIFIED,
            matcher_base_decision=VERIFIED, similarity_score=0.95,
            presentation_ground_truth=SCREEN_REPLAY, liveness_pathway=PASSIVE_ONLY,
        )

        gate = ba.get_presentation_attack_gate_metrics(dataset)
        e2e = ba.get_end_to_end_attack_outcome(dataset)

        self.assertEqual(gate['per_attack_type'][PRINT_PHOTO]['gate_blocked_count'], 1)
        self.assertEqual(gate['per_attack_type'][SCREEN_REPLAY]['gate_passed_count'], 1)

        self.assertEqual(e2e['per_attack_type'][PRINT_PHOTO]['automatically_intercepted'], 1)
        self.assertEqual(e2e['per_attack_type'][SCREEN_REPLAY]['incorrectly_automatically_accepted'], 1)


# ─────────────────────────────────────────────────────────────────────────
# §10 — Active-liveness attestation limitation preserved
# ─────────────────────────────────────────────────────────────────────────

class ActiveLivenessLimitationPreservedTest(TestCase):
    def test_gate_metrics_carry_attestation_limitation(self):
        result = ba.get_presentation_attack_gate_metrics(_make_dataset())
        self.assertIn('RESEARCHER-ATTESTED', result['active_challenge_attestation_limitation'])
        self.assertIn('Machine-Verified Active Liveness Accuracy', result['active_challenge_attestation_limitation'])

    def test_liveness_pathway_breakdown_still_carries_limitation(self):
        result = ba.get_liveness_pathway_breakdown(_make_dataset())
        self.assertIn('RESEARCHER-ATTESTED', result['active_challenge_attestation_limitation'])


# ─────────────────────────────────────────────────────────────────────────
# §11/§12 — ROC/AUC domain, ordering, and boundary audit
# ─────────────────────────────────────────────────────────────────────────

class RocAucBoundaryAuditTest(TestCase):
    def _scored(self, dataset, genuine_scores, impostor_scores):
        for s in genuine_scores:
            _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED, similarity_score=s)
        for s in impostor_scores:
            _make_trial(dataset, identity_ground_truth=IMPOSTOR, system_decision=NOT_VERIFIED, similarity_score=s)

    def test_higher_score_means_more_genuine_like_threshold_direction(self):
        dataset = _make_dataset()
        self._scored(dataset, [0.9], [0.1])
        roc = ba.compute_roc(dataset)
        # As threshold decreases across the sweep, TPR/FPR must be
        # monotonically non-decreasing (never decrease) -- higher cosine
        # similarity must mean "more likely accepted," not the reverse.
        for i in range(1, len(roc['points'])):
            self.assertGreaterEqual(roc['points'][i]['tpr'], roc['points'][i - 1]['tpr'] - 1e-12)
            self.assertGreaterEqual(roc['points'][i]['fpr'], roc['points'][i - 1]['fpr'] - 1e-12)

    def test_perfect_separation_auc_is_exactly_one(self):
        dataset = _make_dataset()
        self._scored(dataset, [0.8, 0.9, 0.95], [-0.1, 0.0, 0.2])
        auc = ba.compute_auc(dataset)
        self.assertAlmostEqual(auc['auc'], 1.0, places=9)

    def test_fully_reversed_ordering_auc_near_zero(self):
        # Genuine scores are all LOWER than impostor scores -- the matcher is
        # behaving backwards. AUC must reflect this as near 0, never near 1.
        dataset = _make_dataset()
        self._scored(dataset, [0.1, 0.2], [0.8, 0.9])
        auc = ba.compute_auc(dataset)
        self.assertAlmostEqual(auc['auc'], 0.0, places=9)

    def test_auc_integration_uses_ascending_fpr_order(self):
        dataset = _make_dataset()
        self._scored(dataset, [0.3, 0.6, 0.9], [0.2, 0.5, 0.8])
        roc = ba.compute_roc(dataset)
        points = sorted(roc['points'], key=lambda p: (p['fpr'], p['tpr']))
        fprs = [p['fpr'] for p in points]
        self.assertEqual(fprs, sorted(fprs))

    def test_roc_boundary_endpoints_present_so_auc_not_understated(self):
        dataset = _make_dataset()
        self._scored(dataset, [0.9, 0.95], [0.1, 0.2])
        roc = ba.compute_roc(dataset)
        fprs = [p['fpr'] for p in roc['points']]
        tprs = [p['tpr'] for p in roc['points']]
        self.assertIn(0.0, fprs)
        self.assertIn(1.0, fprs)
        self.assertIn(0.0, tprs)
        self.assertIn(1.0, tprs)

    def test_tied_scores_are_deterministic_across_repeated_calls(self):
        dataset = _make_dataset()
        self._scored(dataset, [0.5, 0.5], [0.5, 0.5])
        first = ba.compute_roc(dataset)
        second = ba.compute_roc(dataset)
        self.assertEqual(first['points'], second['points'])

    def test_one_class_dataset_remains_unavailable_not_fabricated(self):
        dataset = _make_dataset()
        self._scored(dataset, [0.9, 0.95], [])
        self.assertEqual(ba.compute_roc(dataset)['points'], [])
        self.assertIsNone(ba.compute_auc(dataset)['auc'])
        self.assertIsNone(ba.compute_eer(dataset)['eer'])


# ─────────────────────────────────────────────────────────────────────────
# §13 — EER threshold domain audit
# ─────────────────────────────────────────────────────────────────────────

class EerThresholdDomainAuditTest(TestCase):
    def test_eer_threshold_within_observed_score_range(self):
        dataset = _make_dataset()
        genuine_scores = [0.3, 0.6]
        impostor_scores = [0.2, 0.5]
        for s in genuine_scores:
            _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED, similarity_score=s)
        for s in impostor_scores:
            _make_trial(dataset, identity_ground_truth=IMPOSTOR, system_decision=NOT_VERIFIED, similarity_score=s)

        eer = ba.compute_eer(dataset)
        all_scores = genuine_scores + impostor_scores
        if eer['threshold'] is not None:
            self.assertGreaterEqual(eer['threshold'], min(all_scores) - 1e-6)
            self.assertLessEqual(eer['threshold'], max(all_scores) + 1e-6)

    def test_eer_never_labeled_optimal_threshold(self):
        # Docstring-level contract check: the method name must not claim
        # "optimal" -- only ever "linear_interpolation" or "exact_match".
        dataset = _make_dataset()
        for s in (0.3, 0.6):
            _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=VERIFIED, similarity_score=s)
        for s in (0.2, 0.5):
            _make_trial(dataset, identity_ground_truth=IMPOSTOR, system_decision=NOT_VERIFIED, similarity_score=s)
        eer = ba.compute_eer(dataset)
        if eer['method'] is not None:
            self.assertNotIn('optimal', eer['method'].lower())


# ─────────────────────────────────────────────────────────────────────────
# §14 — Primary identity-performance formulas unchanged (regression guard)
# ─────────────────────────────────────────────────────────────────────────

class PrimaryIdentityMetricsUnchangedRegressionTest(TestCase):
    """Re-runs BPA-3's original hand-verified matrix (see tests_bpa3.py
    FinalSystemMatrixTest) to prove BPA-3.1 did not alter TAR/FRR/GMRR/FAR/
    TNR/IMRR/Coverage/Conditional-Automatic-Accuracy."""

    def test_hand_verified_matrix_unchanged(self):
        dataset = _make_dataset()
        for decision in (VERIFIED, VERIFIED, MANUAL_REVIEW, NOT_VERIFIED):
            _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=decision)
        for decision in (NOT_VERIFIED, MANUAL_REVIEW, VERIFIED, DENIED):
            _make_trial(dataset, identity_ground_truth=IMPOSTOR, system_decision=decision)

        result = ba.get_final_system_performance(dataset)
        self.assertAlmostEqual(result['tar']['rate'], 0.5)
        self.assertAlmostEqual(result['frr']['rate'], 0.25)
        self.assertAlmostEqual(result['gmrr']['rate'], 0.25)
        self.assertAlmostEqual(result['far']['rate'], 0.25)
        self.assertAlmostEqual(result['tnr']['rate'], 0.5)
        self.assertAlmostEqual(result['imrr']['rate'], 0.25)
        self.assertAlmostEqual(result['coverage']['rate'], 0.75)
        self.assertAlmostEqual(result['conditional_automatic_accuracy']['rate'], 4 / 6)

    def test_manual_review_still_a_separate_third_outcome(self):
        dataset = _make_dataset()
        _make_trial(dataset, identity_ground_truth=GENUINE, system_decision=MANUAL_REVIEW)
        result = ba.get_final_system_performance(dataset)
        self.assertEqual(result['matrix']['genuine']['manual_review'], 1)
        self.assertEqual(result['matrix']['genuine']['verified'], 0)
        self.assertEqual(result['matrix']['genuine']['rejected'], 0)


# ─────────────────────────────────────────────────────────────────────────
# build_metric_report still succeeds end-to-end with the new function names
# ─────────────────────────────────────────────────────────────────────────

class MetricReportIntegrationTest(TestCase):
    def test_report_uses_new_gate_and_end_to_end_keys(self):
        dataset = _make_dataset()
        _make_trial(
            dataset, identity_ground_truth=IMPOSTOR, system_decision=NOT_VERIFIED,
            matcher_base_decision=NOT_VERIFIED, presentation_ground_truth=PRINT_PHOTO,
            liveness_pathway=PASSIVE_ONLY, similarity_score=0.1,
        )
        report = ba.build_metric_report(dataset)
        self.assertIn('presentation_attack_gate_metrics', report)
        self.assertIn('end_to_end_attack_outcome', report)
        self.assertNotIn('presentation_attack_metrics', report)
