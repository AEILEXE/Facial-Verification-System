"""
BPA-3 — Biometric Performance Metric Engine (BPA-3.1 semantics hardening).

Read-only calculation layer for CONTROLLED `EvaluationTrial` data collected
via the BPA-2/BPA-2.1 workflow. This module computes NOTHING from live
operational tables (`VerificationAttempt`, `ClaimRecord`, `analytics.py`) —
see docs/BIOMETRIC-EVALUATION-METHODOLOGY.md §10/§20 for why that isolation
matters. It is pure calculation: no writes, no view/template code, no
Analytics UI wiring (deferred to BPA-4).

Every function takes an already-selected `EvaluationDataset` — this module
never silently combines trials from multiple studies. See
docs/BIOMETRIC-EVALUATION-METHODOLOGY.md, "BPA-3"/"BPA-3.1" sections, for the
exact mathematical definition (DATA / CALCULATION / RESULT / MEANING /
LIMITATION) of every metric below.

Rate convention: every rate is returned as a dict
`{'numerator': int, 'denominator': int, 'rate': float | None}` — `rate` is
fraction (0.0-1.0), never a pre-multiplied percentage, and is `None` (never
`0.0`) when `denominator == 0` so "no eligible data" is never confused with
a measured zero. Callers/UI multiply by 100 for display.

Score domain: `similarity_score` is a raw cosine similarity
(`face_utils.cosine_similarity`, never clamped) whose true mathematical
domain is `[-1.0, 1.0]` (`_SCORE_DOMAIN`), not `[0, 1]` — every distribution/
histogram function spans the full domain so a legally negative score is
never silently misclassified (BPA-3.1 §2).

Presentation-attack semantics (BPA-3.1 §4-9): `presentation_ground_truth`
(BONA_FIDE vs. an attack type) is independent of `identity_ground_truth`
(GENUINE vs. IMPOSTOR) — a live impostor is still a BONA_FIDE presentation.
Gate performance (`get_presentation_attack_gate_metrics`, derived from
`liveness_pathway`) and end-to-end decision outcome
(`get_end_to_end_attack_outcome`, derived from `system_decision`) are kept
as two analytically separate results — never merged, since an attack trial
can be intercepted by the matcher or a safety rule even when the liveness/
PAD gate itself was fooled.

No real study performance has ever been computed or reported by this
module — it operates only on whatever `EvaluationTrial` rows exist in the
database, and callers are responsible for treating dev/test/synthetic rows
as such (see BPA-3 checkpoint instructions §31).
"""
import math
from datetime import timedelta

from django.db.models import Count, F, Q
from django.utils import timezone

from . import face_utils
from .models import EvaluationDataset, EvaluationTrial, VerificationAttempt

# ── Stale pending-with-proof retention window (BPA-5.1) ────────────────────
# A trial that completed stage-1 liveness (an encrypted proof embedding is
# stored) but whose browser tab was then closed before stage 2 stays PENDING
# indefinitely — see docs/BIOMETRIC-EVALUATION-METHODOLOGY.md "Abandoned
# pending trials". No background/scheduled cleanup exists (deliberately out
# of scope for this checkpoint); this constant only drives a passive
# dashboard warning (get_stale_pending_trials_with_proof) that prompts a
# researcher/admin to explicitly abort the trial, which clears the proof via
# _abort_trial. 24h comfortably exceeds any real single-sitting trial run.
STALE_PENDING_PROOF_HOURS = 24

# ─── Decision / ground-truth vocabulary (local aliases for readability) ────

_GENUINE = EvaluationTrial.GROUND_TRUTH_GENUINE
_IMPOSTOR = EvaluationTrial.GROUND_TRUTH_IMPOSTOR

_VERIFIED = VerificationAttempt.DECISION_VERIFIED
_MANUAL_REVIEW = VerificationAttempt.DECISION_MANUAL_REVIEW
_NOT_VERIFIED = VerificationAttempt.DECISION_NOT_VERIFIED
_DENIED = VerificationAttempt.DECISION_DENIED

# "Reject/Denied" cell of the primary 2x3 matrix (BPA-3 §6). MANUAL_REVIEW is
# deliberately never included here.
_AUTO_REJECT_DECISIONS = [_NOT_VERIFIED, _DENIED]

_ATTACK_TYPES = [
    EvaluationTrial.PRESENTATION_PRINT_PHOTO,
    EvaluationTrial.PRESENTATION_SCREEN_REPLAY,
    EvaluationTrial.PRESENTATION_OTHER_ATTACK,
]


# ─── Small numeric helpers ──────────────────────────────────────────────────

def _rate(numerator, denominator):
    """A rate as a fraction (0.0-1.0), or `rate=None` when the denominator is
    zero — an unavailable rate must never render as a measured 0%."""
    if denominator == 0:
        return {'numerator': numerator, 'denominator': 0, 'rate': None}
    return {'numerator': numerator, 'denominator': denominator, 'rate': numerator / denominator}


def _median(sorted_values):
    """Median of an already-ascending-sorted sequence."""
    n = len(sorted_values)
    mid = n // 2
    if n % 2 == 1:
        return float(sorted_values[mid])
    return (sorted_values[mid - 1] + sorted_values[mid]) / 2.0


def _sample_stddev(values, mean):
    """Sample standard deviation (n-1 denominator), per BPA-3 §19. Returns
    None when n < 2 — a single observation has no meaningful sample spread."""
    n = len(values)
    if n < 2:
        return None
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    return math.sqrt(variance)


def _numeric_stats(values):
    """count/mean/median/min/max/sample_stddev for a list of numbers. Values
    must already be sorted ascending (callers order_by the field)."""
    n = len(values)
    if n == 0:
        return {'count': 0, 'mean': None, 'median': None, 'min': None, 'max': None, 'sample_stddev': None}
    mean = sum(values) / n
    return {
        'count': n,
        'mean': mean,
        'median': _median(values),
        'min': values[0],
        'max': values[-1],
        'sample_stddev': _sample_stddev(values, mean),
    }


# ─── Dataset-level eligibility (BPA-3 §3) ──────────────────────────────────

def _active_trials(dataset):
    """This dataset's trials EXCLUDING participant-withdrawn rows (BPA-5).
    Every metric/cohort function in this module must start from this — never
    from `dataset.trials` directly — so a withdrawn trial can never silently
    contribute to a reported rate while its row remains retained for audit.
    Equivalent to `dataset.active_trials` (see models.py); kept as a local
    helper so every call site in this module reads identically."""
    return dataset.trials.exclude(withdrawn=True)


