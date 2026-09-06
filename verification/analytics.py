"""
Read-only aggregation queries for the Analytics Dashboard (Executive /
Operational / Security tabs). Pure query functions, no views/templates here —
kept separate from views.py (already ~5000 lines) purely for readability.

Every function accepts optional date_from/date_to (datetime.date or None) and
scopes time-bounded aggregates to that range; omitted bounds mean unbounded.
Nothing here writes to the database.
"""
import datetime

from django.db.models import Count, Sum, Q
from django.db.models.functions import TruncDate, TruncMonth
from django.utils import timezone

from beneficiaries.models import Beneficiary
from accounts.models import CustomUser
from logs.models import AuditLog
from .models import VerificationAttempt, ClaimRecord, StipendEvent


def _apply_date_range(qs, field, date_from, date_to):
    if date_from:
        qs = qs.filter(**{f'{field}__date__gte': date_from})
    if date_to:
        qs = qs.filter(**{f'{field}__date__lte': date_to})
    return qs


# Safety cap on how many days a bounded range will be zero-filled for — an
# admin selecting a multi-year range should not allocate a zero row per day
# in memory. Only affects the fill step below; the underlying aggregate
# query is unaffected and still returns whatever rows exist.
_MAX_FILL_DAYS = 400


def _fill_daily_gaps(rows, date_from, date_to, day_key, value_keys):
    """Zero-fill days with no rows so a line/bar chart doesn't silently
    compress a gap (e.g. 2 attempts 20 days apart) into adjacent, evenly-spaced
    points as if they happened on consecutive days. Only applied when the
    caller supplied BOTH bounds — an unbounded range (either side open) has no
    natural start/end to fill between, so it is returned unfilled."""
    if not date_from or not date_to or date_to < date_from:
        return rows
    if (date_to - date_from).days > _MAX_FILL_DAYS:
        return rows
    by_day = {row[day_key]: row for row in rows}
    filled = []
    day = date_from
    while day <= date_to:
        if day in by_day:
            filled.append(by_day[day])
        else:
            filled.append({day_key: day, **{k: 0 for k in value_keys}})
        day += datetime.timedelta(days=1)
    return filled


