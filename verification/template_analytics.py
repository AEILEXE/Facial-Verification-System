"""
Per-template face match analytics (P1.6) — which stored embedding (primary
FaceEmbedding vs. an AdditionalFaceEmbedding) is winning matches for a
beneficiary, built entirely from VerificationAttempt.matched_template
(already populated by compare_with_all_embeddings() in face_utils.py).

Also includes an appearance-drift signal (appearance_drift_candidates) that
serves the same purpose a literal image-based age-estimation model would —
flagging beneficiaries whose face may have changed enough over time to
warrant re-enrollment — WITHOUT bundling an unverified third-party ML model.
This project already has an explicit rule (see the Phase 4 audit) against
automatic rejection based on estimated age: on-device age-estimation models
are trained/validated mostly on younger demographics and are notably less
reliable for elderly faces on low-quality webcam captures, which is exactly
the population this system serves. A time-elapsed + declining-match-score
trend is transparent, explainable to an admin, and needs no new dependency
or model file of unverifiable provenance — and, like the template-drift
signal below, it is advisory ONLY.

Everything in this module is advisory: get_reenrollment_candidates() and
appearance_drift_candidates() surface *suggestions*, never automatic action.
No function here writes to FaceEmbedding, AdditionalFaceEmbedding, or
triggers re-enrollment on its own — see update_face_data
(verification/views.py), which only reads this module to show a
non-blocking banner.
"""
from django.db.models import Count, Max, Q
from django.utils import timezone

from .models import VerificationAttempt

PRIMARY_TEMPLATE_LABEL = 'primary'

# How far back "recent" verified attempts look when deciding whether a
# non-primary template has taken over — matches the general fraud-signal
# window style (settings-backed, .env overridable) without adding a new
# dedicated setting for a Phase 1 advisory feature.
_RECENT_WINDOW_DAYS = 180

# Minimum number of recent VERIFIED attempts required before "majority
# non-primary" is treated as meaningful. Without this floor, a beneficiary
# with exactly 1 verified attempt on a non-primary template scores
# non_primary_wins(1) * 2 > total(1) and gets flagged from a single data
# point. This is a project-defined heuristic (no statistical literature
# was consulted to derive it) chosen to match the sample size already
# required by the appearance-drift signal below (_DRIFT_MIN_ATTEMPTS_EACH_SIDE
# * 2 = 6 would be stricter; 4 was chosen as a lower, still non-trivial bar
# since this signal only needs one-sided majority, not a before/after split).
_REENROLLMENT_MIN_ATTEMPTS = 4

# Appearance-drift thresholds: compare the average similarity score of a
# beneficiary's most recent verified attempts against their earliest ones.
# A drop of this size, with enough attempts on both sides to be meaningful,
# suggests the stored template is matching less well over time.
_DRIFT_MIN_ATTEMPTS_EACH_SIDE = 3
_DRIFT_SCORE_DROP_THRESHOLD = 0.08


def get_template_win_stats(beneficiary=None):
    """Win counts per (beneficiary, matched_template) across VERIFIED attempts
    only. Optionally scoped to one beneficiary.

    Restricted to decision=VERIFIED because matched_template records whichever
    template scored *highest* in compare_with_all_embeddings(), regardless of
    whether that score cleared the verification threshold — a NOT_VERIFIED or
    MANUAL_REVIEW attempt can still carry a non-empty matched_template. Counting
    those as "wins" would mislabel failed/unresolved comparisons as successful
    template matches. It would also let the anti-spoof sentinel value
    'beneficiary_face_on_rep_claim' (set on a DENIED representative-claim
    cross-probe block in verify_submit — not a real stored face template)
    appear in this table as if it were one."""
    qs = VerificationAttempt.objects.filter(
        decision=VerificationAttempt.DECISION_VERIFIED,
    ).exclude(matched_template='')
    if beneficiary is not None:
        qs = qs.filter(beneficiary=beneficiary)
    return list(
        qs.values('beneficiary__id', 'beneficiary__beneficiary_id',
                  'beneficiary__first_name', 'beneficiary__last_name', 'matched_template')
        .annotate(wins=Count('id'), last_win=Max('timestamp'))
        .order_by('-wins')
    )


def get_reenrollment_candidates():
    """Beneficiaries where a non-primary template has won a majority of their
    recent verified attempts — an advisory list only. Never mutates any
    embedding; a human decides whether to act on this.

    Rule (project-defined heuristic, not derived from statistical literature):
    over the trailing _RECENT_WINDOW_DAYS (180) days, a beneficiary needs at
    least _REENROLLMENT_MIN_ATTEMPTS (4) verified attempts AND a non-primary
    template winning more than half of them. The minimum-attempts floor exists
    because "majority" is not meaningful from 1-2 data points — without it, a
    single verified attempt matched on a non-primary template (1 win out of 1)
    would trivially satisfy "won more than half"."""
    window_start = timezone.now() - timezone.timedelta(days=_RECENT_WINDOW_DAYS)
    rows = (
        VerificationAttempt.objects
        .filter(
            decision=VerificationAttempt.DECISION_VERIFIED,
            timestamp__gte=window_start,
        )
        .exclude(matched_template='')
        .values('beneficiary__id')
        .annotate(
            total=Count('id'),
            non_primary_wins=Count('id', filter=~Q(matched_template=PRIMARY_TEMPLATE_LABEL)),
        )
    )
    candidates = []
    for row in rows:
        if row['total'] >= _REENROLLMENT_MIN_ATTEMPTS and row['non_primary_wins'] * 2 > row['total']:
            candidates.append(row['beneficiary__id'])
    return candidates


def appearance_drift_flag_for(beneficiary):
    """
    Compare the average similarity score of this beneficiary's earliest
    verified attempts against their most recent ones. A meaningful drop
    (see _DRIFT_SCORE_DROP_THRESHOLD) with enough data on both sides
    suggests the enrolled photo is matching less well as time passes —
    the same real-world signal an age-estimation model would be trying to
    approximate, without the reliability problems of doing that from pixels
    alone. Returns (flagged: bool, reason: str). Advisory only.
    """
    scores = list(
        VerificationAttempt.objects
        .filter(beneficiary=beneficiary, decision=VerificationAttempt.DECISION_VERIFIED)
        .exclude(similarity_score__isnull=True)
        .order_by('timestamp')
        .values_list('similarity_score', flat=True)
    )
    n = _DRIFT_MIN_ATTEMPTS_EACH_SIDE
    if len(scores) < n * 2:
        return False, ''

    earliest_avg = sum(scores[:n]) / n
    recent_avg = sum(scores[-n:]) / n
    drop = earliest_avg - recent_avg
    if drop < _DRIFT_SCORE_DROP_THRESHOLD:
        return False, ''

    return True, (
        f'Match confidence has declined over time (avg {earliest_avg:.2f} -> {recent_avg:.2f} '
        f'across {len(scores)} verified attempts). This can happen naturally as appearance '
        'changes — consider re-enrolling if this continues.'
    )


def reenrollment_suggestion_for(beneficiary):
    """Return (suggested: bool, reason: str) for a single beneficiary, for use
    in update_face_data's advisory banner. Checks both the template-drift
    signal and the appearance-drift signal; either can trigger the banner."""
    candidate_ids = get_reenrollment_candidates()
    if beneficiary.id in candidate_ids:
        return True, (
            'An additional face template has been matching more often than the primary '
            'in recent verifications. Consider re-enrolling the primary photo.'
        )

    drift_flagged, drift_reason = appearance_drift_flag_for(beneficiary)
    if drift_flagged:
        return True, drift_reason

    return False, ''