def get_dataset_info(dataset):
    """Identifier / status / purpose / counts for one EvaluationDataset.
    Every metric bundle in this module should be traceable back to this —
    never presented without knowing which dataset, whether it is finalized,
    and whether it is even a real research-study dataset (BPA-5 `purpose`) as
    opposed to QA/synthetic/pilot data. `total_trial_count` etc. below
    include withdrawn trials (transparency — withdrawal is never hidden from
    the researcher); every OTHER function in this module excludes them from
    its actual rate/count calculations (`_active_trials`)."""
    trials = dataset.trials.all()
    counts = trials.aggregate(
        total=Count('id'),
        pending=Count('id', filter=Q(trial_status=EvaluationTrial.TRIAL_STATUS_PENDING)),
        completed=Count('id', filter=Q(trial_status=EvaluationTrial.TRIAL_STATUS_COMPLETED)),
        aborted=Count('id', filter=Q(trial_status=EvaluationTrial.TRIAL_STATUS_ABORTED)),
        withdrawn=Count('id', filter=Q(withdrawn=True)),
    )
    return {
        'dataset_id': str(dataset.id),
        'dataset_name': dataset.name,
        'protocol_version': dataset.protocol_version,
        'status': dataset.status,
        'purpose': dataset.purpose,
        'is_finalized_dataset': dataset.is_finalized,
        'is_final_study_result': dataset.is_final_study_result,
        'total_trial_count': counts['total'],
        'pending_trial_count': counts['pending'],
        'completed_trial_count': counts['completed'],
        'aborted_trial_count': counts['aborted'],
        'withdrawn_trial_count': counts['withdrawn'],
        'unique_participant_count': trials.values('participant_code').distinct().count(),
    }


def _finalization_notice(dataset):
    if dataset.is_finalized:
        return {'is_finalized_dataset': True, 'warning': None}
    return {
        'is_finalized_dataset': False,
        'warning': 'INTERIM CONTROLLED-EVALUATION RESULTS — DATA COLLECTION NOT FINALIZED.',
    }


def get_stale_pending_trials_with_proof(dataset, hours=STALE_PENDING_PROOF_HOURS):
    """PENDING, non-withdrawn trials that captured a stage-1 encrypted
    liveness-proof embedding more than `hours` ago and were never resumed
    (BPA-5.1 §"Abandoned pending trials"). These retain sensitive
    biometric-derived data with no automatic expiry (see module docstring)
    — the only remedy in this checkpoint's scope is an explicit operator
    abort (evaluation_trial_abort -> _abort_trial), which clears the proof.
    Returns the queryset itself (never evaluated here) so callers can both
    `.count()` for a dashboard badge and iterate for a detail listing."""
    cutoff = timezone.now() - timedelta(hours=hours)
    return dataset.trials.filter(
        trial_status=EvaluationTrial.TRIAL_STATUS_PENDING,
        withdrawn=False,
        liveness_proof_embedding__isnull=False,
        liveness_captured_at__lt=cutoff,
    )


# ─── Cohort querysets (BPA-3 §4/§5) ─────────────────────────────────────────

def _primary_identity_cohort_qs(dataset):
    """PRIMARY identity-performance cohort (BPA-3 §5):
    COMPLETED + identity_ground_truth in {GENUINE, IMPOSTOR} +
    presentation_ground_truth = BONA_FIDE. NOT_TESTED is deliberately NOT
    treated as BONA_FIDE here — see module docs and
    docs/BIOMETRIC-EVALUATION-METHODOLOGY.md. Excludes withdrawn trials (BPA-5)."""
    return _active_trials(dataset).filter(
        trial_status=EvaluationTrial.TRIAL_STATUS_COMPLETED,
        identity_ground_truth__in=[_GENUINE, _IMPOSTOR],
        presentation_ground_truth=EvaluationTrial.PRESENTATION_BONA_FIDE,
    )


def _exploratory_identity_cohort_qs(dataset):
    """Broader EXPLORATORY cohort (BPA-3 §5): COMPLETED + identity ground
    truth known, WITHOUT the BONA_FIDE presentation restriction. Mixes staged
    presentation-attack trials into an identity-performance view — never the
    primary capstone number, offered only for research monitoring. Excludes
    withdrawn trials (BPA-5)."""
    return _active_trials(dataset).filter(
        trial_status=EvaluationTrial.TRIAL_STATUS_COMPLETED,
        identity_ground_truth__in=[_GENUINE, _IMPOSTOR],
    )


def _matcher_evaluable_cohort_qs(dataset):
    """MATCHER-EVALUABLE cohort (BPA-3 §10): primary cohort further
    restricted to trials that actually reached similarity comparison
    (similarity_score and matcher_base_decision both present). Excludes
    pre-comparison liveness/PAD denials, where the matcher was never run."""
    return _primary_identity_cohort_qs(dataset).filter(
        similarity_score__isnull=False,
        matcher_base_decision__isnull=False,
    )


# ─── Three-outcome matrix + core identity-performance formulas (BPA-3 §6-9) ─

def _identity_performance(qs, decision_field):
    """Shared 2x3-matrix + TAR/FRR/GMRR/FAR/TNR/IMRR/Coverage/Conditional-
    Automatic-Accuracy calculation, generic over which decision field is used
    (system_decision for FINAL performance, matcher_base_decision for
    MATCHER performance) — one aggregate query, no per-trial Python loop."""

    def q(identity, decisions):
        return Q(identity_ground_truth=identity, **{f'{decision_field}__in': decisions})

    agg = qs.aggregate(
        genuine_total=Count('id', filter=Q(identity_ground_truth=_GENUINE)),
        genuine_verified=Count('id', filter=q(_GENUINE, [_VERIFIED])),
        genuine_manual_review=Count('id', filter=q(_GENUINE, [_MANUAL_REVIEW])),
        genuine_rejected=Count('id', filter=q(_GENUINE, _AUTO_REJECT_DECISIONS)),
        impostor_total=Count('id', filter=Q(identity_ground_truth=_IMPOSTOR)),
        impostor_verified=Count('id', filter=q(_IMPOSTOR, [_VERIFIED])),
        impostor_manual_review=Count('id', filter=q(_IMPOSTOR, [_MANUAL_REVIEW])),
        impostor_rejected=Count('id', filter=q(_IMPOSTOR, _AUTO_REJECT_DECISIONS)),
    )

    genuine_total = agg['genuine_total']
    impostor_total = agg['impostor_total']
    eligible_total = genuine_total + impostor_total

    auto_decided = (
        agg['genuine_verified'] + agg['genuine_rejected']
        + agg['impostor_verified'] + agg['impostor_rejected']
    )
    correct_auto = agg['genuine_verified'] + agg['impostor_rejected']

    return {
        'decision_field': decision_field,
        'matrix': {
            'genuine': {
                'verified': agg['genuine_verified'],
                'manual_review': agg['genuine_manual_review'],
                'rejected': agg['genuine_rejected'],
                'total': genuine_total,
            },
            'impostor': {
                'verified': agg['impostor_verified'],
                'manual_review': agg['impostor_manual_review'],
                'rejected': agg['impostor_rejected'],
                'total': impostor_total,
            },
        },
        'tar': _rate(agg['genuine_verified'], genuine_total),
        'frr': _rate(agg['genuine_rejected'], genuine_total),
        'gmrr': _rate(agg['genuine_manual_review'], genuine_total),
        'far': _rate(agg['impostor_verified'], impostor_total),
        'tnr': _rate(agg['impostor_rejected'], impostor_total),
        'imrr': _rate(agg['impostor_manual_review'], impostor_total),
        'coverage': _rate(auto_decided, eligible_total),
        'conditional_automatic_accuracy': _rate(correct_auto, auto_decided),
        'correct_automatic_outcome_rate': _rate(correct_auto, eligible_total),
        'genuine_trial_count': genuine_total,
        'impostor_trial_count': impostor_total,
        'eligible_identity_trial_count': eligible_total,
    }