def get_executive_metrics(date_from=None, date_to=None):
    """High-level counts for the Executive tab: org size, headcount, verification
    throughput, and payout totals over the selected range."""
    beneficiary_counts = Beneficiary.objects.aggregate(
        total=Count('id'),
        active=Count('id', filter=Q(status=Beneficiary.STATUS_ACTIVE)),
        pending=Count('id', filter=Q(status=Beneficiary.STATUS_PENDING)),
        inactive=Count('id', filter=Q(status=Beneficiary.STATUS_INACTIVE)),
        deceased=Count('id', filter=Q(status=Beneficiary.STATUS_DECEASED)),
    )

    user_counts = CustomUser.objects.aggregate(
        total=Count('id'),
        active=Count('id', filter=Q(is_active=True, account_status=CustomUser.STATUS_ACTIVE)),
        staff=Count('id', filter=Q(role=CustomUser.ROLE_STAFF)),
        admin_level=Count('id', filter=Q(role__in=[CustomUser.ROLE_PRESIDENT, CustomUser.ROLE_ADMIN, CustomUser.ROLE_IT])),
    )

    attempts_qs = _apply_date_range(VerificationAttempt.objects.all(), 'timestamp', date_from, date_to)
    verification_counts = attempts_qs.aggregate(
        total=Count('id'),
        verified=Count('id', filter=Q(decision=VerificationAttempt.DECISION_VERIFIED)),
        manual_review=Count('id', filter=Q(decision=VerificationAttempt.DECISION_MANUAL_REVIEW)),
    )
    total_attempts = verification_counts['total'] or 0
    verified_rate = (
        round(100 * verification_counts['verified'] / total_attempts, 1) if total_attempts else None
    )

    claims_qs = _apply_date_range(
        ClaimRecord.objects.filter(status=ClaimRecord.STATUS_CLAIMED), 'claimed_at', date_from, date_to,
    )
    claim_totals = claims_qs.aggregate(count=Count('id'), amount=Sum('amount'))
    # Current open-queue snapshot, not range-scoped (same convention as
    # get_security_metrics' manual_review_pending) — "how many claims need
    # action right now" doesn't depend on the stat-tile date filter.
    pending_claims = ClaimRecord.objects.filter(status=ClaimRecord.STATUS_PENDING_APPROVAL).count()

    active_event = StipendEvent.get_active_event_now()
    # get_active_event_now() also checks payout_start_time/payout_end_time, so
    # it can be None even when an approved event's DATE window covers today —
    # e.g. a 07:00-20:00 payout window checked at 21:00. Distinguish that case
    # ("no payout scheduled today" would be false) from genuinely no event
    # today, so the Executive tab's empty-state text doesn't overclaim.
    event_today_outside_window = (
        active_event is None
        and StipendEvent.get_active_event_for_date(timezone.localdate()) is not None
    )

    # v2.2.0 Phase 6: beneficiary growth (cumulative registrations by month,
    # last 12 months) and monthly distribution (total PHP released by month,
    # last 12 months) — for the Executive tab's charts. Independent of the
    # date_from/date_to range filter, which is a short-window filter for the
    # stat tiles; a 12-month trend is more useful un-narrowed.
    twelve_months_ago = timezone.now() - timezone.timedelta(days=365)
    monthly_registrations = list(
        Beneficiary.objects
        .filter(created_at__gte=twelve_months_ago)
        .annotate(month=TruncMonth('created_at'))
        .values('month')
        .annotate(n=Count('id'))
        .order_by('month')
    )
    running_total = Beneficiary.objects.filter(created_at__lt=twelve_months_ago).count()
    beneficiary_growth = []
    for row in monthly_registrations:
        running_total += row['n']
        beneficiary_growth.append({'month': row['month'], 'new': row['n'], 'cumulative': running_total})

    monthly_distribution = list(
        ClaimRecord.objects
        .filter(status=ClaimRecord.STATUS_CLAIMED, claimed_at__gte=twelve_months_ago)
        .annotate(month=TruncMonth('claimed_at'))
        .values('month')
        .annotate(total=Sum('amount'), count=Count('id'))
        .order_by('month')
    )

    # v2.2.0 Post-UAT Phase 13: Claim Progress + Payout Completion, scoped to
    # the currently active stipend event (the one decision-makers actually
    # care about right now) — falls back to the nearest upcoming published
    # event when nothing is active today, so the card is never empty between
    # payout days.
    distribution_event = active_event or (
        StipendEvent.objects
        .filter(is_active=True, approval_status=StipendEvent.APPROVAL_APPROVED,
                date__gte=timezone.localdate())
        .order_by('date')
        .first()
    )
    if distribution_event:
        eligible_count = distribution_event.get_eligible_beneficiaries().count()
        claimed_count = ClaimRecord.objects.filter(
            stipend_event=distribution_event, status=ClaimRecord.STATUS_CLAIMED,
        ).count()
        distribution_progress = {
            'event_title': distribution_event.title,
            'expected': eligible_count,
            'claimed': claimed_count,
            'remaining': max(eligible_count - claimed_count, 0),
        }
    else:
        distribution_progress = None

    return {
        'beneficiary_counts': beneficiary_counts,
        'user_counts': user_counts,
        'total_verifications': total_attempts,
        'verified_count': verification_counts['verified'],
        'manual_review_count': verification_counts['manual_review'],
        'verified_rate': verified_rate,
        'claims_count': claim_totals['count'] or 0,
        'claims_amount': claim_totals['amount'] or 0,
        'pending_claims': pending_claims,
        'active_event': active_event,
        'event_today_outside_window': event_today_outside_window,
        'beneficiary_growth': beneficiary_growth,
        'monthly_distribution': monthly_distribution,
        'distribution_progress': distribution_progress,
    }


