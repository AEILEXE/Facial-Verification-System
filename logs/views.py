import logging
import traceback
from urllib.parse import urlencode

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.views.decorators.http import require_POST

from .models import AuditLog, Notification
from verification.models import VerificationAttempt
from accounts.models import CustomUser

logger = logging.getLogger('logs')

# Audit Log role filter — same choices as CustomUser.ROLE_CHOICES but with the
# 'it' role shown as CustomUser.get_role_display() would render it, so the
# filter dropdown never leaks the raw legacy "IT" label.
_ROLE_FILTER_CHOICES = [
    (value, CustomUser.TECHNICAL_ADMIN_LABEL if value == CustomUser.ROLE_IT else label)
    for value, label in CustomUser.ROLE_CHOICES
]


@login_required
def audit_log_list(request):
    # Audit logs contain sensitive fields (login failures with usernames,
    # admin override reasons, beneficiary IDs). Restrict to management roles.
    if not request.user.is_admin:
        messages.error(request, 'Admin access required.')
        return redirect('beneficiaries:dashboard')

    # Issue 8: extended filters + CSV export.
    from datetime import datetime
    query_error = None
    logs_page = None
    action_filter = ''
    user_filter = ''
    date_from = ''
    date_to = ''
    decision_filter = ''
    beneficiary_filter = ''
    ip_filter = ''
    search_filter = ''
    role_filter = ''
    target_type_filter = ''
    export_fmt = request.GET.get('export', '').strip().lower()

    try:
        queryset = AuditLog.objects.select_related('user').order_by('-timestamp')
        target_type_choices = list(
            AuditLog.objects.exclude(target_type='').exclude(target_type__isnull=True)
            .values_list('target_type', flat=True).distinct().order_by('target_type')
        )

        # Filters — validate action against known choices to prevent injection
        action_filter = request.GET.get('action', '').strip()
        user_filter   = request.GET.get('user', '').strip()
        date_from     = request.GET.get('date_from', '').strip()
        date_to       = request.GET.get('date_to', '').strip()
        decision_filter = request.GET.get('decision', '').strip()
        beneficiary_filter = request.GET.get('beneficiary_id', '').strip()
        ip_filter     = request.GET.get('ip', '').strip()
        search_filter = request.GET.get('q', '').strip()
        role_filter   = request.GET.get('role', '').strip()
        target_type_filter = request.GET.get('target_type', '').strip()
        valid_actions = {a for a, _ in AuditLog.ACTION_CHOICES}
        if action_filter and action_filter not in valid_actions:
            action_filter = ''
        valid_roles = {r for r, _ in CustomUser.ROLE_CHOICES}
        if role_filter and role_filter not in valid_roles:
            role_filter = ''
        if target_type_filter and target_type_filter not in target_type_choices:
            target_type_filter = ''

        if action_filter:
            queryset = queryset.filter(action=action_filter)
        if user_filter:
            queryset = queryset.filter(user__username__icontains=user_filter[:100])
        if role_filter:
            queryset = queryset.filter(user__role=role_filter)
        if target_type_filter:
            queryset = queryset.filter(target_type=target_type_filter)
        if date_from:
            try:
                df = datetime.strptime(date_from, '%Y-%m-%d').date()
                queryset = queryset.filter(timestamp__date__gte=df)
            except ValueError:
                date_from = ''
        if date_to:
            try:
                dt = datetime.strptime(date_to, '%Y-%m-%d').date()
                queryset = queryset.filter(timestamp__date__lte=dt)
            except ValueError:
                date_to = ''
        if ip_filter:
            queryset = queryset.filter(ip_address__icontains=ip_filter[:50])
        if beneficiary_filter:
            queryset = queryset.filter(details__beneficiary_id__icontains=beneficiary_filter[:50])
        if decision_filter:
            queryset = queryset.filter(details__decision=decision_filter)
        if search_filter:
            # JSONField icontains works on Postgres; fall back to target_id match
            queryset = (
                queryset.filter(target_id__icontains=search_filter[:200])
                | queryset.filter(details__icontains=search_filter[:200])
            )

        if export_fmt == 'csv':
            import csv
            from django.http import HttpResponse
            from django.utils import timezone as _tz
            from fans.report_export import sanitize_export_cell
            response = HttpResponse(content_type='text/csv')
            response['Content-Disposition'] = (
                'attachment; filename="fansc-audit-logs.csv"'
            )
            writer = csv.writer(response)
            writer.writerow([
                'Timestamp', 'User', 'Action', 'Target Type', 'Target ID',
                'IP Address', 'Decision', 'Beneficiary ID', 'FaceNet Score',
                'Threshold', 'Liveness Passed', 'Anti-Spoof Score',
                'Reason / Excerpt',
            ])
            for log in queryset[:10000]:
                d = log.details or {}
                writer.writerow([sanitize_export_cell(v) for v in [
                    log.timestamp.astimezone().strftime('%Y-%m-%d %H:%M:%S'),
                    log.user.username if log.user else '',
                    log.action,
                    log.target_type,
                    log.target_id,
                    log.ip_address or '',
                    d.get('decision', ''),
                    d.get('beneficiary_id', ''),
                    d.get('score', d.get('facenet_score', '')),
                    d.get('threshold', ''),
                    d.get('liveness_passed', ''),
                    d.get('anti_spoof_score', ''),
                    (d.get('reason') or d.get('block_reason') or
                     d.get('outcome') or '')[:280],
                ]])
            AuditLog.log(
                action=AuditLog.ACTION_REPORT_EXPORT,
                user=request.user,
                details={'report': 'audit_log', 'format': 'csv'},
                request=request,
            )
            return response

        paginator = Paginator(queryset, 50)
        try:
            page_num = int(request.GET.get('page', 1))
        except (ValueError, TypeError):
            page_num = 1
        logs_page = paginator.get_page(page_num)
    except Exception as exc:
        logger.exception('audit_log_list query failed (%s): %s', type(exc).__name__, exc)
        query_error = f'{type(exc).__name__}: {exc}'

    if query_error:
        return render(request, 'logs/audit_logs.html', {
            'logs': None,
            'action_filter': action_filter,
            'user_filter': user_filter,
            'role_filter': role_filter,
            'target_type_filter': target_type_filter,
            'action_choices': AuditLog.ACTION_CHOICES,
            'role_choices': _ROLE_FILTER_CHOICES,
            'target_type_choices': [],
            'load_error': query_error,
        })

    return render(request, 'logs/audit_logs.html', {
        'logs': logs_page,
        'action_filter': action_filter,
        'user_filter': user_filter,
        'date_from': date_from,
        'date_to': date_to,
        'decision_filter': decision_filter,
        'beneficiary_filter': beneficiary_filter,
        'ip_filter': ip_filter,
        'search_filter': search_filter,
        'role_filter': role_filter,
        'target_type_filter': target_type_filter,
        'action_choices': AuditLog.ACTION_CHOICES,
        'role_choices': _ROLE_FILTER_CHOICES,
        'target_type_choices': target_type_choices,
        'load_error': None,
    })