def get_final_system_performance(dataset):
    """FINAL AUTOMATED FANS-C PERFORMANCE (BPA-3 §7-9): `system_decision`
    against ground truth, over the PRIMARY bona-fide identity cohort. This is
    the "FANS-C system accuracy" number, in the qualified sense described in
    docs/BIOMETRIC-EVALUATION-METHODOLOGY.md §30 ("B")."""
    result = _identity_performance(_primary_identity_cohort_qs(dataset), 'system_decision')
    result['population'] = 'primary_bona_fide_identity_cohort'
    return result


def get_matcher_performance(dataset):
    """MATCHER PERFORMANCE (BPA-3 §10): `matcher_base_decision` against
    ground truth, restricted to trials that actually reached comparison
    (docs/BIOMETRIC-EVALUATION-METHODOLOGY.md §30 ("A")). Excludes
    pre-comparison liveness/PAD denials — the matcher was never evaluated on
    those, so including them would understate matcher FRR/increase its
    apparent reject rate for a reason the matcher had nothing to do with."""
    result = _identity_performance(_matcher_evaluable_cohort_qs(dataset), 'matcher_base_decision')
    result['population'] = 'matcher_evaluable_bona_fide_identity_cohort'
    return result


def get_exploratory_identity_performance(dataset):
    """EXPLORATORY (non-primary) identity performance using `system_decision`
    over the broader cohort that does NOT exclude staged presentation-attack
    trials. Offered for research monitoring only (BPA-3 §5) — never cite this
    as the capstone's primary FAR/FRR/TAR result."""
    result = _identity_performance(_exploratory_identity_cohort_qs(dataset), 'system_decision')
    result['population'] = 'exploratory_all_identity_cohort'
    result['warning'] = (
        'EXPLORATORY cohort — includes staged presentation-attack trials '
        'alongside bona-fide ones. Not the primary capstone metric.'
    )
    return result


# ─── Matcher -> final-system delta (BPA-3 §11) ─────────────────────────────

def get_matcher_to_final_delta(dataset):
    """Counts how post-score FANS-C safety policies changed the raw matcher
    decision into the final decision, over the matcher-evaluable cohort.
    Single grouped query — no per-transition-pair query loop."""
    qs = _matcher_evaluable_cohort_qs(dataset)
    rows = qs.values('matcher_base_decision', 'system_decision').annotate(count=Count('id'))
    transitions = {
        f"{row['matcher_base_decision']}_to_{row['system_decision']}": row['count']
        for row in rows
    }
    changed_count = qs.exclude(matcher_base_decision=F('system_decision')).count()
    escalation_reasons = qs.aggregate(
        quality_override_applied=Count('id', filter=Q(quality_override_applied=True)),
        lookalike_escalation_applied=Count('id', filter=Q(lookalike_escalation_applied=True)),
        representative_fallback_blocked=Count('id', filter=Q(representative_fallback_blocked=True)),
    )
    return {
        'population': 'matcher_evaluable_bona_fide_identity_cohort',
        'total_trial_count': qs.count(),
        'changed_count': changed_count,
        'transitions': transitions,
        'escalation_reasons': escalation_reasons,
        'note': (
            'These transitions are post-score FANS-C safety-policy escalations '
            '(lookalike detection, representative-fallback block), never FaceNet '
            'matcher errors — see docs/BIOMETRIC-EVALUATION-METHODOLOGY.md §29.'
        ),
    }


# ─── Participant / trial counts (BPA-3 §14) ────────────────────────────────

def get_participant_and_trial_counts(dataset):
    """Distinguishes unique participants from trial counts, and breaks
    completed trials down by identity/attack category. `genuine`/`impostor`/
    `attack` counts are over ALL completed trials (not restricted to the
    bona-fide primary cohort) — `eligible_identity_trial_count` is the
    primary-cohort count for comparison. Excludes withdrawn trials (BPA-5)
    via `_active_trials` — use `get_dataset_info()` for the withdrawn count
    itself."""
    trials = _active_trials(dataset)
    completed = trials.filter(trial_status=EvaluationTrial.TRIAL_STATUS_COMPLETED)
    agg = trials.aggregate(
        total=Count('id'),
        pending=Count('id', filter=Q(trial_status=EvaluationTrial.TRIAL_STATUS_PENDING)),
        completed=Count('id', filter=Q(trial_status=EvaluationTrial.TRIAL_STATUS_COMPLETED)),
        aborted=Count('id', filter=Q(trial_status=EvaluationTrial.TRIAL_STATUS_ABORTED)),
        genuine=Count('id', filter=Q(trial_status=EvaluationTrial.TRIAL_STATUS_COMPLETED, identity_ground_truth=_GENUINE)),
        impostor=Count('id', filter=Q(trial_status=EvaluationTrial.TRIAL_STATUS_COMPLETED, identity_ground_truth=_IMPOSTOR)),
        attack=Count('id', filter=Q(trial_status=EvaluationTrial.TRIAL_STATUS_COMPLETED, presentation_ground_truth__in=_ATTACK_TYPES)),
    )
    return {
        'unique_participant_count': trials.values('participant_code').distinct().count(),
        'total_trial_count': agg['total'],
        'pending_trial_count': agg['pending'],
        'completed_trial_count': agg['completed'],
        'aborted_trial_count': agg['aborted'],
        'eligible_identity_trial_count': _primary_identity_cohort_qs(dataset).count(),
        'genuine_trial_count': agg['genuine'],
        'impostor_trial_count': agg['impostor'],
        'attack_trial_count': agg['attack'],
    }


# ─── Beneficiary vs representative stratification (BPA-3 §15) ─────────────

def get_target_type_stratification(dataset):
    """Descriptive subgroup breakdown of FINAL-system performance by claimant
    target type. No significance test is performed or implied."""
    primary = _primary_identity_cohort_qs(dataset)
    beneficiary_result = _identity_performance(primary.filter(target_beneficiary__isnull=False), 'system_decision')
    representative_result = _identity_performance(primary.filter(target_representative__isnull=False), 'system_decision')
    return {
        'beneficiary': beneficiary_result,
        'representative': representative_result,
        'note': (
            'Descriptive subgroup rates only. No statistical significance test was '
            'performed — do not treat a difference between these rates as a proven '
            'performance difference without a formal test and adequate sample size.'
        ),
    }


# ─── Liveness pathway breakdown (BPA-3 §16) ────────────────────────────────