def get_operational_metrics(date_from=None, date_to=None):
    """Verification throughput/trend and per-staff activity for the Operational tab."""
    attempts_qs = _apply_date_range(VerificationAttempt.objects.all(), 'timestamp', date_from, date_to)

    decision_breakdown = attempts_qs.values('decision').annotate(n=Count('id')).order_by('-n')

    # Top operational summary — the same decision counts as decision_breakdown,
    # just pre-aggregated into named fields (and rates) so the template doesn't
    # have to hunt through a list for one decision's count.
    outcome_counts = attempts_qs.aggregate(
        total=Count('id'),
        verified=Count('id', filter=Q(decision=VerificationAttempt.DECISION_VERIFIED)),
        manual_review=Count('id', filter=Q(decision=VerificationAttempt.DECISION_MANUAL_REVIEW)),
        not_verified_denied=Count('id', filter=Q(decision__in=[
            VerificationAttempt.DECISION_NOT_VERIFIED, VerificationAttempt.DECISION_DENIED,
        ])),
    )
    total_attempts = outcome_counts['total'] or 0
    summary = {
        'total': total_attempts,
        'verified': outcome_counts['verified'],
        'manual_review': outcome_counts['manual_review'],
        'not_verified_denied': outcome_counts['not_verified_denied'],
        'success_rate': round(100 * outcome_counts['verified'] / total_attempts, 1) if total_attempts else None,
        'manual_review_rate': round(100 * outcome_counts['manual_review'] / total_attempts, 1) if total_attempts else None,
    }

    daily_trend = (
        attempts_qs
        .annotate(day=TruncDate('timestamp'))
        .values('day')
        .annotate(
            total=Count('id'),
            verified=Count('id', filter=Q(decision=VerificationAttempt.DECISION_VERIFIED)),
            manual_review=Count('id', filter=Q(decision=VerificationAttempt.DECISION_MANUAL_REVIEW)),
            not_verified_denied=Count('id', filter=Q(decision__in=[
                VerificationAttempt.DECISION_NOT_VERIFIED, VerificationAttempt.DECISION_DENIED,
            ])),
        )
        .order_by('day')
    )

    registrations_qs = _apply_date_range(Beneficiary.objects.all(), 'created_at', date_from, date_to)
    registration_trend = (
        registrations_qs
        .annotate(day=TruncDate('created_at'))
        .values('day')
        .annotate(n=Count('id'))
        .order_by('day')
    )

    daily_trend = _fill_daily_gaps(
        list(daily_trend), date_from, date_to, 'day', ['total', 'verified', 'manual_review', 'not_verified_denied'],
    )
    registration_trend = _fill_daily_gaps(list(registration_trend), date_from, date_to, 'day', ['n'])

    staff_activity = list(
        attempts_qs
        .exclude(performed_by__isnull=True)
        .values('performed_by__id', 'performed_by__username', 'performed_by__first_name', 'performed_by__last_name')
        .annotate(
            n=Count('id'),
            verified=Count('id', filter=Q(decision=VerificationAttempt.DECISION_VERIFIED)),
            manual_review=Count('id', filter=Q(decision=VerificationAttempt.DECISION_MANUAL_REVIEW)),
        )
        .order_by('-n')[:10]
    )

    # Distribution summary by barangay/staff — which areas are actually
    # receiving released stipends over the range, and which staff released
    # them, so an admin can spot barangays falling behind or gauge workload —
    # not a ranking, see fields chosen below.
    claims_qs = _apply_date_range(
        ClaimRecord.objects.filter(status=ClaimRecord.STATUS_CLAIMED), 'claimed_at', date_from, date_to,
    )
    barangay_distribution = list(
        claims_qs
        .values('beneficiary__barangay')
        .annotate(claims_count=Count('id'), total_amount=Sum('amount'))
        .order_by('-total_amount')[:15]
    )

    staff_ids = [row['performed_by__id'] for row in staff_activity]
    claims_released_by_staff = dict(
        claims_qs
        .filter(released_by__id__in=staff_ids)
        .values_list('released_by__id')
        .annotate(n=Count('id'))
    )
    for row in staff_activity:
        row['claims_released'] = claims_released_by_staff.get(row['performed_by__id'], 0)
        row['success_rate'] = round(100 * row['verified'] / row['n'], 1) if row['n'] else None

    return {
        'decision_breakdown': list(decision_breakdown),
        'summary': summary,
        'daily_trend': list(daily_trend),
        'registration_trend': list(registration_trend),
        'staff_activity': staff_activity,
        'barangay_distribution': barangay_distribution,
    }


