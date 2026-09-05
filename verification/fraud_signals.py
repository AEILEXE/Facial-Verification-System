"""
Fraud Detection Intelligence — rule-based FLAG signals, computed on demand
from existing tables. No new persistent model, no dismiss/acknowledge
workflow: this is a report an admin reviews, not a stateful queue. Nothing
here writes to any other model or account status — see fraud_signals_report
(verification/views.py) and the corresponding no-write regression tests in
verification/tests.py.

Thresholds are settings.py constants (see fans/settings.py, FRAUD_* block),
.env-overridable so an admin can tune sensitivity without a redeploy.

v2.2.0 Phase 7: risk is expressed as an explicit 0-100 point score (not a
black-box ML score — every point is directly explainable as "how far past
the configured threshold is this"), banded LOW (0-30) / MEDIUM (31-70) /
HIGH (71-100). At exactly the threshold a signal scores 30 (the boundary
into "worth a look"); by 3.33x the threshold it caps at 100. HIGH is never
an instruction to block anything automatically — every signal here only
ever produces a flag for human review, never an automatic account action.
"""
from django.conf import settings
from django.db.models import Count, Q
from django.utils import timezone

from .models import VerificationAttempt
from logs.models import AuditLog

RISK_LOW = 'LOW'
RISK_MEDIUM = 'MEDIUM'
RISK_HIGH = 'HIGH'


def _risk_score(count, threshold):
    """0-100 point score: count==threshold -> 30, scales linearly, caps at 100."""
    if threshold <= 0:
        return 100
    return min(100, round((count / threshold) * 30))


def _risk_band(score):
    """LOW 0-30 / MEDIUM 31-70 / HIGH 71-100 — the exact bands requested."""
    if score > 70:
        return RISK_HIGH
    if score > 30:
        return RISK_MEDIUM
    return RISK_LOW


def _score_and_band(count, threshold):
    score = _risk_score(count, threshold)
    return score, _risk_band(score)


def repeated_failures():
    """Beneficiaries with FRAUD_REPEATED_FAILURE_THRESHOLD+ failed/denied
    verification attempts in the trailing FRAUD_REPEATED_FAILURE_WINDOW_DAYS days."""
    threshold = settings.FRAUD_REPEATED_FAILURE_THRESHOLD
    window_start = timezone.now() - timezone.timedelta(days=settings.FRAUD_REPEATED_FAILURE_WINDOW_DAYS)
    rows = list(
        VerificationAttempt.objects
        .filter(timestamp__gte=window_start)
        .values(
            'beneficiary__id', 'beneficiary__beneficiary_id',
            'beneficiary__first_name', 'beneficiary__last_name',
        )
        .annotate(
            n_failed=Count(
                'id',
                filter=Q(decision__in=[VerificationAttempt.DECISION_NOT_VERIFIED, VerificationAttempt.DECISION_DENIED]),
            ),
        )
        .filter(n_failed__gte=threshold)
        .order_by('-n_failed')
    )
    for row in rows:
        row['risk_score'], row['risk_level'] = _score_and_band(row['n_failed'], threshold)
    return rows


def anomalous_staff_volume():
    """Staff with FRAUD_STAFF_VOLUME_THRESHOLD+ verification attempts in the
    trailing FRAUD_STAFF_VOLUME_WINDOW_HOURS hours."""
    threshold = settings.FRAUD_STAFF_VOLUME_THRESHOLD
    window_start = timezone.now() - timezone.timedelta(hours=settings.FRAUD_STAFF_VOLUME_WINDOW_HOURS)
    rows = list(
        VerificationAttempt.objects
        .filter(timestamp__gte=window_start)
        .exclude(performed_by__isnull=True)
        .values('performed_by__id', 'performed_by__username', 'performed_by__first_name', 'performed_by__last_name')
        .annotate(n=Count('id'))
        .filter(n__gte=threshold)
        .order_by('-n')
    )
    for row in rows:
        row['risk_score'], row['risk_level'] = _score_and_band(row['n'], threshold)
    return rows