def get_liveness_pathway_breakdown(dataset):
    """Descriptive grouping of FINAL-system decision by stored
    `liveness_pathway`. Does NOT compute or label a "Machine-Verified Active
    Liveness Accuracy" — the active-challenge attestation is researcher-set,
    not machine-verified (see limitation string below and BPA-3 §16)."""
    completed = _active_trials(dataset).filter(trial_status=EvaluationTrial.TRIAL_STATUS_COMPLETED)
    breakdown = {}
    for pathway, _label in EvaluationTrial.LIVENESS_PATHWAY_CHOICES:
        sub = completed.filter(liveness_pathway=pathway)
        decision_rows = sub.values('system_decision').annotate(count=Count('id'))
        breakdown[pathway] = {
            'total': sub.count(),
            'decision_counts': {row['system_decision']: row['count'] for row in decision_rows},
        }
    return {
        'breakdown': breakdown,
        'unlabeled_count': completed.filter(liveness_pathway='').count(),
        'active_challenge_attestation_limitation': _ACTIVE_CHALLENGE_ATTESTATION_LIMITATION,
    }


# ─── Presentation-attack metrics (BPA-3 §17-18, corrected by BPA-3.1) ──────
#
# BPA-3.1 §4-9 correction: BPA-3's original get_presentation_attack_metrics()
# conflated two distinct concepts and is REMOVED, replaced by the two
# functions below:
#
#   get_presentation_attack_gate_metrics()  -- did the liveness/anti-spoof/PAD
#       SECURITY GATE itself pass or block the presentation? Derived from the
#       trial's actual recorded `liveness_pathway` (BPA-2.1's per-trial record
#       of which gate outcome occurred), NEVER from system_decision, and
#       NEVER restricted to identity_ground_truth=GENUINE -- BONA_FIDE is a
#       presentation-truth label independent of identity ground truth (a live
#       IMPOSTOR is still a BONA_FIDE presentation for gate purposes).
#
#   get_end_to_end_attack_outcome()  -- what did FANS-C's FINAL decision turn
#       out to be for a staged attack trial? This can be non-VERIFIED for
#       reasons that have nothing to do with PAD (an identity mismatch at the
#       matcher, lookalike escalation, the representative-fallback block) --
#       so it is explicitly labeled END-TO-END and never called "PAD
#       detection."
#
# The two must never be merged back into one "attack block rate."

_GATE_PASS_PATHWAYS = [
    EvaluationTrial.LIVENESS_PATHWAY_PASSIVE_ONLY,
    EvaluationTrial.LIVENESS_PATHWAY_ACTIVE_CHALLENGE,
]
_GATE_BLOCKED_PATHWAYS = [
    EvaluationTrial.LIVENESS_PATHWAY_FAILED_PASSIVE,
    EvaluationTrial.LIVENESS_PATHWAY_FAILED_ACTIVE,
]
_GATE_RECORDED_PATHWAYS = _GATE_PASS_PATHWAYS + _GATE_BLOCKED_PATHWAYS

_ACTIVE_CHALLENGE_ATTESTATION_LIMITATION = (
    'liveness_pathway=active_challenge / head_movement_completed is '
    'RESEARCHER-ATTESTED, not machine-verified via the production browser\'s '
    'MediaPipe motion-tracking pipeline. Do not derive "Machine-Verified Active '
    'Liveness Accuracy" from this breakdown.'
)


def _gate_counts(qs):
    """Raw gate-outcome counts for a set of trials, from the stored
    `liveness_pathway` field (the BPA-2.1 runner's own record of what the
    liveness/anti-spoof/PAD gate actually decided). Trials with no recorded
    pathway (blank) are excluded from `eligible_count` and reported
    separately — never silently folded into pass or block."""
    return {
        'eligible_count': qs.filter(liveness_pathway__in=_GATE_RECORDED_PATHWAYS).count(),
        'excluded_unknown_gate_outcome_count': qs.filter(liveness_pathway='').count(),
        'gate_passed_count': qs.filter(liveness_pathway__in=_GATE_PASS_PATHWAYS).count(),
        'gate_blocked_count': qs.filter(liveness_pathway__in=_GATE_BLOCKED_PATHWAYS).count(),
    }


def get_presentation_attack_gate_metrics(dataset):
    """
    PRESENTATION-ATTACK GATE PERFORMANCE (BPA-3.1 §6/§9). Measures the
    liveness/anti-spoof/PAD security gate itself, via `liveness_pathway` —
    never via `system_decision`, and never restricted to
    `identity_ground_truth=GENUINE`. Grouped by `presentation_ground_truth`
    only, so a BONA_FIDE trial with an IMPOSTOR identity is still counted in
    the bona-fide gate cohort (BPA-3.1 §4).
    """
    completed = _active_trials(dataset).filter(trial_status=EvaluationTrial.TRIAL_STATUS_COMPLETED)
    bona_fide_qs = completed.filter(presentation_ground_truth=EvaluationTrial.PRESENTATION_BONA_FIDE)
    attack_qs = completed.filter(presentation_ground_truth__in=_ATTACK_TYPES)

    def with_bona_fide_rates(counts):
        return {
            **counts,
            'bona_fide_gate_pass_rate': _rate(counts['gate_passed_count'], counts['eligible_count']),
            'bona_fide_gate_rejection_rate': _rate(counts['gate_blocked_count'], counts['eligible_count']),
        }

    def with_attack_rates(counts):
        return {
            **counts,
            'attack_gate_block_rate': _rate(counts['gate_blocked_count'], counts['eligible_count']),
            'attack_gate_pass_rate': _rate(counts['gate_passed_count'], counts['eligible_count']),
        }

    bona_fide_result = with_bona_fide_rates(_gate_counts(bona_fide_qs))
    # Optional identity stratification (BPA-3.1 §9) — never a hidden
    # requirement of the overall bona-fide cohort above.
    bona_fide_result['by_identity_ground_truth'] = {
        'genuine': with_bona_fide_rates(_gate_counts(bona_fide_qs.filter(identity_ground_truth=_GENUINE))),
        'impostor': with_bona_fide_rates(_gate_counts(bona_fide_qs.filter(identity_ground_truth=_IMPOSTOR))),
    }

    return {
        'population': (
            'All COMPLETED trials with a recorded liveness_pathway, grouped by '
            'presentation_ground_truth ONLY — independent of identity_ground_truth.'
        ),
        'gate_field_used': 'liveness_pathway',
        'bona_fide': bona_fide_result,
        'overall_attack': with_attack_rates(_gate_counts(attack_qs)),
        'per_attack_type': {
            attack_type: with_attack_rates(_gate_counts(attack_qs.filter(presentation_ground_truth=attack_type)))
            for attack_type in _ATTACK_TYPES
        },
        'active_challenge_attestation_limitation': _ACTIVE_CHALLENGE_ATTESTATION_LIMITATION,
        'naming_note': (
            'Descriptive gate-level metrics only: Attack Gate Block Rate, Attack Gate '
            'Pass Rate, Bona-Fide Gate Pass Rate, Bona-Fide Gate Rejection Rate. NOT '
            'labeled APCER/BPCER — no standards-conformance methodology exists for '
            'this controlled protocol.'
        ),
    }