def get_security_metrics(date_from=None, date_to=None):
    """Security-relevant counts for the Security tab. Mirrors (but does not
    duplicate) the detail queries in report_suspicious_attempts — this tab
    links out to that report for drill-down.

    date_from/date_to scope `counts` (AuditLog-based) and
    `high_attempt_beneficiaries` (VerificationAttempt-based) only.
    `fraud_risk_counts` and `review_cases['fraud_alerts']` come from
    fraud_signals.py, where each of the 5 signal functions uses its own fixed
    trailing lookback window (FRAUD_*_WINDOW_DAYS/HOURS in settings.py) measured
    from now — they intentionally ignore date_from/date_to, since "give me
    unusual-volume alerts from March 1-15" doesn't make sense for a rule whose
    whole premise is a trailing window from the present moment. `manual_review`
    (in review_cases) and `duplicate_face`/`representative_review` are current
    open-queue snapshots, not range- or window-scoped at all."""
    audit_qs = _apply_date_range(AuditLog.objects.all(), 'timestamp', date_from, date_to)

    counts = audit_qs.aggregate(
        failed_logins=Count('id', filter=Q(action=AuditLog.ACTION_LOGIN_FAILED)),
        duplicate_faces=Count('id', filter=Q(action=AuditLog.ACTION_DUPLICATE_FACE)),
        duplicate_payout_attempts=Count('id', filter=Q(action=AuditLog.ACTION_DUPLICATE_PAYOUT_ATTEMPT)),
        payout_overrides=Count('id', filter=Q(action=AuditLog.ACTION_PAYOUT_OVERRIDE)),
        config_changes=Count('id', filter=Q(action=AuditLog.ACTION_CONFIG_CHANGE)),
    )

    attempts_qs = _apply_date_range(VerificationAttempt.objects.all(), 'timestamp', date_from, date_to)
    # Distinct from `failed_logins` above (account-login failures) — this is
    # the biometric decision itself coming back negative.
    failed_verifications = attempts_qs.filter(
        decision__in=[VerificationAttempt.DECISION_NOT_VERIFIED, VerificationAttempt.DECISION_DENIED],
    ).count()

    # Security Events Over Time — same four range-scoped counts as `counts`/
    # `failed_verifications` above, just bucketed by day for a trend view.
    # Only built when both date bounds are supplied (mirrors _fill_daily_gaps'
    # own bounded-range requirement) so an unbounded/all-time selection never
    # triggers an unbounded day-by-day query.
    security_events_trend = []
    if date_from and date_to:
        by_day = {}
        action_field_map = {
            AuditLog.ACTION_LOGIN_FAILED: 'failed_logins',
            AuditLog.ACTION_DUPLICATE_FACE: 'duplicate_faces',
            AuditLog.ACTION_PAYOUT_OVERRIDE: 'payout_overrides',
        }
        audit_daily = (
            audit_qs
            .filter(action__in=action_field_map.keys())
            .annotate(day=TruncDate('timestamp'))
            .values('day', 'action')
            .annotate(n=Count('id'))
        )
        for row in audit_daily:
            field = action_field_map[row['action']]
            entry = by_day.setdefault(row['day'], {
                'day': row['day'], 'failed_logins': 0, 'failed_verifications': 0,
                'duplicate_faces': 0, 'payout_overrides': 0,
            })
            entry[field] = row['n']

        failed_verif_daily = (
            attempts_qs
            .filter(decision__in=[VerificationAttempt.DECISION_NOT_VERIFIED, VerificationAttempt.DECISION_DENIED])
            .annotate(day=TruncDate('timestamp'))
            .values('day')
            .annotate(n=Count('id'))
        )
        for row in failed_verif_daily:
            entry = by_day.setdefault(row['day'], {
                'day': row['day'], 'failed_logins': 0, 'failed_verifications': 0,
                'duplicate_faces': 0, 'payout_overrides': 0,
            })
            entry['failed_verifications'] = row['n']

        security_events_trend = _fill_daily_gaps(
            sorted(by_day.values(), key=lambda r: r['day']), date_from, date_to, 'day',
            ['failed_logins', 'failed_verifications', 'duplicate_faces', 'payout_overrides'],
        )

    high_attempt_beneficiaries = (
        attempts_qs
        .values('beneficiary')
        .annotate(n=Count('id'))
        .filter(n__gte=3)
        .count()
    )
    manual_review_pending = VerificationAttempt.objects.filter(
        decision=VerificationAttempt.DECISION_MANUAL_REVIEW,
    ).count()

    from . import fraud_signals as _fraud_signals
    fraud_rows = (
        _fraud_signals.repeated_failures()
        + _fraud_signals.anomalous_staff_volume()
        + _fraud_signals.mass_edit_detection()
        + _fraud_signals.repeated_login_failures()
        + _fraud_signals.payout_anomalies()
    )
    fraud_risk_counts = {
        _fraud_signals.RISK_HIGH: sum(1 for r in fraud_rows if r['risk_level'] == _fraud_signals.RISK_HIGH),
        _fraud_signals.RISK_MEDIUM: sum(1 for r in fraud_rows if r['risk_level'] == _fraud_signals.RISK_MEDIUM),
        _fraud_signals.RISK_LOW: sum(1 for r in fraud_rows if r['risk_level'] == _fraud_signals.RISK_LOW),
    }

    # v2.2.0 Post-UAT Phase 13: Review/Security Cases — the four
    # outstanding-review categories an admin actually has to act on, in one
    # place, instead of scattered across separate queue pages. 'fraud_alerts'
    # counts only MEDIUM+HIGH rows, matching sync_fraud_notifications()'s own
    # rule that LOW-risk rows are not worth alerting on — counting LOW here
    # would present a routine "at/near threshold" row as an open case needing
    # action alongside genuinely elevated ones.
    from beneficiaries.models import Beneficiary, SharedRepresentativeReview
    review_cases = {
        'duplicate_face': Beneficiary.objects.filter(duplicate_review_required=True).count(),
        'manual_review': manual_review_pending,
        'representative_review': SharedRepresentativeReview.objects.filter(
            status=SharedRepresentativeReview.STATUS_PENDING,
        ).count(),
        'fraud_alerts': fraud_risk_counts[_fraud_signals.RISK_HIGH] + fraud_risk_counts[_fraud_signals.RISK_MEDIUM],
    }

    return {
        **counts,
        'failed_verifications': failed_verifications,
        'high_attempt_beneficiaries': high_attempt_beneficiaries,
        'manual_review_pending': manual_review_pending,
        'fraud_risk_counts': fraud_risk_counts,
        'review_cases': review_cases,
        'security_events_trend': security_events_trend,
    }