_MASS_EDIT_ACTIONS = [
    AuditLog.ACTION_USER_UPDATE,
    AuditLog.ACTION_PAYOUT_OVERRIDE,
    AuditLog.ACTION_CONFIG_CHANGE,
    AuditLog.ACTION_RECORD_APPROVED,
]


def mass_edit_detection():
    """Admins with FRAUD_MASS_EDIT_THRESHOLD+ user/payout/config/approval edits
    in the trailing FRAUD_MASS_EDIT_WINDOW_HOURS hours."""
    threshold = settings.FRAUD_MASS_EDIT_THRESHOLD
    window_start = timezone.now() - timezone.timedelta(hours=settings.FRAUD_MASS_EDIT_WINDOW_HOURS)
    rows = list(
        AuditLog.objects
        .filter(timestamp__gte=window_start, action__in=_MASS_EDIT_ACTIONS)
        .exclude(user__isnull=True)
        .values('user__id', 'user__username', 'user__first_name', 'user__last_name')
        .annotate(n=Count('id'))
        .filter(n__gte=threshold)
        .order_by('-n')
    )
    for row in rows:
        row['risk_score'], row['risk_level'] = _score_and_band(row['n'], threshold)
    return rows


def repeated_login_failures():
    """IPs with FRAUD_LOGIN_FAILURE_THRESHOLD+ failed login attempts in the
    trailing FRAUD_LOGIN_FAILURE_WINDOW_HOURS hours. Complements the
    real-time login lockout (accounts.views._record_failure, which already
    notifies once per lockout) with a reviewable trend — an IP that keeps
    getting locked out and retrying is worth a human look even though each
    individual lockout was already handled."""
    threshold = settings.FRAUD_LOGIN_FAILURE_THRESHOLD
    window_start = timezone.now() - timezone.timedelta(hours=settings.FRAUD_LOGIN_FAILURE_WINDOW_HOURS)
    rows = list(
        AuditLog.objects
        .filter(timestamp__gte=window_start, action=AuditLog.ACTION_LOGIN_FAILED)
        .exclude(ip_address__isnull=True)
        .values('ip_address')
        .annotate(n=Count('id'))
        .filter(n__gte=threshold)
        .order_by('-n')
    )
    for row in rows:
        row['risk_score'], row['risk_level'] = _score_and_band(row['n'], threshold)
    return rows


_PAYOUT_ANOMALY_ACTIONS = [
    AuditLog.ACTION_PAYOUT_OVERRIDE,
    AuditLog.ACTION_PAYOUT_CANCELLED,
    AuditLog.ACTION_PAYOUT_FAILED,
    AuditLog.ACTION_DUPLICATE_PAYOUT_ATTEMPT,
    AuditLog.ACTION_PAYOUT_FALLBACK_RELEASED,
]


def payout_anomalies():
    """Staff/admins with FRAUD_PAYOUT_ANOMALY_THRESHOLD+ payout overrides,
    cancellations, failures, blocked duplicate attempts, or fallback releases
    in the trailing FRAUD_PAYOUT_ANOMALY_WINDOW_HOURS hours — an unusually
    high rate of any of these on released stipend money is worth review,
    even though every individual action was already independently
    audit-logged and (for overrides) reason-required at the time."""
    threshold = settings.FRAUD_PAYOUT_ANOMALY_THRESHOLD
    window_start = timezone.now() - timezone.timedelta(hours=settings.FRAUD_PAYOUT_ANOMALY_WINDOW_HOURS)
    rows = list(
        AuditLog.objects
        .filter(timestamp__gte=window_start, action__in=_PAYOUT_ANOMALY_ACTIONS)
        .exclude(user__isnull=True)
        .values('user__id', 'user__username', 'user__first_name', 'user__last_name')
        .annotate(n=Count('id'))
        .filter(n__gte=threshold)
        .order_by('-n')
    )
    for row in rows:
        row['risk_score'], row['risk_level'] = _score_and_band(row['n'], threshold)
    return rows