def get_end_to_end_attack_outcome(dataset):
    """
    END-TO-END FANS-C ATTACK-TRIAL OUTCOME (BPA-3.1 §5/§7). What the FINAL
    automated FANS-C decision (`system_decision`) turned out to be for staged
    attack trials — deliberately NOT called a PAD detection rate. A trial may
    be automatically intercepted for reasons that have nothing to do with the
    liveness/PAD gate (an identity mismatch at the matcher, lookalike
    escalation, the representative-fallback block); cross-reference with
    `get_presentation_attack_gate_metrics()` to see whether the gate itself
    actually fired.
    """
    completed = _active_trials(dataset).filter(trial_status=EvaluationTrial.TRIAL_STATUS_COMPLETED)
    attack_qs = completed.filter(presentation_ground_truth__in=_ATTACK_TYPES)

    def outcome_for(qs):
        agg = qs.aggregate(
            total=Count('id'),
            intercepted=Count('id', filter=Q(system_decision__in=_AUTO_REJECT_DECISIONS)),
            manual_review=Count('id', filter=Q(system_decision=_MANUAL_REVIEW)),
            incorrectly_accepted=Count('id', filter=Q(system_decision=_VERIFIED)),
        )
        total = agg['total']
        return {
            'total': total,
            'automatically_intercepted': agg['intercepted'],
            'attack_non_acceptance_rate': _rate(agg['intercepted'], total),
            'routed_to_manual_review': agg['manual_review'],
            'attack_manual_review_rate': _rate(agg['manual_review'], total),
            'incorrectly_automatically_accepted': agg['incorrectly_accepted'],
            'attack_automatic_acceptance_rate': _rate(agg['incorrectly_accepted'], total),
        }

    return {
        'label': 'END-TO-END FANS-C ATTACK-TRIAL OUTCOME (not a PAD detection rate)',
        'overall': outcome_for(attack_qs),
        'per_attack_type': {
            attack_type: outcome_for(attack_qs.filter(presentation_ground_truth=attack_type))
            for attack_type in _ATTACK_TYPES
        },
        'note': (
            'Interception may result from the liveness/PAD gate, an identity mismatch '
            'at the matcher, lookalike escalation, or the representative-fallback '
            'block — do not attribute every automatically-intercepted attack trial to '
            'PAD. See get_presentation_attack_gate_metrics() for the gate itself.'
        ),
    }


# ─── Timing analytics (BPA-3 §19-20) ───────────────────────────────────────

_TIMING_METRIC_NAME = 'Controlled Verification Elapsed Time'


def get_timing_analytics(dataset, group_by=None):
    """count/mean/median/min/max/sample-stddev over
    `verification_duration_ms`, optionally grouped by 'system_decision',
    'target_type', or 'liveness_pathway'. Uses SAMPLE standard deviation
    (n-1) — see docs/BIOMETRIC-EVALUATION-METHODOLOGY.md §15 for the exact
    start/end definition of this metric ("Controlled Verification Elapsed
    Time" — not "capture-to-display latency")."""
    completed = _active_trials(dataset).filter(trial_status=EvaluationTrial.TRIAL_STATUS_COMPLETED)

    def timing_for(qs):
        values = list(
            qs.exclude(verification_duration_ms__isnull=True)
            .order_by('verification_duration_ms')
            .values_list('verification_duration_ms', flat=True)
        )
        return _numeric_stats(values)

    result = {
        'metric_name': _TIMING_METRIC_NAME,
        'start_definition': 'Server timestamp when the trial runner authorized the trial to begin (evaluation_started_at).',
        'end_definition': 'Server timestamp when the final outcome was computed (evaluated_at).',
        'included_stages': 'Browser round-trip of captured frame(s), decode/detect/align, anti-spoof check, PAD analysis, embedding + comparison (when reached), decision computation.',
        'excluded_stages': 'Time before the runner page loads, camera warm-up, and browser rendering time after the server responds.',
        'overall': timing_for(completed),
        'groups': None,
    }

    if group_by == 'system_decision':
        result['groups'] = {
            decision: timing_for(completed.filter(system_decision=decision))
            for decision, _label in VerificationAttempt.DECISION_CHOICES
        }
    elif group_by == 'target_type':
        result['groups'] = {
            'beneficiary': timing_for(completed.filter(target_beneficiary__isnull=False)),
            'representative': timing_for(completed.filter(target_representative__isnull=False)),
        }
    elif group_by == 'liveness_pathway':
        result['groups'] = {
            pathway: timing_for(completed.filter(liveness_pathway=pathway))
            for pathway, _label in EvaluationTrial.LIVENESS_PATHWAY_CHOICES
        }

    return result


# ─── Similarity score distributions (BPA-3 §21, domain-corrected by BPA-3.1) ─
#
# BPA-3.1 §2 correction: FANS-C's similarity score is a raw cosine similarity
# (`face_utils.cosine_similarity` — `np.dot(emb1/n1, emb2/n2)`, never
# clamped), whose true mathematical domain is [-1, 1], not [0, 1]. The
# original BPA-3 histogram used a [0.0, 1.0] range and its clamp
# (`max(0, min(idx, bins-1))`) would have silently placed a legitimately
# negative score into the same bin as a small positive one. The histogram now
# spans the full legal cosine domain; the clamp below only ever fires for a
# floating-point value sitting exactly on a boundary, never to hide an
# out-of-declared-range score.

_SCORE_DOMAIN = (-1.0, 1.0)


def _histogram(values, bins):
    """Fixed-range histogram over the full legal cosine-similarity domain
    `_SCORE_DOMAIN` (-1.0 to 1.0) — never the narrower [0, 1] a purely
    face-recognition-tuned assumption would suggest. A valid negative score
    lands in its own correct low bin, not bin 0."""
    lo, hi = _SCORE_DOMAIN
    width = (hi - lo) / bins
    counts = [0] * bins
    for v in values:
        idx = int((v - lo) / width) if width else 0
        idx = max(0, min(idx, bins - 1))
        counts[idx] += 1
    edges = [lo + i * width for i in range(bins + 1)]
    return {'bin_edges': edges, 'counts': counts, 'domain': _SCORE_DOMAIN}


def _score_distribution(values, bins):
    stats = _numeric_stats(values)
    stats['values'] = values
    stats['histogram'] = _histogram(values, bins) if values else None
    return stats


def get_score_distributions(dataset, bins=10):
    """Genuine vs. impostor similarity-score distributions over the
    matcher-evaluable bona-fide cohort — inputs for a future ROC/histogram
    chart, not a chart itself (BPA-4). Every valid stored score, including a
    legally negative cosine similarity, is represented — see `_SCORE_DOMAIN`
    and BPA-3.1 §2/§17."""
    qs = _matcher_evaluable_cohort_qs(dataset)
    genuine_scores = list(qs.filter(identity_ground_truth=_GENUINE).order_by('similarity_score').values_list('similarity_score', flat=True))
    impostor_scores = list(qs.filter(identity_ground_truth=_IMPOSTOR).order_by('similarity_score').values_list('similarity_score', flat=True))
    return {
        'population': 'matcher_evaluable_bona_fide_identity_cohort',
        'score_domain': _SCORE_DOMAIN,
        'genuine': _score_distribution(genuine_scores, bins),
        'impostor': _score_distribution(impostor_scores, bins),
    }