@login_required
def verification_log_list(request):
    from datetime import datetime

    if not request.user.is_admin:
        # Staff see only their own
        attempts = VerificationAttempt.objects.filter(
            performed_by=request.user
        ).select_related('beneficiary', 'performed_by')
    else:
        attempts = VerificationAttempt.objects.select_related('beneficiary', 'performed_by')

    # Filters
    decision_filter = request.GET.get('decision', '').strip()
    beneficiary_filter = request.GET.get('beneficiary', '').strip()
    performed_by_filter = request.GET.get('performed_by', '').strip()
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()
    date_range_error = None

    if decision_filter:
        attempts = attempts.filter(decision=decision_filter)
    if beneficiary_filter:
        needle = beneficiary_filter[:100]
        attempts = attempts.filter(
            Q(beneficiary__first_name__icontains=needle)
            | Q(beneficiary__last_name__icontains=needle)
            | Q(beneficiary__beneficiary_id__icontains=needle)
        )
    if performed_by_filter:
        attempts = attempts.filter(performed_by__username__icontains=performed_by_filter[:100])

    # Date filtering uses VerificationAttempt.timestamp — the authoritative
    # record of when the verification attempt occurred. With USE_TZ=True and
    # TIME_ZONE='Asia/Manila', the __date lookup converts to the application
    # timezone before truncating, so boundaries line up with what staff see
    # on screen. Both bounds are inclusive of the whole named day.
    parsed_from = parsed_to = None
    if date_from:
        try:
            parsed_from = datetime.strptime(date_from, '%Y-%m-%d').date()
        except ValueError:
            date_from = ''
    if date_to:
        try:
            parsed_to = datetime.strptime(date_to, '%Y-%m-%d').date()
        except ValueError:
            date_to = ''

    if parsed_from and parsed_to and parsed_from > parsed_to:
        date_range_error = 'Date From must not be after Date To.'
    else:
        if parsed_from:
            attempts = attempts.filter(timestamp__date__gte=parsed_from)
        if parsed_to:
            attempts = attempts.filter(timestamp__date__lte=parsed_to)

    attempts = attempts.order_by('-timestamp')
    paginator = Paginator(attempts, 50)
    page = request.GET.get('page', 1)
    attempts_page = paginator.get_page(page)

    # Preserve the active filters across pagination links without repeating
    # them by hand in the template for every ?page= link.
    filter_params = {}
    if decision_filter:
        filter_params['decision'] = decision_filter
    if beneficiary_filter:
        filter_params['beneficiary'] = beneficiary_filter
    if performed_by_filter:
        filter_params['performed_by'] = performed_by_filter
    if date_from:
        filter_params['date_from'] = date_from
    if date_to:
        filter_params['date_to'] = date_to
    filter_querystring = urlencode(filter_params)

    return render(request, 'logs/verification_logs.html', {
        'attempts': attempts_page,
        'decision_filter': decision_filter,
        'beneficiary_filter': beneficiary_filter,
        'performed_by_filter': performed_by_filter,
        'date_from': date_from,
        'date_to': date_to,
        'date_range_error': date_range_error,
        'decision_choices': VerificationAttempt.DECISION_CHOICES,
        'filter_querystring': filter_querystring,
    })


# ─── Notification Center ─────────────────────────────────────────────────────

@login_required
def notification_center(request):
    """Full notification inbox for the current user — paginated, newest first."""
    notifications = Notification.objects.filter(recipient=request.user)
    paginator = Paginator(notifications, 30)
    page = request.GET.get('page', 1)
    notifications_page = paginator.get_page(page)

    return render(request, 'logs/notification_center.html', {
        'notifications': notifications_page,
    })


@login_required
def notification_open(request, pk):
    """Mark one notification read and redirect to its target URL — this is
    what a click on a notification (bell dropdown or center) does."""
    notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
    notification.mark_read()
    if notification.url:
        return redirect(notification.url)
    return redirect('logs:notification_center')


@login_required
@require_POST
def notification_mark_all_read(request):
    from django.utils import timezone as _tz
    Notification.objects.filter(recipient=request.user, is_read=False).update(
        is_read=True, read_at=_tz.now(),
    )
    messages.success(request, 'All notifications marked as read.')
    next_url = request.POST.get('next') or 'logs:notification_center'
    return redirect(next_url)