def sync_fraud_notifications():
    """
    Best-effort, idempotent: create a Notification for any MEDIUM/HIGH-risk
    signal row not already notified at that risk band. Called opportunistically
    from fraud_signals_report and the Security analytics tab (same pattern as
    accounts.management.commands.sync_backup_audit) — there is no background
    scheduler in this app, so signals are (re)computed on page load rather than
    on a timer. LOW-risk rows are intentionally not notified to avoid alert
    fatigue; they still show on the report page. A beneficiary/staff/admin
    already notified at MEDIUM is notified again only if they climb to HIGH
    (dedupe_key includes the risk band, not the raw count/score).
    """
    from django.urls import reverse
    from logs.models import Notification
    from logs.notifications import notify_admins

    created = []

    for row in repeated_failures():
        if row['risk_level'] == RISK_LOW:
            continue
        bid = row['beneficiary__id']
        created += notify_admins(
            category=Notification.CATEGORY_FRAUD_ALERT,
            title=f'{row["risk_level"]} risk ({row["risk_score"]}/100): repeated verification failures',
            message=f'{row["beneficiary__first_name"]} {row["beneficiary__last_name"]} — {row["n_failed"]} failed attempts.',
            url=reverse('verification:fraud_signals_report'),
            dedupe_key=f'fraud_repeated_failures:{bid}:{row["risk_level"]}',
            priority=row['risk_level'],
        )

    for row in anomalous_staff_volume():
        if row['risk_level'] == RISK_LOW:
            continue
        uid = row['performed_by__id']
        created += notify_admins(
            category=Notification.CATEGORY_FRAUD_ALERT,
            title=f'{row["risk_level"]} risk ({row["risk_score"]}/100): unusual staff verification volume',
            message=f'{row["performed_by__first_name"]} {row["performed_by__last_name"]} — {row["n"]} attempts.',
            url=reverse('verification:fraud_signals_report'),
            dedupe_key=f'fraud_staff_volume:{uid}:{row["risk_level"]}',
            priority=row['risk_level'],
        )

    for row in mass_edit_detection():
        if row['risk_level'] == RISK_LOW:
            continue
        uid = row['user__id']
        created += notify_admins(
            category=Notification.CATEGORY_FRAUD_ALERT,
            title=f'{row["risk_level"]} risk ({row["risk_score"]}/100): admin mass-edit activity',
            message=f'{row["user__first_name"]} {row["user__last_name"]} — {row["n"]} edits.',
            url=reverse('verification:fraud_signals_report'),
            dedupe_key=f'fraud_mass_edit:{uid}:{row["risk_level"]}',
            priority=row['risk_level'],
        )

    for row in repeated_login_failures():
        if row['risk_level'] == RISK_LOW:
            continue
        ip = row['ip_address']
        created += notify_admins(
            category=Notification.CATEGORY_SECURITY_ALERT,
            title=f'{row["risk_level"]} risk ({row["risk_score"]}/100): repeated login failures',
            message=f'{ip} — {row["n"]} failed login attempts.',
            url=reverse('verification:fraud_signals_report'),
            dedupe_key=f'fraud_login_failures:{ip}:{row["risk_level"]}',
            priority=row['risk_level'],
        )

    for row in payout_anomalies():
        if row['risk_level'] == RISK_LOW:
            continue
        uid = row['user__id']
        created += notify_admins(
            category=Notification.CATEGORY_FRAUD_ALERT,
            title=f'{row["risk_level"]} risk ({row["risk_score"]}/100): unusual payout activity',
            message=f'{row["user__first_name"]} {row["user__last_name"]} — {row["n"]} overrides/cancellations/failures.',
            url=reverse('verification:fraud_signals_report'),
            dedupe_key=f'fraud_payout_anomaly:{uid}:{row["risk_level"]}',
            priority=row['risk_level'],
        )

    return created