# ─── Threshold sweep (BPA-3 §22-23) ────────────────────────────────────────

def run_threshold_sweep(dataset, candidate_policies):
    """Offline MATCHER-LEVEL COUNTERFACTUAL analysis: re-evaluates
    `face_utils.decide_base_outcome` (the exact shared function production
    uses) against each trial's real persisted `similarity_score`, for each
    `(manual_review_threshold, auto_verify_threshold)` candidate pair in
    `candidate_policies`. Does NOT re-run FaceNet and does NOT re-apply the
    final-system post-score safety overlay (quality override / lookalike
    escalation / representative-fallback-block) — see BPA-3 §22-23. No
    "optimal threshold" is selected or implied."""
    rows = list(
        _matcher_evaluable_cohort_qs(dataset).values('identity_ground_truth', 'similarity_score')
    )
    results = []
    for manual_review_threshold, auto_verify_threshold in candidate_policies:
        counts = {
            'genuine_verified': 0, 'genuine_manual_review': 0, 'genuine_rejected': 0,
            'impostor_verified': 0, 'impostor_manual_review': 0, 'impostor_rejected': 0,
        }
        for row in rows:
            decision = face_utils.decide_base_outcome(
                row['similarity_score'], manual_review_threshold, auto_verify_threshold,
            )
            prefix = 'genuine' if row['identity_ground_truth'] == _GENUINE else 'impostor'
            if decision == _VERIFIED:
                counts[f'{prefix}_verified'] += 1
            elif decision == _MANUAL_REVIEW:
                counts[f'{prefix}_manual_review'] += 1
            else:
                counts[f'{prefix}_rejected'] += 1

        genuine_total = counts['genuine_verified'] + counts['genuine_manual_review'] + counts['genuine_rejected']
        impostor_total = counts['impostor_verified'] + counts['impostor_manual_review'] + counts['impostor_rejected']
        auto_decided = counts['genuine_verified'] + counts['genuine_rejected'] + counts['impostor_verified'] + counts['impostor_rejected']
        correct_auto = counts['genuine_verified'] + counts['impostor_rejected']

        results.append({
            'manual_review_threshold': manual_review_threshold,
            'auto_verify_threshold': auto_verify_threshold,
            'counts': counts,
            'tar': _rate(counts['genuine_verified'], genuine_total),
            'frr': _rate(counts['genuine_rejected'], genuine_total),
            'gmrr': _rate(counts['genuine_manual_review'], genuine_total),
            'far': _rate(counts['impostor_verified'], impostor_total),
            'tnr': _rate(counts['impostor_rejected'], impostor_total),
            'imrr': _rate(counts['impostor_manual_review'], impostor_total),
            'coverage': _rate(auto_decided, genuine_total + impostor_total),
            'conditional_automatic_accuracy': _rate(correct_auto, auto_decided),
        })

    return {
        'population': 'matcher_evaluable_bona_fide_identity_cohort',
        'limitation': (
            'MATCHER-LEVEL COUNTERFACTUAL ANALYSIS ONLY. Re-runs decide_base_outcome() '
            'against the persisted similarity_score for each candidate threshold pair '
            'using the review-floor logic already inside that shared function '
            '(review_band = manual_review_threshold * 0.85). Does not reproduce the '
            'FINAL FANS-C decision at that threshold — the post-score safety overlay '
            '(quality override / lookalike escalation / representative-fallback-block) '
            'is not re-applied.'
        ),
        'results': results,
    }


# ─── ROC / AUC / EER (BPA-3 §24-26) ─────────────────────────────────────────

def compute_roc(dataset):
    """Binary MATCHER characterization over the matcher-evaluable bona-fide
    cohort: TPR/FPR swept across every observed similarity-score threshold.
    Manual Review is NOT a third axis here — this characterizes score
    separability, not the deployed three-zone operating policy."""
    qs = _matcher_evaluable_cohort_qs(dataset)
    genuine_scores = sorted(qs.filter(identity_ground_truth=_GENUINE).values_list('similarity_score', flat=True))
    impostor_scores = sorted(qs.filter(identity_ground_truth=_IMPOSTOR).values_list('similarity_score', flat=True))
    n_genuine, n_impostor = len(genuine_scores), len(impostor_scores)

    warnings = []
    if n_genuine == 0:
        warnings.append('No genuine scores available in this cohort — ROC cannot be computed.')
    if n_impostor == 0:
        warnings.append('No impostor scores available in this cohort — ROC cannot be computed.')
    if warnings:
        return {'points': [], 'n_genuine': n_genuine, 'n_impostor': n_impostor, 'warnings': warnings}

    all_scores = sorted(set(genuine_scores) | set(impostor_scores), reverse=True)
    # Sweep from an effectively-infinite threshold (accept nothing) down to
    # below the minimum observed score (accept everything), so the curve
    # runs from (0,0) to (1,1).
    candidate_thresholds = [all_scores[0] + 1e-9] + all_scores + [all_scores[-1] - 1e-9]

    points = []
    for t in candidate_thresholds:
        tp = sum(1 for s in genuine_scores if s >= t)
        fp = sum(1 for s in impostor_scores if s >= t)
        points.append({'threshold': t, 'tpr': tp / n_genuine, 'fpr': fp / n_impostor})

    return {'points': points, 'n_genuine': n_genuine, 'n_impostor': n_impostor, 'warnings': []}


def compute_auc(dataset):
    """Deterministic trapezoidal AUC over the ROC curve above. Returns
    `auc=None` (with a warning) when either class is empty — never a
    fabricated placeholder value. Not a claim about production correctness,
    only about matcher score separability."""
    roc = compute_roc(dataset)
    if roc['warnings']:
        return {'auc': None, 'method': None, 'warnings': roc['warnings']}

    points = sorted(roc['points'], key=lambda p: (p['fpr'], p['tpr']))
    auc = 0.0
    for i in range(1, len(points)):
        x0, y0 = points[i - 1]['fpr'], points[i - 1]['tpr']
        x1, y1 = points[i]['fpr'], points[i]['tpr']
        auc += (x1 - x0) * (y0 + y1) / 2.0

    return {
        'auc': auc,
        'method': 'trapezoidal',
        'n_genuine': roc['n_genuine'],
        'n_impostor': roc['n_impostor'],
        'warnings': [],
    }


def compute_eer(dataset):
    """Estimated Equal Error Rate via linear interpolation between the two
    adjacent swept-threshold points where FAR (=FPR) and FRR (=1-TPR) cross.
    MATCHER CHARACTERIZATION only — not "the optimal threshold" and not
    necessarily the FANS-C deployed operating point."""
    roc = compute_roc(dataset)
    if roc['warnings']:
        return {'eer': None, 'threshold': None, 'method': None, 'warnings': roc['warnings']}

    # Sweep from high threshold (FAR ascending as threshold decreases) to low.
    points = sorted(roc['points'], key=lambda p: -p['threshold'])
    prev = None
    for p in points:
        far, frr, threshold = p['fpr'], 1 - p['tpr'], p['threshold']
        if far == frr:
            return {'eer': far, 'threshold': threshold, 'method': 'exact_match', 'warnings': []}
        if prev is not None:
            pfar, pfrr, pthreshold = prev
            d_prev = pfar - pfrr
            d_curr = far - frr
            if d_prev * d_curr < 0:
                ratio = d_prev / (d_prev - d_curr)
                eer = ((pfar + ratio * (far - pfar)) + (pfrr + ratio * (frr - pfrr))) / 2.0
                threshold_est = pthreshold + ratio * (threshold - pthreshold)
                return {'eer': eer, 'threshold': threshold_est, 'method': 'linear_interpolation', 'warnings': []}
        prev = (far, frr, threshold)

    return {
        'eer': None,
        'threshold': None,
        'method': None,
        'warnings': ['No FAR/FRR crossing found (e.g. perfect class separation) — EER not estimable by this method.'],
    }


# ─── Claim Recording Reliability (BPA-3 §28) ───────────────────────────────

def get_claim_recording_reliability():
    """
    NOT derived from EvaluationTrial — this concerns operational
    VerificationAttempt/ClaimRecord data. `ClaimRecord.status` is a mutable
    current-state field, not an append-only transition log: a claim that is
    `claimed` today may have been `cancelled` and re-claimed, or `claimed`
    and later `cancelled`, at some point since. The current schema cannot
    distinguish these histories retrospectively, so neither Population A
    ("no valid existing claimed record at decision time") nor Population B
    ("existing claimed record already present") can be reconstructed
    reliably for past VerificationAttempt rows.

    Returns a structured "not computable" result rather than fabricating
    Claim Recording Reliability or Duplicate-Block Correctness from an
    unreliable historical reconstruction.
    """
    return {
        'computable': False,
        'reason': (
            'ClaimRecord.status/claimed_at/updated_at reflect only the CURRENT state of '
            'a claim, with no append-only status-history log. Reconstructing "was there '
            'already a valid claimed record for this beneficiary+event at the moment '
            'this historical VerificationAttempt was decided" cannot be done reliably '
            'from current-state columns alone once any claim has since changed status.'
        ),
        'recommendation': (
            'Add prospective instrumentation — e.g. an immutable claim-state '
            'snapshot recorded on VerificationAttempt at decision time, or an '
            'append-only ClaimRecord status-history table — so future attempts can '
            'be evaluated correctly going forward. Do not compute Claim Recording '
            'Reliability or Duplicate-Block Correctness retrospectively under the '
            'current schema.'
        ),
    }


# ─── Threshold-policy presentation helpers (BPA-4 §16-18) ─────────────────
#
# Additive only — neither function recomputes or alters any reviewed BPA-3/
# BPA-3.1 formula above. They exist so the BPA-4 UI never has to (a) collapse
# multiple historical threshold snapshots into one misleading line, or
# (b) invent its own "recommended" sweep candidates in a template.

def get_threshold_snapshot_summary(dataset):
    """Distinct (review_threshold_snapshot, auto_verify_threshold_snapshot)
    pairs actually recorded on this dataset's matcher-evaluable trials — the
    thresholds EvaluationTrial snapshotted at run time, never today's live
    SystemConfig value silently presented as if it were historical
    (BPA-4 §16). `multiple_policies_represented=True` means the caller must
    say so rather than draw one single threshold line."""
    qs = _matcher_evaluable_cohort_qs(dataset)
    policies = list(
        qs.exclude(review_threshold_snapshot__isnull=True)
        .exclude(auto_verify_threshold_snapshot__isnull=True)
        .values('review_threshold_snapshot', 'auto_verify_threshold_snapshot')
        .annotate(trial_count=Count('id'))
        .order_by('-trial_count')
    )
    return {
        'distinct_policy_count': len(policies),
        'policies': policies,
        'multiple_policies_represented': len(policies) > 1,
        'unlabeled_count': qs.filter(
            Q(review_threshold_snapshot__isnull=True) | Q(auto_verify_threshold_snapshot__isnull=True)
        ).count(),
    }


def default_exploratory_threshold_candidates(dataset):
    """Small, transparent EXPLORATORY threshold-sensitivity candidate set
    (BPA-4 §18): every distinct (review_threshold_snapshot,
    auto_verify_threshold_snapshot) pair this dataset's own trials actually
    used, plus the current live SystemConfig operating pair as one explicit
    comparison value. NOT a validated or recommended policy — callers must
    keep `run_threshold_sweep`'s 'limitation' text next to any table built
    from this. Never a hardcoded "recommended" range."""
    from .models import SystemConfig
    pairs = {
        (round(p['review_threshold_snapshot'], 4), round(p['auto_verify_threshold_snapshot'], 4))
        for p in get_threshold_snapshot_summary(dataset)['policies']
    }
    pairs.add((round(SystemConfig.get_threshold(), 4), round(SystemConfig.get_auto_verify_threshold(), 4)))
    return sorted(pairs)


# ─── Data-quality audit (BPA-5) ─────────────────────────────────────────────
#
# Every check below is defense-in-depth against a rule ALREADY enforced by
# EvaluationTrial.clean()/model constraints at save time (participant_code/
# target_identity_code required, completed trials need a system_decision,
# duration non-negative, etc.). A non-zero count here means some row bypassed
# that validation — e.g. created directly via shell/fixture/an older schema
# version — and should be investigated before the dataset is cited as a
# result; it is deliberately NOT a new validation rule and does not itself
# block finalization (see evaluation_dataset_finalize, unchanged by BPA-5).

def get_dataset_quality_report(dataset):
    """Read-only data-quality audit for one dataset. Counts only — never
    mutates data. Operates on `_active_trials` (withdrawn rows are already
    excluded from analysis, so a data-quality issue on a withdrawn row is not
    reported here)."""
    trials = _active_trials(dataset)
    completed = trials.filter(trial_status=EvaluationTrial.TRIAL_STATUS_COMPLETED)
    pending = trials.filter(trial_status=EvaluationTrial.TRIAL_STATUS_PENDING)

    checks = {
        'missing_participant_code': trials.filter(Q(participant_code='') | Q(participant_code__isnull=True)).count(),
        'missing_target_identity_code': trials.filter(Q(target_identity_code='') | Q(target_identity_code__isnull=True)).count(),
        'missing_identity_ground_truth': trials.filter(Q(identity_ground_truth='') | Q(identity_ground_truth__isnull=True)).count(),
        'completed_without_system_decision': completed.filter(system_decision__isnull=True).count(),
        'matcher_decision_with_null_similarity': trials.filter(
            matcher_base_decision__isnull=False, similarity_score__isnull=True,
        ).count(),
        'completed_without_threshold_snapshot': completed.filter(
            Q(review_threshold_snapshot__isnull=True) | Q(auto_verify_threshold_snapshot__isnull=True)
        ).count(),
        'negative_duration': trials.filter(verification_duration_ms__lt=0).count(),
        'gate_blocked_but_presentation_not_tested': completed.filter(
            liveness_pathway__in=_GATE_BLOCKED_PATHWAYS,
            presentation_ground_truth=EvaluationTrial.PRESENTATION_NOT_TESTED,
        ).count(),
        'pending_with_unresolved_target': pending.filter(
            target_beneficiary__isnull=True, target_representative__isnull=True,
        ).count(),
    }
    issue_count = sum(checks.values())
    return {
        'dataset_id': str(dataset.id),
        'checks': checks,
        'issue_count': issue_count,
        'has_issues': issue_count > 0,
        'note': (
            'Defense-in-depth audit only — every check above is already enforced by '
            'EvaluationTrial.clean()/model constraints at save time. A non-zero count '
            'means a row bypassed that validation and should be investigated; it does '
            'not by itself block dataset finalization (see get_study_readiness() for '
            'the technical readiness classification that DOES weigh this).'
        ),
    }


# ─── Study readiness (BPA-5) ────────────────────────────────────────────────

def get_study_readiness(dataset):
    """TECHNICAL-ONLY readiness classification: 'READY' / 'READY_WITH_LIMITATIONS'
    / 'NOT_READY', derived purely from objective schema/workflow checks
    already implemented elsewhere in this module (dataset status, trial
    completeness, the data-quality audit above, ground-truth class coverage,
    dataset purpose). This function makes NO ethics/IRB, statistical-power,
    or institutional-approval determination — see the returned 'scope' note.
    A 'READY' result means the SYSTEM is technically ready to support a real
    controlled evaluation; it is never the same claim as 'the study has been
    completed' — no real participant data has been collected by any BPA
    checkpoint through BPA-5."""
    info = get_dataset_info(dataset)
    quality = get_dataset_quality_report(dataset)
    primary = _primary_identity_cohort_qs(dataset)
    has_genuine = primary.filter(identity_ground_truth=_GENUINE).exists()
    has_impostor = primary.filter(identity_ground_truth=_IMPOSTOR).exists()

    reasons_not_ready = []
    reasons_limitation = []

    if dataset.status != EvaluationDataset.STATUS_COMPLETED:
        reasons_not_ready.append(
            f'Dataset status is {dataset.get_status_display()}, not Completed — data '
            'collection/finalization is not finished.'
        )
    if info['total_trial_count'] == 0:
        reasons_not_ready.append('Dataset has zero trials.')
    # Excludes withdrawn (BPA-5, mirrors evaluation_dataset_finalize) — a
    # trial withdrawn before it ever ran is already excluded from analysis
    # and must not block readiness forever.
    active_pending_count = _active_trials(dataset).filter(trial_status=EvaluationTrial.TRIAL_STATUS_PENDING).count()
    if active_pending_count > 0:
        reasons_not_ready.append(
            f"{active_pending_count} trial(s) are still PENDING (never run or aborted)."
        )
    if quality['issue_count'] > 0:
        reasons_not_ready.append(
            f"{quality['issue_count']} data-quality issue(s) found — see get_dataset_quality_report()."
        )

    if not reasons_not_ready:
        if dataset.purpose != EvaluationDataset.PURPOSE_RESEARCH_STUDY:
            reasons_limitation.append(
                f'Dataset purpose is "{dataset.get_purpose_display()}", not Research Study — '
                'not eligible to be cited as a final study result (see '
                'EvaluationDataset.is_final_study_result).'
            )
        if not has_genuine:
            reasons_limitation.append('No eligible GENUINE (bona-fide) trials — FRR/TAR/GMRR cannot be computed.')
        if not has_impostor:
            reasons_limitation.append('No eligible IMPOSTOR (bona-fide) trials — FAR/TNR/IMRR cannot be computed.')
        if info['withdrawn_trial_count'] > 0:
            reasons_limitation.append(
                f"{info['withdrawn_trial_count']} trial(s) withdrawn and excluded from analysis "
                '— reduces effective sample size.'
            )
        if dataset.status == EvaluationDataset.STATUS_ARCHIVED:
            reasons_limitation.append('Dataset is ARCHIVED — read-only, retained for record-keeping.')

    if reasons_not_ready:
        status = 'NOT_READY'
    elif reasons_limitation:
        status = 'READY_WITH_LIMITATIONS'
    else:
        status = 'READY'

    return {
        'dataset_id': str(dataset.id),
        'status': status,
        'reasons_not_ready': reasons_not_ready,
        'reasons_limitation': reasons_limitation,
        'scope': (
            'Technical readiness only, derived from schema/workflow checks (dataset '
            'status, trial completeness, data-quality audit, ground-truth class '
            'coverage, dataset purpose). NOT an IRB/ethics-approval determination, NOT '
            'a statistical-power determination, and NOT a claim that any real '
            'participant data has been collected. SYSTEM READY is not the same as '
            'STUDY COMPLETED.'
        ),
    }


# ─── Top-level report orchestrator ──────────────────────────────────────────

def build_metric_report(dataset, threshold_candidates=None):
    """Bundles every BPA-3 metric for one EvaluationDataset into a single
    read-only result. Callers (future BPA-4 UI, tests, CSV export) should
    generally use this rather than calling every function individually, so
    the finalized/interim distinction (BPA-3 §30) is applied consistently
    everywhere a report is produced. `threshold_candidates`, if given, is a
    list of `(manual_review_threshold, auto_verify_threshold)` tuples for
    `run_threshold_sweep` — omitted by default since no default/optimal
    threshold set should ever be silently assumed."""
    report = {
        'dataset': get_dataset_info(dataset),
        **_finalization_notice(dataset),
        'participant_and_trial_counts': get_participant_and_trial_counts(dataset),
        'final_system_performance': get_final_system_performance(dataset),
        'matcher_performance': get_matcher_performance(dataset),
        'exploratory_identity_performance': get_exploratory_identity_performance(dataset),
        'matcher_to_final_delta': get_matcher_to_final_delta(dataset),
        'target_type_stratification': get_target_type_stratification(dataset),
        'liveness_pathway_breakdown': get_liveness_pathway_breakdown(dataset),
        'presentation_attack_gate_metrics': get_presentation_attack_gate_metrics(dataset),
        'end_to_end_attack_outcome': get_end_to_end_attack_outcome(dataset),
        'timing': get_timing_analytics(dataset, group_by='system_decision'),
        'score_distributions': get_score_distributions(dataset),
        'roc': compute_roc(dataset),
        'auc': compute_auc(dataset),
        'eer': compute_eer(dataset),
        'claim_recording_reliability': get_claim_recording_reliability(),
        'data_quality': get_dataset_quality_report(dataset),
        'study_readiness': get_study_readiness(dataset),
    }
    if threshold_candidates:
        report['threshold_sweep'] = run_threshold_sweep(dataset, threshold_candidates)
    return report
