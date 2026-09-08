import json
import base64
import uuid
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods, require_POST
from django.views.decorators.cache import never_cache
from django.http import JsonResponse
from django.utils import timezone

from .models import Beneficiary, Representative
from .forms import BeneficiaryInfoForm, BeneficiaryEditForm, RepresentativeForm, ConsentForm
from verification.models import FaceEmbedding
from verification.face_utils import process_face_for_registration, check_duplicate_face
from django.urls import reverse
from logs.models import AuditLog, Notification
from logs.notifications import notify_admins
from accounts.models import CustomUser
from accounts.forms import UserCreateForm, UserUpdateForm


@login_required
@never_cache
def dashboard(request):
    total_beneficiaries = Beneficiary.objects.count()
    active_beneficiaries = Beneficiary.objects.filter(status='active').count()
    pending_beneficiaries = Beneficiary.objects.filter(status='pending').count()

    from verification.models import VerificationAttempt, StipendEvent
    today = timezone.localdate()

    # Staff see only their own verification stats so the dashboard cards stay
    # consistent with what they see in the Verification Logs page (which also
    # filters by performed_by=request.user for non-admin users).
    if request.user.is_staff_member:
        va_qs = VerificationAttempt.objects.filter(performed_by=request.user)
    else:
        va_qs = VerificationAttempt.objects.all()

    verifications_today = va_qs.filter(timestamp__date=today).count()
    verified_today = va_qs.filter(timestamp__date=today, decision='verified').count()

    # Pending manual review is an admin action queue — only compute it for
    # roles that can actually access the review page.
    if request.user.is_admin:
        manual_review_pending = VerificationAttempt.objects.filter(
            decision=VerificationAttempt.DECISION_MANUAL_REVIEW,
            overridden=False,
        ).count()
        from .models import SharedRepresentativeReview
        shared_rep_review_pending = SharedRepresentativeReview.objects.filter(
            status=SharedRepresentativeReview.STATUS_PENDING,
        ).count()
        duplicate_review_pending = Beneficiary.objects.filter(
            duplicate_review_required=True, status=Beneficiary.STATUS_PENDING,
        ).count()
        # The dashboard is the page every admin/President lands on first —
        # approval reminders (e.g. a payout stuck pending President approval)
        # must not depend on someone specifically visiting the Manual Review
        # queue to surface. Idempotent/deduped, safe to call on every load.
        try:
            from logs.notifications import sync_approval_reminders
            sync_approval_reminders()
        except Exception:
            import logging
            logging.getLogger(__name__).exception('sync_approval_reminders failed (non-fatal)')
    else:
        manual_review_pending = None
        shared_rep_review_pending = None
        duplicate_review_pending = None

    recent_logs = AuditLog.objects.select_related('user').order_by('-timestamp')[:5]

    # Upcoming stipend events (next 60 days). v2.2.0 Phase 2 fix: a schedule
    # still pending_approval must NEVER appear on the dashboard as a confirmed
    # "Next Payout" — only approval_status=APPROVED (published) schedules are
    # shown here. Pending ones are surfaced separately, on Payout Schedule's
    # own "Pending President Approval" panel, never implied as confirmed.
    from datetime import timedelta
    upcoming_events = StipendEvent.objects.filter(
        is_active=True, approval_status=StipendEvent.APPROVAL_APPROVED,
        date__gte=today, date__lte=today + timedelta(days=60),
    ).order_by('date')[:5]

    pending_approval_events_count = StipendEvent.objects.filter(
        is_active=True, approval_status=StipendEvent.APPROVAL_PENDING,
    ).count() if request.user.is_admin else None

    # Active event today
    active_event = StipendEvent.get_active_event_for_date(today)
    next_event = upcoming_events.first()

    # v2.1.19 UX pass (section 12) — "Current Distribution Event" panel.
    # current_event is whichever of active_event/next_event we already have
    # (no extra query); claiming_status is the explicit OPEN/UPCOMING/
    # INACTIVE label the redesigned dashboard needs instead of implying
    # status only through card color.
    from verification.models import ClaimRecord
    current_event = active_event or next_event
    distribution_progress = None
    if current_event:
        if active_event and current_event.id == active_event.id:
            # Same time-window check the Verify page enforces (get_open_events_now
            # / is_within_time_window) — without it, an event active for today's
            # date but outside its daily payout_start_time/payout_end_time window
            # showed "OPEN" here while Verify correctly refused to start a claim.
            if active_event.is_within_time_window(timezone.localtime().time()):
                claiming_status = 'OPEN'
            else:
                claiming_status = 'SCHEDULED'
        elif current_event.approval_status != StipendEvent.APPROVAL_APPROVED:
            claiming_status = 'PENDING APPROVAL'
        elif not current_event.is_active:
            claiming_status = 'INACTIVE'
        else:
            claiming_status = 'UPCOMING'

        eligible_count = current_event.get_eligible_beneficiaries().count()
        claimed_count = ClaimRecord.objects.filter(
            stipend_event=current_event, status=ClaimRecord.STATUS_CLAIMED,
        ).count()
        distribution_progress = {
            'eligible': eligible_count,
            'claimed': claimed_count,
            'remaining': max(eligible_count - claimed_count, 0),
            'pct': round((claimed_count / eligible_count) * 100) if eligible_count else 0,
        }
    else:
        claiming_status = 'INACTIVE'

    from django.db.models import Sum
    claims_released_today_qs = ClaimRecord.objects.filter(
        status=ClaimRecord.STATUS_CLAIMED, released_at__date=today,
    )
    claims_released_today = claims_released_today_qs.count()
    amount_released_today = claims_released_today_qs.aggregate(total=Sum('amount'))['total'] or 0

    # One short operational trend, not a growth chart — verification attempts
    # and released claims per day for the last 7 days (section 12: "ONE
    # USEFUL TREND", explicitly not a 12-month beneficiary-growth graph).
    from datetime import timedelta as _td
    trend_labels, trend_verifications, trend_claims = [], [], []
    for i in range(6, -1, -1):
        d = today - _td(days=i)
        trend_labels.append(d.strftime('%b %d'))
        trend_verifications.append(va_qs.filter(timestamp__date=d).count())
        trend_claims.append(
            ClaimRecord.objects.filter(status=ClaimRecord.STATUS_CLAIMED, released_at__date=d).count()
        )
    dashboard_trend = {'labels': trend_labels, 'verifications': trend_verifications, 'claims': trend_claims}

    # Beneficiary status snapshot for the Dashboard chart — reuses the same
    # aggregate the Analytics Executive tab already computes, so the two
    # numbers can never drift apart.
    from verification.analytics import get_executive_metrics
    beneficiary_status_counts = get_executive_metrics()['beneficiary_counts']
    chart_data = {
        'beneficiary_status': {
            'active': beneficiary_status_counts['active'],
            'pending': beneficiary_status_counts['pending'],
            'inactive': beneficiary_status_counts['inactive'],
            'deceased': beneficiary_status_counts['deceased'],
        },
        'dashboard_trend': dashboard_trend,
    }

    needs_president_bootstrap = request.user.is_admin_it and not CustomUser.active_president_exists()

    return render(request, 'dashboard/index.html', {
        'server_time': timezone.localtime(),
        'chart_data': chart_data,
        'total_beneficiaries': total_beneficiaries,
        'active_beneficiaries': active_beneficiaries,
        'pending_beneficiaries': pending_beneficiaries,
        'pending_registrations_count': pending_beneficiaries,
        'verifications_today': verifications_today,
        'verified_today': verified_today,
        'manual_review_pending': manual_review_pending,
        'shared_rep_review_pending': shared_rep_review_pending,
        'duplicate_review_pending': duplicate_review_pending,
        'recent_logs': recent_logs,
        'upcoming_events': upcoming_events,
        'active_event': active_event,
        'next_event': next_event,
        'current_event': current_event,
        'claiming_status': claiming_status,
        'distribution_progress': distribution_progress,
        'claims_released_today': claims_released_today,
        'amount_released_today': amount_released_today,
        'pending_approval_events_count': pending_approval_events_count,
        'needs_president_bootstrap': needs_president_bootstrap,
    })


@login_required
def beneficiary_list(request):
    from django.core.paginator import Paginator
    query = request.GET.get('q', '')
    status_filter = request.GET.get('status', '')
    barangay_filter = request.GET.get('barangay', '').strip()
    beneficiaries = Beneficiary.objects.all()
    if query:
        beneficiaries = (
            beneficiaries.filter(last_name__icontains=query) |
            beneficiaries.filter(first_name__icontains=query) |
            beneficiaries.filter(beneficiary_id__icontains=query) |
            beneficiaries.filter(senior_citizen_id__icontains=query)
        ).distinct()
    if status_filter:
        beneficiaries = beneficiaries.filter(status=status_filter)
    if barangay_filter:
        beneficiaries = beneficiaries.filter(barangay=barangay_filter)
    beneficiaries = beneficiaries.order_by('last_name', 'first_name')

    # Same field the Master List Report's barangay filter already uses —
    # exposing it here too since staff commonly work one barangay at a time.
    barangays = (
        Beneficiary.objects.exclude(barangay='')
        .order_by('barangay').values_list('barangay', flat=True).distinct()
    )

    paginator = Paginator(beneficiaries, 50)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    active_filter_count = sum(1 for v in (query, status_filter, barangay_filter) if v)

    return render(request, 'beneficiaries/list.html', {
        'beneficiaries': page_obj,
        'page_obj': page_obj,
        'paginator': paginator,
        'query': query,
        'status_filter': status_filter,
        'barangay_filter': barangay_filter,
        'barangays': barangays,
        'active_filter_count': active_filter_count,
    })


@login_required
def beneficiary_master_list_report(request):
    """
    Beneficiary Master List / Summary Report — v2.1.19 UX pass, section 35.

    Admin-tier only. Filters use existing Beneficiary fields; exports never
    include FaceEmbedding, biometric templates, or liveness proof images —
    only the administrative columns listed in `columns` below.
    """
    from django.core.paginator import Paginator
    from datetime import date as _date

    if not request.user.is_admin:
        messages.error(request, 'Admin access required.')
        return redirect('beneficiaries:dashboard')

    status_f      = request.GET.get('status', '')
    barangay_f    = request.GET.get('barangay', '').strip()
    date_from     = request.GET.get('date_from', '')
    date_to       = request.GET.get('date_to', '')
    age_min       = request.GET.get('age_min', '').strip()
    age_max       = request.GET.get('age_max', '').strip()
    query         = request.GET.get('q', '').strip()
    export_fmt    = request.GET.get('export', '')

    qs = Beneficiary.objects.all().order_by('last_name', 'first_name')
    if status_f:
        qs = qs.filter(status=status_f)
    if barangay_f:
        qs = qs.filter(barangay=barangay_f)
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)
    if query:
        qs = (
            qs.filter(last_name__icontains=query) |
            qs.filter(first_name__icontains=query) |
            qs.filter(beneficiary_id__icontains=query) |
            qs.filter(senior_citizen_id__icontains=query)
        ).distinct()
    # Age is derived (not a stored column), so filter it in Python after the
    # DB query rather than trying to express it in SQL.
    if age_min.isdigit() or age_max.isdigit():
        lo = int(age_min) if age_min.isdigit() else None
        hi = int(age_max) if age_max.isdigit() else None
        qs = [b for b in qs if (lo is None or b.age >= lo) and (hi is None or b.age <= hi)]

    barangays = Beneficiary.objects.order_by('barangay').values_list('barangay', flat=True).distinct()

    filter_log = {
        'status': status_f or None, 'barangay': barangay_f or None,
        'date_from': date_from or None, 'date_to': date_to or None,
        'age_min': age_min or None, 'age_max': age_max or None, 'q': query or None,
    }

    def _rows():
        for b in qs:
            yield [
                b.beneficiary_id, b.senior_citizen_id, b.full_name, b.age,
                b.barangay, b.get_status_display(), b.created_at.date().isoformat(),
            ]

    columns = ['Beneficiary ID', 'Senior Citizen ID', 'Full Name', 'Age', 'Barangay', 'Status', 'Registration Date']

    if export_fmt == 'csv':
        import csv
        from django.http import HttpResponse
        from fans.report_export import sanitize_export_cell
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="fansc-beneficiary-master-list.csv"'
        writer = csv.writer(response)
        writer.writerow(columns)
        for row in _rows():
            writer.writerow([sanitize_export_cell(v) for v in row])
        AuditLog.log(
            action=AuditLog.ACTION_REPORT_EXPORT, user=request.user,
            details={'report': 'beneficiary_master_list', 'format': 'csv', 'filters': filter_log, 'row_count': len(qs) if isinstance(qs, list) else qs.count()},
            request=request,
        )
        return response

    if export_fmt == 'excel':
        from fans.report_export import build_report_workbook
        from django.http import HttpResponse
        wb = build_report_workbook(
            title='Beneficiary Master List', sheet_name='Master List',
            headers=columns, rows=list(_rows()),
            generated_by=request.user.get_full_name() or request.user.username,
            filters=filter_log,
        )
        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = 'attachment; filename="fansc-beneficiary-master-list.xlsx"'
        wb.save(response)
        AuditLog.log(
            action=AuditLog.ACTION_REPORT_EXPORT, user=request.user,
            details={'report': 'beneficiary_master_list', 'format': 'excel', 'filters': filter_log, 'row_count': len(qs) if isinstance(qs, list) else qs.count()},
            request=request,
        )
        return response

    if export_fmt == 'pdf':
        response = _beneficiary_master_list_pdf(request, columns, list(_rows()), filter_log)
        AuditLog.log(
            action=AuditLog.ACTION_REPORT_EXPORT, user=request.user,
            details={'report': 'beneficiary_master_list', 'format': 'pdf', 'filters': filter_log, 'row_count': len(qs) if isinstance(qs, list) else qs.count()},
            request=request,
        )
        return response

    # On-screen: paginate (age-filtered qs is a plain list; Paginator accepts either).
    paginator = Paginator(qs, 50)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    return render(request, 'beneficiaries/master_list_report.html', {
        'page_obj': page_obj,
        'paginator': paginator,
        'status_choices': Beneficiary.STATUS_CHOICES,
        'barangays': barangays,
        'status_f': status_f, 'barangay_f': barangay_f,
        'date_from': date_from, 'date_to': date_to,
        'age_min': age_min, 'age_max': age_max, 'query': query,
        'total': len(qs) if isinstance(qs, list) else qs.count(),
    })


def _beneficiary_master_list_pdf(request, columns, rows, filter_log):
    """Real binary PDF via reportlab — mirrors the pattern already used by
    verification._beneficiary_history_pdf. Landscape for the wider table."""
    from io import BytesIO
    from django.http import HttpResponse
    from django.utils import timezone as _tz

    try:
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib import colors
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    except ImportError:
        return HttpResponse(
            'PDF export requires the reportlab library. Run: python -m pip install reportlab',
            status=503, content_type='text/plain',
        )

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4),
                             leftMargin=12*mm, rightMargin=12*mm, topMargin=12*mm, bottomMargin=14*mm)
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle('h1', parent=styles['Heading1'], fontSize=14, spaceAfter=4)
    small = ParagraphStyle('small', parent=styles['Normal'], fontSize=8, textColor=colors.grey)
    body = ParagraphStyle('body', parent=styles['Normal'], fontSize=9)

    applied = ', '.join(f'{k}={v}' for k, v in filter_log.items() if v) or 'None'
    elements = [
        Paragraph('FANS-C — Beneficiary Master List', h1),
        Paragraph('Senior Citizen Stipend Distribution &mdash; Quezon City', body),
        Paragraph(f'Applied Filters: {applied}', small),
        Paragraph(
            f'Generated: {_tz.localtime(_tz.now()).strftime("%Y-%m-%d %H:%M")} '
            f'by {request.user.get_full_name() or request.user.username}',
            small,
        ),
        Spacer(1, 4*mm),
        Paragraph(f'Total Records: <b>{len(rows)}</b>', body),
        Spacer(1, 3*mm),
    ]

    tbl_data = [columns] + [[str(c) for c in row] for row in rows] if rows else [columns, ['No records match the selected filters.', '', '', '', '', '', '']]
    table = Table(tbl_data, colWidths=[28*mm, 32*mm, 55*mm, 15*mm, 35*mm, 25*mm, 30*mm], repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a3a6b')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f4f6f9')]),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    elements.append(table)

    def _footer(canvas, doc_):
        canvas.saveState()
        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(colors.grey)
        canvas.drawRightString(
            doc_.pagesize[0] - 12*mm, 8*mm,
            f'Page {doc_.page} — Generated {_tz.localtime(_tz.now()).strftime("%Y-%m-%d %H:%M")} — Administrative use only',
        )
        canvas.restoreState()

    doc.build(elements, onFirstPage=_footer, onLaterPages=_footer)
    response = HttpResponse(buf.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="fansc-beneficiary-master-list.pdf"'
    return response


@login_required
def beneficiary_detail(request, pk):
    beneficiary = get_object_or_404(Beneficiary, pk=pk)
    has_embedding = hasattr(beneficiary, 'face_embedding')
    from verification.models import VerificationAttempt, ClaimRecord, StipendEvent
    attempts = (
        VerificationAttempt.objects
        .filter(beneficiary=beneficiary)
        .select_related('representative')
        .order_by('-timestamp')[:20]
    )
    claims = (
        ClaimRecord.objects
        .filter(beneficiary=beneficiary)
        .select_related('stipend_event', 'claimed_by', 'approved_by', 'verification_attempt', 'representative')
        .order_by('-claimed_at')
    )
    today = timezone.localdate()
    active_event = StipendEvent.get_active_event_for_date(today)
    current_event_claimed = False
    if active_event:
        current_event_claimed = ClaimRecord.objects.filter(
            beneficiary=beneficiary,
            stipend_event=active_event,
            status=ClaimRecord.STATUS_CLAIMED,
        ).exists()
    pending_special = beneficiary.special_claim_requests.filter(
        status='pending'
    ).select_related('stipend_event').first() if active_event else None
    representatives = (
        beneficiary.representatives
        .select_related('face_embedding')
        .order_by('-is_active', 'last_name')
    )
    return render(request, 'beneficiaries/detail.html', {
        'beneficiary': beneficiary,
        'has_embedding': has_embedding,
        'attempts': attempts,
        'claims': claims,
        'active_event': active_event,
        'current_event_claimed': current_event_claimed,
        'pending_special': pending_special,
        'representatives': representatives,
    })


@login_required
@require_http_methods(['GET', 'POST'])
def beneficiary_edit(request, pk):
    beneficiary = get_object_or_404(Beneficiary, pk=pk)

    # Editing personal records (name, DOB, ID numbers, representative info)
    # is admin-only, consistent with all other write operations on existing
    # records.  Staff register new beneficiaries but do not edit existing ones.
    if not request.user.is_admin:
        messages.error(request, 'Admin access required to edit beneficiary records.')
        return redirect('beneficiaries:beneficiary_detail', pk=pk)

    if request.method == 'POST':
        form = BeneficiaryEditForm(request.POST, request.FILES, instance=beneficiary)
        if form.is_valid():
            changed_fields = [f for f in form.changed_data]
            form.save()
            AuditLog.log(
                action=AuditLog.ACTION_UPDATE,
                user=request.user,
                target_type='Beneficiary',
                target_id=beneficiary.id,
                details={
                    'beneficiary_id': beneficiary.beneficiary_id,
                    'changed_fields': changed_fields,
                },
                request=request
            )
            messages.success(request, 'Beneficiary record updated successfully.')
            return redirect('beneficiaries:beneficiary_detail', pk=beneficiary.pk)
    else:
        form = BeneficiaryEditForm(instance=beneficiary)

    return render(request, 'beneficiaries/edit.html', {
        'form': form,
        'beneficiary': beneficiary,
    })


@login_required
@require_POST
def beneficiary_correct_dob(request, pk):
    """
    Privileged, audited Date of Birth correction — the only way to change DOB
    for a beneficiary once reviewed (see BeneficiaryEditForm.clean_date_of_birth,
    which blocks DOB mutation through the ordinary edit form for any non-PENDING
    status). Restricted to President/Admin (has_financial_authority — NOT
    Technical Administrator) because DOB drives Birthday Bonus eligibility, a
    financial matter. Requires a written reason and records old/new DOB on the
    audit trail. Does not attempt to snapshot historical eligibility — see
    v2.1.17 audit notes.
    """
    if not request.user.has_financial_authority:
        messages.error(request, 'President or Admin authority is required to correct Date of Birth.')
        return redirect('beneficiaries:beneficiary_detail', pk=pk)

    beneficiary = get_object_or_404(Beneficiary, pk=pk)

    new_dob_raw = request.POST.get('new_date_of_birth', '').strip()
    reason = request.POST.get('reason', '').strip()

    if len(reason) < 10:
        messages.error(request, 'A written reason of at least 10 characters is required to correct Date of Birth.')
        return redirect('beneficiaries:beneficiary_detail', pk=pk)

    import datetime
    try:
        new_dob = datetime.date.fromisoformat(new_dob_raw)
    except (ValueError, TypeError):
        messages.error(request, 'Enter a valid Date of Birth.')
        return redirect('beneficiaries:beneficiary_detail', pk=pk)

    from .validators import validate_senior_citizen_dob
    from django.core.exceptions import ValidationError as DjangoValidationError
    try:
        validate_senior_citizen_dob(new_dob)
    except DjangoValidationError as e:
        messages.error(request, ' '.join(e.messages))
        return redirect('beneficiaries:beneficiary_detail', pk=pk)

    old_dob = beneficiary.date_of_birth
    if new_dob == old_dob:
        messages.info(request, 'Date of birth is unchanged.')
        return redirect('beneficiaries:beneficiary_detail', pk=pk)

    beneficiary.date_of_birth = new_dob
    beneficiary.save(update_fields=['date_of_birth', 'updated_at'])

    AuditLog.log(
        action=AuditLog.ACTION_UPDATE,
        user=request.user,
        target_type='Beneficiary',
        target_id=beneficiary.id,
        details={
            'beneficiary_id': beneficiary.beneficiary_id,
            'action': 'dob_correction',
            'old_date_of_birth': str(old_dob),
            'new_date_of_birth': str(new_dob),
            'reason': reason,
        },
        request=request,
    )
    messages.success(request, f"Date of birth corrected for {beneficiary.full_name}.")
    return redirect('beneficiaries:beneficiary_detail', pk=beneficiary.pk)


@login_required
@require_http_methods(['GET', 'POST'])
def beneficiary_deactivate(request, pk):
    """
    Deactivate or mark a beneficiary as deceased.
    Does NOT delete — preserves all historical records for audit.
    Only admins can deactivate.
    """
    if not request.user.is_admin:
        messages.error(request, 'Admin access required to change beneficiary status.')
        return redirect('beneficiaries:beneficiary_detail', pk=pk)

    beneficiary = get_object_or_404(Beneficiary, pk=pk)

    if request.method == 'POST':
        new_status = request.POST.get('new_status', Beneficiary.STATUS_INACTIVE)
        reason = request.POST.get('reason', '').strip()

        if new_status not in (Beneficiary.STATUS_INACTIVE, Beneficiary.STATUS_DECEASED):
            messages.error(request, 'Invalid status selected.')
            return redirect('beneficiaries:beneficiary_detail', pk=pk)

        if not reason:
            messages.error(request, 'A reason is required for deactivation.')
            return render(request, 'beneficiaries/deactivate.html', {
                'beneficiary': beneficiary,
                'error': 'Reason is required.',
            })

        old_status = beneficiary.status
        beneficiary.status = new_status
        beneficiary.deactivated_at = timezone.now()
        beneficiary.deactivated_by = request.user
        beneficiary.deactivated_reason = reason
        beneficiary.save()

        AuditLog.log(
            action=AuditLog.ACTION_UPDATE,
            user=request.user,
            target_type='Beneficiary',
            target_id=beneficiary.id,
            details={
                'beneficiary_id': beneficiary.beneficiary_id,
                'old_status': old_status,
                'new_status': new_status,
                'reason': reason,
            },
            request=request
        )
        status_label = 'Deceased' if new_status == Beneficiary.STATUS_DECEASED else 'Inactive'
        messages.success(request, f'{beneficiary.full_name} marked as {status_label}.')
        return redirect('beneficiaries:beneficiary_detail', pk=pk)

    return render(request, 'beneficiaries/deactivate.html', {'beneficiary': beneficiary})


@login_required
@require_http_methods(['POST'])
def beneficiary_reactivate(request, pk):
    """Re-activate a previously deactivated beneficiary (admin only)."""
    if not request.user.is_admin:
        messages.error(request, 'Admin access required.')
        return redirect('beneficiaries:beneficiary_detail', pk=pk)

    beneficiary = get_object_or_404(Beneficiary, pk=pk)
    old_status = beneficiary.status
    beneficiary.status = Beneficiary.STATUS_ACTIVE
    beneficiary.deactivated_at = None
    beneficiary.deactivated_by = None
    beneficiary.deactivated_reason = ''
    beneficiary.save()

    AuditLog.log(
        action=AuditLog.ACTION_UPDATE,
        user=request.user,
        target_type='Beneficiary',
        target_id=beneficiary.id,
        details={
            'beneficiary_id': beneficiary.beneficiary_id,
            'old_status': old_status,
            'new_status': 'active',
            'reason': 'Reactivated by admin',
        },
        request=request
    )
    messages.success(request, f'{beneficiary.full_name} reactivated to Active status.')
    return redirect('beneficiaries:beneficiary_detail', pk=pk)


# ─── Registration ─────────────────────────────────────────────────────────────

@login_required
@require_http_methods(['GET', 'POST'])
def register_step1(request):
    if request.method == 'POST':
        form = BeneficiaryInfoForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data.copy()
            data['date_of_birth'] = str(data['date_of_birth'])
            request.session['reg_step1'] = data
            return redirect('beneficiaries:register_step2')
    else:
        form = BeneficiaryInfoForm(initial={
            'province': 'Metro Manila (NCR)',
            'municipality': 'Quezon City',
        })
    return render(request, 'beneficiaries/register_step1.html', {
        'form': form,
        'QC_CITY': 'Quezon City',
    })


@login_required
@require_http_methods(['GET', 'POST'])
def register_step2(request):
    if 'reg_step1' not in request.session:
        return redirect('beneficiaries:register_step1')

    step1 = request.session['reg_step1']
    if request.method == 'POST':
        # Carry the not-yet-saved beneficiary's own ID fields on a transient
        # instance so RepresentativeForm.clean() can block a representative
        # from using the beneficiary's own identity document. This instance
        # is never saved by this form.
        instance = Beneficiary(
            valid_id_type=step1.get('valid_id_type', ''),
            valid_id_number=step1.get('valid_id_number', ''),
            senior_citizen_id=step1.get('senior_citizen_id', ''),
        )
        form = RepresentativeForm(request.POST, instance=instance)
        if form.is_valid():
            request.session['reg_step2'] = form.cleaned_data
            return redirect('beneficiaries:register_step3')
    else:
        form = RepresentativeForm()
    return render(request, 'beneficiaries/register_step2.html', {'form': form})


@login_required
@require_http_methods(['GET', 'POST'])
def register_step3(request):
    if 'reg_step1' not in request.session:
        return redirect('beneficiaries:register_step1')

    if request.method == 'POST':
        form = ConsentForm(request.POST)
        if form.is_valid():
            request.session['reg_step3'] = {'consent': True}
            return redirect('beneficiaries:register_face')
    else:
        form = ConsentForm()
    return render(request, 'beneficiaries/register_step3.html', {'form': form})


@login_required
def register_face(request):
    if 'reg_step1' not in request.session or 'reg_step3' not in request.session:
        return redirect('beneficiaries:register_step1')
    from verification.liveness import get_random_challenge
    from verification.views import _challenge_display
    challenge = get_random_challenge()
    request.session['reg_liveness_challenge'] = challenge
    return render(request, 'beneficiaries/register_face.html', {
        'challenge': challenge,
        'challenge_display': _challenge_display(challenge),
    })


@login_required
@require_POST
def register_submit_face(request):
    if 'reg_step1' not in request.session:
        return JsonResponse({'success': False, 'error': 'Session expired. Please restart registration.'})

    try:
        data = json.loads(request.body)
        image_data = data.get('image', '')
        client_liveness_passed = bool(data.get('liveness_passed', False))
        client_challenge_completed = bool(data.get('challenge_completed', False))
        client_anti_spoof_score = float(data.get('anti_spoof_score', 0.0))
        # Issue 1: when same-name+same-DOB is detected, staff can confirm
        # this is a Different Person by supplying a written reason. Without
        # the reason the registration is hard-blocked as before.
        duplicate_override_confirmed = bool(data.get('duplicate_override_confirmed', False))
        duplicate_override_reason = (data.get('duplicate_override_reason') or '').strip()
        duplicate_distinguishing_info = (data.get('duplicate_distinguishing_info') or '').strip()

        if not image_data:
            return JsonResponse({'success': False, 'error': 'No image received.'})

        if ',' in image_data:
            image_data = image_data.split(',')[1]
        try:
            image_bytes = base64.b64decode(image_data)
        except Exception:
            return JsonResponse({'success': False, 'error': 'Invalid image data. Please retake the photo and try again.'})

        # ── Server-side liveness re-validation (registration is stricter) ────
        from django.conf import settings as django_settings
        reg_liveness_required = getattr(django_settings, 'REGISTRATION_LIVENESS_REQUIRED', True)
        if reg_liveness_required:
            from verification.face_utils import load_image_from_bytes, detect_and_align_face, check_face_quality
            from verification.liveness import check_anti_spoofing
            try:
                _img = load_image_from_bytes(image_bytes)
                _face = detect_and_align_face(_img)
                spoof_check = check_anti_spoofing(
                    _face,
                    threshold=getattr(django_settings, 'ANTI_SPOOF_THRESHOLD', 0.25),
                )
                server_anti_spoof_score = float(spoof_check['score'])
                server_anti_spoof_passed = bool(spoof_check['passed'])
                quality = check_face_quality(_face)
                quality_ok = quality.get('ok', True)
            except ValueError:
                return JsonResponse({
                    'success': False,
                    'error': 'Registration blocked: no face detected in the submitted image. Please retake.',
                })

            if not server_anti_spoof_passed:
                AuditLog.log(
                    action=AuditLog.ACTION_REGISTER,
                    user=request.user,
                    details={
                        'outcome': 'registration_liveness_failed',
                        'reason': f'Anti-spoof score {server_anti_spoof_score:.3f} below threshold',
                    },
                    request=request,
                )
                return JsonResponse({
                    'success': False,
                    'error': (
                        f'Registration blocked: liveness check failed (anti-spoof score '
                        f'{round(server_anti_spoof_score * 100)}% — too low). '
                        'A phone screen or printed photo was detected. Please use a live person.'
                    ),
                })

            # Risk-based challenge: only require head-movement when the anti-spoof
            # score is borderline/suspicious, quality is poor, or a global override
            # forces it.  Strong anti-spoof scores (>= LIVENESS_CHALLENGE_TRIGGER_THRESHOLD)
            # with good quality are accepted without requiring the challenge.
            _challenge_trigger = getattr(django_settings, 'LIVENESS_CHALLENGE_TRIGGER_THRESHOLD', 0.30)
            _reg_challenge_required = getattr(django_settings, 'REGISTRATION_CHALLENGE_REQUIRED', False)

            server_challenge_required = (
                _reg_challenge_required
                or not quality_ok
                or server_anti_spoof_score < _challenge_trigger
            )

            if server_challenge_required and not client_challenge_completed:
                AuditLog.log(
                    action=AuditLog.ACTION_REGISTER,
                    user=request.user,
                    details={
                        'outcome': 'registration_challenge_failed',
                        'reason': 'Head movement challenge required but not completed',
                        'anti_spoof_score': server_anti_spoof_score,
                        'quality_ok': quality_ok,
                        'challenge_trigger_threshold': _challenge_trigger,
                    },
                    request=request,
                )
                return JsonResponse({
                    'success': False,
                    'error': (
                        'Registration blocked: additional liveness verification was required '
                        'but the head movement challenge was not completed. '
                        'Please retry with the real person present and follow the on-screen movement instruction.'
                    ),
                })

        result = process_face_for_registration(image_bytes)
        if not result['success']:
            return JsonResponse({'success': False, 'error': result['error']})

        import datetime
        step1 = request.session['reg_step1']
        step2 = request.session.get('reg_step2', {})

        dob = datetime.date.fromisoformat(step1['date_of_birth'])

        # ── Duplicate check ────────────────────────────────────────────────────
        sc_id = step1.get('senior_citizen_id', '').strip()
        if sc_id and Beneficiary.objects.filter(senior_citizen_id=sc_id).exists():
            return JsonResponse({
                'success': False,
                'error': (
                    f'A beneficiary with Senior Citizen ID "{sc_id}" is already registered. '
                    'Check existing records before proceeding.'
                ),
            })

        name_dob_qs = Beneficiary.objects.filter(
            first_name__iexact=step1['first_name'],
            last_name__iexact=step1['last_name'],
            date_of_birth=dob,
        )
        namedob_existing = None
        namedob_override_required = False
        if name_dob_qs.exists():
            namedob_existing = name_dob_qs.first()
            if not duplicate_override_confirmed:
                # Return structured response so UI can show the duplicate
                # review modal: existing record details + action choices
                # (Cancel / View Existing / Different Person request).
                return JsonResponse({
                    'success': False,
                    'duplicate_namedob_detected': True,
                    'existing': {
                        'beneficiary_id': namedob_existing.beneficiary_id,
                        'full_name': namedob_existing.full_name,
                        'date_of_birth': str(namedob_existing.date_of_birth),
                        'address': namedob_existing.full_address,
                        'status': namedob_existing.get_status_display(),
                        'detail_url': f'/beneficiaries/{namedob_existing.pk}/',
                    },
                    'error': (
                        f'A beneficiary named "{namedob_existing.full_name}" with the same date '
                        f'of birth ({dob}) is already registered '
                        f'(ID: {namedob_existing.beneficiary_id}). '
                        'If this is the SAME person, cancel this registration and use the '
                        'existing record. If this is a DIFFERENT person who happens to share '
                        'the same name and birthdate, submit a Different Person override '
                        'request with a written reason for review.'
                    ),
                })
            # Override path requires a non-empty written reason.
            if len(duplicate_override_reason) < 10:
                return JsonResponse({
                    'success': False,
                    'duplicate_namedob_detected': True,
                    'existing': {
                        'beneficiary_id': namedob_existing.beneficiary_id,
                        'full_name': namedob_existing.full_name,
                        'date_of_birth': str(namedob_existing.date_of_birth),
                        'address': namedob_existing.full_address,
                        'status': namedob_existing.get_status_display(),
                        'detail_url': f'/beneficiaries/{namedob_existing.pk}/',
                    },
                    'error': (
                        'A written reason of at least 10 characters is required for the '
                        'Different Person override request.'
                    ),
                })
            namedob_override_required = True
        # ──────────────────────────────────────────────────────────────────────

        # ── Duplicate face check (CRITICAL SECURITY) ───────────────────────
        from django.conf import settings as django_settings
        dup_threshold = getattr(django_settings, 'FACE_DEDUP_THRESHOLD', 0.80)
        from verification.face_utils import get_embedding, decrypt_embedding
        import numpy as np
        # Decrypt and re-use embedding from the registration result
        live_emb = decrypt_embedding(result['encrypted_embedding'])

        dup_result = check_duplicate_face(live_emb, threshold=dup_threshold)
        # Resolve matched beneficiary object for FK (may be None)
        _dup_match_obj = None
        _dup_score = None
        if dup_result['duplicates_found']:
            top = dup_result['matches'][0]
            _dup_score = top['score']
            try:
                _dup_match_obj = Beneficiary.objects.get(beneficiary_id=top['beneficiary_id'])
            except Beneficiary.DoesNotExist:
                pass
            AuditLog.log(
                action=AuditLog.ACTION_DUPLICATE_FACE,
                user=request.user,
                target_type='Beneficiary',
                target_id=top['beneficiary_id'],
                details={
                    'attempted_name': f"{step1['first_name']} {step1['last_name']}",
                    'matched_beneficiary_id': top['beneficiary_id'],
                    'matched_name': top['full_name'],
                    'score': top['score'],
                    'threshold': dup_threshold,
                    'total_matches': len(dup_result['matches']),
                    'action': 'pending_duplicate_review',
                },
                request=request,
            )
        # ──────────────────────────────────────────────────────────────────────

        beneficiary = Beneficiary(
            first_name=step1['first_name'],
            middle_name=step1.get('middle_name', ''),
            last_name=step1['last_name'],
            date_of_birth=dob,
            gender=step1['gender'],
            house_no=step1.get('house_no', ''),
            street=step1.get('street', ''),
            address=step1.get('address', ''),
            barangay=step1['barangay'],
            municipality=step1['municipality'],
            province=step1['province'],
            contact_number=step1.get('contact_number', ''),
            senior_citizen_id=step1.get('senior_citizen_id', ''),
            valid_id_type=step1.get('valid_id_type', ''),
            valid_id_number=step1.get('valid_id_number', ''),
            has_representative=step2.get('has_representative', False),
            rep_first_name=step2.get('rep_first_name', ''),
            rep_last_name=step2.get('rep_last_name', ''),
            rep_relationship=step2.get('rep_relationship', ''),
            rep_contact=step2.get('rep_contact', ''),
            rep_id_type=step2.get('rep_id_type', ''),
            rep_id_number=step2.get('rep_id_number', ''),
            consent_given=True,
            consent_date=timezone.now(),
            status=Beneficiary.STATUS_PENDING,
            registered_by=request.user,
            # Duplicate face fields — set if a face match was found
            duplicate_review_required=dup_result['duplicates_found'],
            duplicate_match_beneficiary=_dup_match_obj,
            duplicate_match_score=_dup_score,
        )
        beneficiary.save()

        if dup_result['duplicates_found']:
            # v2.2.0 Post-UAT Phase 5: identify the beneficiary by ID (not just
            # name — names collide), name the matched existing record and
            # score, and state the pending review status explicitly so the
            # notification is self-contained without opening the case.
            # Follow-up: the match may come from a representative's face
            # rather than the beneficiary's own — say so, since the two mean
            # different things to the reviewing admin.
            _top_match = dup_result['matches'][0]
            if _top_match.get('source') == 'representative':
                _matched_desc = (
                    f'representative {_top_match["representative_name"]} '
                    f'(registered for beneficiary {_top_match["beneficiary_id"]} — '
                    f'{_top_match["full_name"]})'
                )
            else:
                _matched_desc = (
                    f'{_dup_match_obj.full_name} ({_dup_match_obj.beneficiary_id})'
                    if _dup_match_obj else 'an existing beneficiary'
                )
            notify_admins(
                category=Notification.CATEGORY_FRAUD_ALERT,
                title=f'Duplicate face detected — {beneficiary.full_name} ({beneficiary.beneficiary_id})',
                message=(
                    f'Registration face matches {_matched_desc} at '
                    f'{_dup_score:.0%} similarity. Status: Pending Review.'
                ),
                url=reverse('beneficiaries:duplicate_review_detail', args=[beneficiary.pk]),
                dedupe_key=f'duplicate_face:{beneficiary.pk}',
            )

        # Mark this record as created on an offline device so sync.py
        # knows which workstation to attribute the registration to.
        # This is a no-op in centralized mode (SYNC_API_URL not configured).
        from beneficiaries import sync as _sync
        _sync.mark_created(beneficiary)

        FaceEmbedding.objects.create(
            beneficiary=beneficiary,
            embedding_data=result['encrypted_embedding'],
            created_by=request.user,
        )

        # Create the Representative object if one was entered in Step 2.
        # Without this, the Beneficiary's representative data sits in inline
        # fields only; the detail page queries representatives.all() which
        # always returned 0 even though has_representative was True.
        if step2.get('has_representative') and step2.get('rep_first_name') and step2.get('rep_last_name'):
            Representative.objects.create(
                beneficiary=beneficiary,
                first_name=step2['rep_first_name'],
                last_name=step2['rep_last_name'],
                relationship=step2.get('rep_relationship', ''),
                contact_number=step2.get('rep_contact', ''),
                valid_id_type=step2.get('rep_id_type', ''),
                valid_id_number=step2.get('rep_id_number', ''),
                registered_by=request.user,
            )

        # Create the DuplicateNameDobRequest if staff used the override path.
        namedob_request = None
        if namedob_override_required and namedob_existing is not None:
            from .models import DuplicateNameDobRequest
            namedob_request = DuplicateNameDobRequest.objects.create(
                new_beneficiary=beneficiary,
                existing_beneficiary=namedob_existing,
                requested_by=request.user,
                reason=duplicate_override_reason,
                distinguishing_info=duplicate_distinguishing_info,
            )
            notify_admins(
                category=Notification.CATEGORY_APPROVAL_REQUIRED,
                title='Name/DOB duplicate override needs review',
                message=f'{beneficiary.full_name} — staff confirmed different person from {namedob_existing.full_name}.',
                url=reverse('beneficiaries:namedob_review_detail', args=[namedob_request.pk]),
                dedupe_key=f'namedob_override:{namedob_request.pk}',
            )

            AuditLog.log(
                action=AuditLog.ACTION_DUPLICATE_FACE,
                user=request.user,
                target_type='Beneficiary',
                target_id=beneficiary.id,
                details={
                    'event': 'namedob_override_submitted',
                    'beneficiary_id': beneficiary.beneficiary_id,
                    'name': beneficiary.full_name,
                    'matched_beneficiary_id': namedob_existing.beneficiary_id,
                    'matched_name': namedob_existing.full_name,
                    'reason_excerpt': duplicate_override_reason[:280],
                    'distinguishing_info_provided': bool(duplicate_distinguishing_info),
                },
                request=request,
            )

        for key in ['reg_step1', 'reg_step2', 'reg_step3']:
            request.session.pop(key, None)

        # Duplicate review required — never auto-approve, always pending review.
        # A name+DOB override also blocks auto-approval.
        from verification.models import SystemConfig
        auto_approve = (
            SystemConfig.get_bool('auto_approve_beneficiaries', default=False)
            and not dup_result['duplicates_found']
            and not namedob_override_required
        )
        if auto_approve:
            beneficiary.status = Beneficiary.STATUS_ACTIVE
            beneficiary.save(update_fields=['status'])
            AuditLog.log(
                action=AuditLog.ACTION_AUTO_APPROVED,
                user=request.user,
                target_type='Beneficiary',
                target_id=beneficiary.id,
                details={
                    'beneficiary_id': beneficiary.beneficiary_id,
                    'name': beneficiary.full_name,
                    'trigger': 'auto_approve_beneficiaries=true',
                },
                request=request
            )

        AuditLog.log(
            action=AuditLog.ACTION_REGISTER,
            user=request.user,
            target_type='Beneficiary',
            target_id=beneficiary.id,
            details={
                'beneficiary_id': beneficiary.beneficiary_id,
                'name': beneficiary.full_name,
                'status': 'active' if auto_approve else ('pending_duplicate_review' if dup_result['duplicates_found'] else 'pending_approval'),
                'highest_dedup_score': dup_result['highest_score'],
                'duplicate_review_required': dup_result['duplicates_found'],
            },
            request=request
        )

        quality_msg = ''
        if result.get('quality') and not result['quality']['ok']:
            quality_msg = f' Note: {result["quality"]["reason"]}'

        if dup_result['duplicates_found']:
            top_match = dup_result['matches'][0]
            status_msg = (
                f'Registration submitted (ID: {beneficiary.beneficiary_id}) — '
                f'DUPLICATE FACE DETECTED (similarity {top_match["score"]:.0%} with {top_match["full_name"]}). '
                f'An administrator must review and approve or reject this record before it can be activated.{quality_msg}'
            )
        elif namedob_override_required:
            status_msg = (
                f'Registration submitted as a DIFFERENT-PERSON override request '
                f'(ID: {beneficiary.beneficiary_id}). '
                f'A President/Admin must review and approve the override before the '
                f'beneficiary can be activated.{quality_msg}'
            )
        elif auto_approve:
            status_msg = f'Registration complete — beneficiary is now active (ID: {beneficiary.beneficiary_id}).{quality_msg}'
        else:
            status_msg = (
                f'Registration submitted (ID: {beneficiary.beneficiary_id}). '
                f'Pending admin approval before the beneficiary can be verified.{quality_msg}'
            )

        return JsonResponse({
            'success': True,
            'message': status_msg,
            'duplicate_review_required': dup_result['duplicates_found'],
            'redirect': f'/dashboard/beneficiaries/{beneficiary.id}/'
        })

    except Exception as e:
        import logging
        logging.getLogger('verification').exception('register_submit_face failed: %s', e)
        return JsonResponse({
            'success': False,
            'error': 'Registration failed. Please retry; if the problem persists, contact your Technical Administrator.',
        })


# ─── Representative Management ───────────────────────────────────────────────

@login_required
@require_http_methods(['POST'])
def add_representative(request, pk):
    """Add an authorized representative to a beneficiary."""
    beneficiary = get_object_or_404(Beneficiary, pk=pk)
    first_name = request.POST.get('rep_first_name', '').strip()
    last_name = request.POST.get('rep_last_name', '').strip()
    relationship = request.POST.get('rep_relationship', '').strip()
    contact_number = request.POST.get('rep_contact', '').strip()
    valid_id_type = request.POST.get('rep_id_type', '').strip()
    valid_id_number = request.POST.get('rep_id_number', '').strip()

    from .forms import REP_ID_CHOICES
    allowed_id_types = [v for v, _ in REP_ID_CHOICES if v]

    errors = []
    if not first_name:
        errors.append('First name is required.')
    if not last_name:
        errors.append('Last name is required.')
    if not contact_number:
        errors.append('Contact number is required.')
    if not valid_id_type:
        errors.append('ID type must be selected.')
    elif valid_id_type not in allowed_id_types:
        errors.append(f'"{valid_id_type}" is not a valid ID type. Please select from the list.')
    if not valid_id_number:
        errors.append('Valid ID number is required.')

    if not errors:
        from .validators import representative_uses_beneficiary_identity
        if representative_uses_beneficiary_identity(
            beneficiary.valid_id_type,
            beneficiary.valid_id_number,
            beneficiary.senior_citizen_id,
            valid_id_type,
            valid_id_number,
        ):
            errors.append("The representative cannot use the beneficiary's own identity document.")

    if errors:
        for e in errors:
            messages.error(request, e)
        return redirect('beneficiaries:beneficiary_detail', pk=pk)

    rep = Representative.objects.create(
        beneficiary=beneficiary,
        first_name=first_name,
        last_name=last_name,
        relationship=relationship,
        contact_number=contact_number,
        valid_id_type=valid_id_type,
        valid_id_number=valid_id_number,
        registered_by=request.user,
    )
    # Also update legacy inline fields for backward compat
    beneficiary.has_representative = True
    beneficiary.rep_first_name = first_name
    beneficiary.rep_last_name = last_name
    beneficiary.rep_relationship = relationship
    beneficiary.rep_contact = contact_number
    beneficiary.rep_id_type = valid_id_type
    beneficiary.rep_id_number = valid_id_number
    beneficiary.save()

    AuditLog.log(
        action=AuditLog.ACTION_UPDATE,
        user=request.user,
        target_type='Representative',
        target_id=rep.id,
        details={
            'beneficiary_id': beneficiary.beneficiary_id,
            'representative_name': rep.full_name,
            'action': 'Representative added',
        },
        request=request,
    )
    messages.success(
        request,
        f'{rep.full_name} added as representative. '
        'Register their face data now to enable verification.'
    )
    return redirect('verification:register_rep_face', pk=beneficiary.pk, rep_pk=rep.pk)


@login_required
@require_POST
def deactivate_representative(request, pk, rep_pk):
    """Deactivate a representative (soft delete)."""
    beneficiary = get_object_or_404(Beneficiary, pk=pk)
    rep = get_object_or_404(Representative, pk=rep_pk, beneficiary=beneficiary)
    rep.is_active = False
    rep.save()
    AuditLog.log(
        action=AuditLog.ACTION_UPDATE,
        user=request.user,
        target_type='Representative',
        target_id=rep.id,
        details={
            'beneficiary_id': beneficiary.beneficiary_id,
            'representative_name': rep.full_name,
            'action': 'Representative deactivated',
        },
        request=request,
    )
    messages.warning(request, f'{rep.full_name} has been deactivated as representative.')
    return redirect('beneficiaries:beneficiary_detail', pk=pk)


# ─── Address Data API ─────────────────────────────────────────────────────────

@login_required
def address_municipalities(request):
    """Return municipalities/cities for a given province."""
    province = request.GET.get('province', '')
    from django.conf import settings as django_settings
    import os

    data_file = os.path.join(django_settings.BASE_DIR, 'static', 'data', 'ph_addresses.json')
    try:
        with open(data_file, 'r', encoding='utf-8') as f:
            address_data = json.load(f)
        municipalities = address_data.get('municipalities', {}).get(province, [])
    except (FileNotFoundError, json.JSONDecodeError):
        municipalities = []

    return JsonResponse({'municipalities': municipalities})


@login_required
def address_barangays(request):
    """Return barangays for a given municipality."""
    municipality = request.GET.get('municipality', '')
    from django.conf import settings as django_settings
    import os

    data_file = os.path.join(django_settings.BASE_DIR, 'static', 'data', 'ph_addresses.json')
    try:
        with open(data_file, 'r', encoding='utf-8') as f:
            address_data = json.load(f)
        barangays = address_data.get('barangays', {}).get(municipality, [])
    except (FileNotFoundError, json.JSONDecodeError):
        barangays = []

    return JsonResponse({'barangays': barangays})


# ─── User Management (Admin only) ────────────────────────────────────────────

@login_required
def user_list(request):
    return redirect('accounts:user_list')


@login_required
@require_http_methods(['GET', 'POST'])
def user_create(request):
    return redirect('accounts:user_create')


@login_required
@require_http_methods(['GET', 'POST'])
def user_edit(request, pk):
    return redirect('accounts:user_edit', pk=pk)


# ─── Sync Conflict Dashboard (Admin only) ─────────────────────────────────────

@login_required
def sync_conflict_list(request):
    """
    Admin-only view: list all beneficiary records in sync_conflict or
    sync_rejected state that require manual review.

    These records were created offline and, when sync was attempted:
    - sync_conflict  — the central server already has a different record for
                       the same ID (HTTP 409).  Admin must decide which version
                       is authoritative.
    - sync_rejected  — the central server refused the payload (HTTP 400/422).
                       Admin must correct the data or accept the local record.

    Records in this list cannot claim stipends until the conflict is resolved.
    """
    if not request.user.is_admin:
        messages.error(request, 'Admin access required.')
        return redirect('beneficiaries:dashboard')

    conflict_records = Beneficiary.objects.filter(
        sync_status__in=[Beneficiary.SYNC_CONFLICT, Beneficiary.SYNC_REJECTED]
    ).select_related('registered_by').order_by('sync_status', 'created_at')

    from beneficiaries.sync import conflict_count, rejected_count
    return render(request, 'beneficiaries/sync_conflict_list.html', {
        'conflict_records': conflict_records,
        'conflict_count': conflict_count(),
        'rejected_count': rejected_count(),
        'SYNC_CONFLICT': Beneficiary.SYNC_CONFLICT,
        'SYNC_REJECTED': Beneficiary.SYNC_REJECTED,
    })


@login_required
@require_http_methods(['GET', 'POST'])
def sync_conflict_review(request, pk):
    """
    Admin-only view: review a single beneficiary in sync_conflict or sync_rejected.

    POST actions:
      retry   — reset sync_status to 'pending_sync' so the next sync run will
                re-attempt to send this record.  Use when the conflict may have
                been transient (e.g. a previously-deleted duplicate on the server).
      accept  — mark sync_status as 'synced' locally.  Use when the admin has
                determined that the local record is authoritative and the central
                server's conflicting copy should be disregarded.
      reject  — keep sync_status as 'sync_rejected' with an admin note.  Use
                when the local record is invalid and should never be synced.
                The beneficiary remains in the system for audit purposes.

    All decisions are permanently recorded in the audit log.
    """
    if not request.user.is_admin:
        messages.error(request, 'Admin access required.')
        return redirect('beneficiaries:sync_conflict_list')

    beneficiary = get_object_or_404(
        Beneficiary,
        pk=pk,
        sync_status__in=[Beneficiary.SYNC_CONFLICT, Beneficiary.SYNC_REJECTED],
    )

    if request.method == 'POST':
        action = request.POST.get('action', '').strip()
        review_notes = request.POST.get('review_notes', '').strip()

        if not review_notes:
            messages.error(request, 'Review notes are required.')
            return render(request, 'beneficiaries/sync_conflict_review.html', {
                'beneficiary': beneficiary,
            })

        if action == 'retry':
            old_status = beneficiary.sync_status
            beneficiary.sync_status = Beneficiary.SYNC_PENDING
            beneficiary.sync_error = ''
            beneficiary.save(update_fields=['sync_status', 'sync_error'])
            AuditLog.log(
                action=AuditLog.ACTION_UPDATE,
                user=request.user,
                target_type='Beneficiary',
                target_id=beneficiary.id,
                details={
                    'beneficiary_id': beneficiary.beneficiary_id,
                    'sync_action': 'retry',
                    'old_sync_status': old_status,
                    'new_sync_status': Beneficiary.SYNC_PENDING,
                    'review_notes': review_notes,
                    'offline_device': beneficiary.offline_device,
                },
                request=request,
            )
            messages.success(
                request,
                f'{beneficiary.full_name} reset to pending_sync. '
                'The record will be re-sent on the next sync run.'
            )

        elif action == 'accept':
            old_status = beneficiary.sync_status
            beneficiary.sync_status = Beneficiary.SYNC_SYNCED
            beneficiary.sync_error = f'Admin-accepted after {old_status}. Notes: {review_notes}'
            beneficiary.save(update_fields=['sync_status', 'sync_error'])
            AuditLog.log(
                action=AuditLog.ACTION_SYNC_ACCEPTED,
                user=request.user,
                target_type='Beneficiary',
                target_id=beneficiary.id,
                details={
                    'beneficiary_id': beneficiary.beneficiary_id,
                    'sync_action': 'accept',
                    'old_sync_status': old_status,
                    'review_notes': review_notes,
                    'offline_device': beneficiary.offline_device,
                },
                request=request,
            )
            messages.success(
                request,
                f'{beneficiary.full_name} accepted as synced. '
                'The local record is now treated as authoritative.'
            )

        elif action == 'reject':
            beneficiary.sync_status = Beneficiary.SYNC_REJECTED
            beneficiary.sync_error = f'Admin-rejected. Notes: {review_notes}'
            beneficiary.save(update_fields=['sync_status', 'sync_error'])
            AuditLog.log(
                action=AuditLog.ACTION_SYNC_REJECTED,
                user=request.user,
                target_type='Beneficiary',
                target_id=beneficiary.id,
                details={
                    'beneficiary_id': beneficiary.beneficiary_id,
                    'sync_action': 'reject',
                    'review_notes': review_notes,
                    'offline_device': beneficiary.offline_device,
                },
                request=request,
            )
            messages.warning(
                request,
                f'{beneficiary.full_name} marked as sync_rejected. '
                'The record is retained for audit but will not be synced.'
            )

        else:
            messages.error(request, 'Invalid action. Choose retry, accept, or reject.')
            return render(request, 'beneficiaries/sync_conflict_review.html', {
                'beneficiary': beneficiary,
            })

        return redirect('beneficiaries:sync_conflict_list')

    return render(request, 'beneficiaries/sync_conflict_review.html', {
        'beneficiary': beneficiary,
    })


# ── Auto-Approval Settings ────────────────────────────────────────────────────

AUTO_APPROVE_KEYS = {
    'auto_approve_beneficiaries': 'Auto-approve beneficiary registrations',
    'auto_approve_representatives': 'Auto-approve representative additions',
    'auto_approve_face_enrollments': 'Auto-approve face enrollment updates',
    'auto_approve_user_accounts': 'Auto-approve new user accounts (set inactive by default when OFF)',
}


@login_required
@require_http_methods(['GET', 'POST'])
def auto_approval_settings(request):
    from verification.models import SystemConfig
    if not request.user.is_admin:
        messages.error(request, 'Access denied.')
        return redirect('beneficiaries:dashboard')

    if request.method == 'POST':
        for key, label in AUTO_APPROVE_KEYS.items():
            new_value = 'true' if request.POST.get(key) == 'on' else 'false'
            old_value = 'true' if SystemConfig.get_bool(key) else 'false'
            if new_value != old_value:
                SystemConfig.set_value(key, new_value, user=request.user, description=label)
                AuditLog.log(
                    action=AuditLog.ACTION_CONFIG_CHANGE,
                    user=request.user,
                    target_type='SystemConfig',
                    target_id=key,
                    details={'key': key, 'old_value': old_value, 'new_value': new_value},
                    request=request,
                )
        messages.success(request, 'Auto-approval settings saved.')
        return redirect('beneficiaries:auto_approval_settings')

    settings_state = {key: SystemConfig.get_bool(key) for key in AUTO_APPROVE_KEYS}
    return render(request, 'admin_panel/auto_approval_settings.html', {
        'settings_state': settings_state,
        'AUTO_APPROVE_KEYS': AUTO_APPROVE_KEYS,
    })


@login_required
def pending_approvals(request):
    from verification.models import FaceEmbedding
    if not request.user.is_admin:
        messages.error(request, 'Access denied.')
        return redirect('beneficiaries:dashboard')

    pending_beneficiaries = Beneficiary.objects.filter(
        status=Beneficiary.STATUS_PENDING,
        duplicate_review_required=False,
    ).order_by('created_at')
    duplicate_review_count = Beneficiary.objects.filter(
        duplicate_review_required=True,
        status=Beneficiary.STATUS_PENDING,
    ).count()

    pending_users = CustomUser.objects.filter(is_active=False).order_by('date_joined')

    return render(request, 'admin_panel/pending_approvals.html', {
        'pending_beneficiaries': pending_beneficiaries,
        'pending_users': pending_users,
        'duplicate_review_count': duplicate_review_count,
    })


@login_required
@require_POST
def approve_record(request):
    from verification.models import SystemConfig
    if not request.user.is_admin:
        return JsonResponse({'success': False, 'error': 'Access denied.'}, status=403)

    record_type = request.POST.get('type')
    record_id = request.POST.get('id')

    if record_type == 'beneficiary':
        try:
            b = Beneficiary.objects.get(pk=record_id, status=Beneficiary.STATUS_PENDING)
        except Beneficiary.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Record not found or already processed.'})
        b.status = Beneficiary.STATUS_ACTIVE
        b.save(update_fields=['status'])
        AuditLog.log(
            action=AuditLog.ACTION_RECORD_APPROVED,
            user=request.user,
            target_type='Beneficiary',
            target_id=b.id,
            details={'beneficiary_id': b.beneficiary_id, 'name': b.full_name},
            request=request,
        )
        return JsonResponse({'success': True, 'message': f'{b.full_name} approved.'})

    elif record_type == 'user':
        try:
            u = CustomUser.objects.get(pk=record_id, is_active=False)
        except CustomUser.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'User not found or already active.'})
        u.is_active = True
        u.save(update_fields=['is_active'])
        AuditLog.log(
            action=AuditLog.ACTION_RECORD_APPROVED,
            user=request.user,
            target_type='CustomUser',
            target_id=u.pk,
            details={'username': u.username, 'role': u.role},
            request=request,
        )
        return JsonResponse({'success': True, 'message': f'User {u.username} activated.'})

    return JsonResponse({'success': False, 'error': 'Unknown record type.'})


@login_required
@require_POST
def reject_record(request):
    if not request.user.is_admin:
        return JsonResponse({'success': False, 'error': 'Access denied.'}, status=403)

    record_type = request.POST.get('type')
    record_id = request.POST.get('id')
    reason = request.POST.get('reason', '').strip() or 'No reason provided.'

    if record_type == 'beneficiary':
        try:
            b = Beneficiary.objects.get(pk=record_id, status=Beneficiary.STATUS_PENDING)
        except Beneficiary.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Record not found or already processed.'})
        b.status = Beneficiary.STATUS_DISAPPROVED
        b.deactivated_reason = f'Registration rejected by {request.user.get_full_name() or request.user.username}: {reason}'
        b.save(update_fields=['status', 'deactivated_reason'])
        AuditLog.log(
            action=AuditLog.ACTION_RECORD_REJECTED,
            user=request.user,
            target_type='Beneficiary',
            target_id=b.id,
            details={'beneficiary_id': b.beneficiary_id, 'name': b.full_name, 'reason': reason},
            request=request,
        )
        return JsonResponse({'success': True, 'message': f'{b.full_name} rejected.'})

    elif record_type == 'user':
        try:
            u = CustomUser.objects.get(pk=record_id, is_active=False)
        except CustomUser.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'User not found.'})
        u.delete()
        AuditLog.log(
            action=AuditLog.ACTION_RECORD_REJECTED,
            user=request.user,
            target_type='CustomUser',
            target_id=record_id,
            details={'username': u.username, 'role': u.role, 'reason': reason},
            request=request,
        )
        return JsonResponse({'success': True, 'message': f'User {u.username} rejected and removed.'})

    return JsonResponse({'success': False, 'error': 'Unknown record type.'})


@login_required
@require_POST
def bulk_approve(request):
    if not request.user.is_admin:
        return JsonResponse({'success': False, 'error': 'Access denied.'}, status=403)

    import json as _json
    try:
        data = _json.loads(request.body)
    except Exception:
        return JsonResponse({'success': False, 'error': 'Invalid JSON.'})

    record_type = data.get('type')
    ids = data.get('ids', [])
    if not ids:
        return JsonResponse({'success': False, 'error': 'No records selected.'})

    approved = 0
    if record_type == 'beneficiary':
        qs = Beneficiary.objects.filter(pk__in=ids, status=Beneficiary.STATUS_PENDING, duplicate_review_required=False)
        for b in qs:
            b.status = Beneficiary.STATUS_ACTIVE
            b.save(update_fields=['status'])
            AuditLog.log(
                action=AuditLog.ACTION_RECORD_APPROVED,
                user=request.user,
                target_type='Beneficiary',
                target_id=b.id,
                details={'beneficiary_id': b.beneficiary_id, 'name': b.full_name, 'bulk': True},
                request=request,
            )
            approved += 1

    elif record_type == 'user':
        qs = CustomUser.objects.filter(pk__in=ids, is_active=False)
        for u in qs:
            u.is_active = True
            u.save(update_fields=['is_active'])
            AuditLog.log(
                action=AuditLog.ACTION_RECORD_APPROVED,
                user=request.user,
                target_type='CustomUser',
                target_id=u.pk,
                details={'username': u.username, 'bulk': True},
                request=request,
            )
            approved += 1

    return JsonResponse({'success': True, 'approved': approved})


# ─── Duplicate Face Review ────────────────────────────────────────────────────

@login_required
def duplicate_review_list(request):
    """Admin queue: all beneficiaries awaiting duplicate face review."""
    if not request.user.is_admin:
        messages.error(request, 'Admin access required.')
        return redirect('beneficiaries:dashboard')

    pending = (
        Beneficiary.objects
        .filter(duplicate_review_required=True, status=Beneficiary.STATUS_PENDING)
        .select_related('duplicate_match_beneficiary', 'registered_by')
        .order_by('created_at')
    )
    return render(request, 'beneficiaries/duplicate_review_list.html', {
        'pending': pending,
    })


@login_required
@require_http_methods(['GET', 'POST'])
def duplicate_review_detail(request, pk):
    """Admin review page for a single duplicate-flagged beneficiary."""
    if not request.user.is_admin:
        messages.error(request, 'Admin access required.')
        return redirect('beneficiaries:dashboard')

    # v2.2.0 Post-UAT Phase 5: no longer filtered to duplicate_review_required=True
    # only — a resolved case must still open (read-only) when reached via its
    # notification link instead of 404ing, so the notification and the case
    # it points to never disagree about whether review is still needed.
    beneficiary = get_object_or_404(
        Beneficiary.objects.select_related(
            'duplicate_match_beneficiary', 'registered_by', 'duplicate_reviewed_by',
        ),
        pk=pk,
    )

    if request.method == 'POST':
        if not beneficiary.duplicate_review_required:
            messages.warning(request, 'This duplicate case has already been reviewed.')
            return redirect('beneficiaries:duplicate_review_list')

        action = request.POST.get('action', '')
        notes = request.POST.get('notes', '').strip()

        if action == 'approve_twin':
            # Legitimate twin or lookalike — activate the record
            beneficiary.status = Beneficiary.STATUS_ACTIVE
            beneficiary.duplicate_review_required = False
            beneficiary.duplicate_review_notes = notes
            beneficiary.duplicate_reviewed_by = request.user
            beneficiary.duplicate_reviewed_at = timezone.now()
            beneficiary.save(update_fields=[
                'status', 'duplicate_review_required', 'duplicate_review_notes',
                'duplicate_reviewed_by', 'duplicate_reviewed_at',
            ])
            AuditLog.log(
                action=AuditLog.ACTION_RECORD_APPROVED,
                user=request.user,
                target_type='Beneficiary',
                target_id=beneficiary.id,
                details={
                    'beneficiary_id': beneficiary.beneficiary_id,
                    'name': beneficiary.full_name,
                    'decision': 'approved_twin_lookalike',
                    'matched_beneficiary': (
                        beneficiary.duplicate_match_beneficiary.beneficiary_id
                        if beneficiary.duplicate_match_beneficiary else None
                    ),
                    'match_score': beneficiary.duplicate_match_score,
                    'notes': notes,
                },
                request=request,
            )
            from logs.notifications import resolve_notification
            resolve_notification(f'duplicate_face:{beneficiary.pk}')
            messages.success(
                request,
                f'{beneficiary.full_name} approved as legitimate twin/lookalike and activated.'
            )

        elif action == 'reject_duplicate':
            # Confirmed duplicate/fraud — this registration was never
            # approved, so it is disapproved, not "deactivated" (Phase 4).
            beneficiary.status = Beneficiary.STATUS_DISAPPROVED
            beneficiary.duplicate_review_required = False
            beneficiary.duplicate_review_notes = notes
            beneficiary.duplicate_reviewed_by = request.user
            beneficiary.duplicate_reviewed_at = timezone.now()
            beneficiary.deactivated_reason = f'Registration rejected as confirmed duplicate/fraud: {notes}'
            beneficiary.save(update_fields=[
                'status', 'duplicate_review_required', 'duplicate_review_notes',
                'duplicate_reviewed_by', 'duplicate_reviewed_at', 'deactivated_reason',
            ])
            AuditLog.log(
                action=AuditLog.ACTION_RECORD_REJECTED,
                user=request.user,
                target_type='Beneficiary',
                target_id=beneficiary.id,
                details={
                    'beneficiary_id': beneficiary.beneficiary_id,
                    'name': beneficiary.full_name,
                    'decision': 'rejected_duplicate_fraud',
                    'matched_beneficiary': (
                        beneficiary.duplicate_match_beneficiary.beneficiary_id
                        if beneficiary.duplicate_match_beneficiary else None
                    ),
                    'match_score': beneficiary.duplicate_match_score,
                    'notes': notes,
                },
                request=request,
            )
            from logs.notifications import resolve_notification
            resolve_notification(f'duplicate_face:{beneficiary.pk}')
            messages.warning(
                request,
                f'{beneficiary.full_name} rejected as duplicate/fraud and disapproved.'
            )
        else:
            messages.error(request, 'Invalid action.')
            return render(request, 'beneficiaries/duplicate_review_detail.html', {
                'beneficiary': beneficiary,
            })

        return redirect('beneficiaries:duplicate_review_list')

    return render(request, 'beneficiaries/duplicate_review_detail.html', {
        'beneficiary': beneficiary,
    })


# ─── Duplicate Name+DOB Override Review (Issue 1) ────────────────────────────

@login_required
def namedob_review_list(request):
    """
    Admin queue for Different-Person override requests.

    Listed only when a name+DOB collision was overridden by staff during
    registration. President/Admin may approve (activate the new record) or
    reject (deactivate it).
    """
    from .models import DuplicateNameDobRequest
    if not request.user.is_admin:
        messages.error(request, 'Admin access required.')
        return redirect('beneficiaries:dashboard')

    pending = (
        DuplicateNameDobRequest.objects
        .filter(status=DuplicateNameDobRequest.STATUS_PENDING)
        .select_related('new_beneficiary', 'existing_beneficiary', 'requested_by')
        .order_by('created_at')
    )
    return render(request, 'beneficiaries/namedob_review_list.html', {
        'pending': pending,
    })


@login_required
@require_http_methods(['GET', 'POST'])
def namedob_review_detail(request, pk):
    """Review a single Different-Person override request."""
    from .models import DuplicateNameDobRequest
    if not request.user.is_admin:
        messages.error(request, 'Admin access required.')
        return redirect('beneficiaries:dashboard')

    req = get_object_or_404(
        DuplicateNameDobRequest.objects.select_related(
            'new_beneficiary', 'existing_beneficiary', 'requested_by',
        ),
        pk=pk,
    )

    if request.method == 'POST':
        action = request.POST.get('action', '')
        notes = (request.POST.get('notes') or '').strip()

        if req.status != DuplicateNameDobRequest.STATUS_PENDING:
            messages.warning(request, 'This request has already been reviewed.')
            return redirect('beneficiaries:namedob_review_list')

        if action == 'approve':
            req.status = DuplicateNameDobRequest.STATUS_APPROVED
            req.reviewed_by = request.user
            req.reviewed_at = timezone.now()
            req.review_notes = notes
            req.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'review_notes'])
            # Activate new beneficiary if still pending and no other holds
            b = req.new_beneficiary
            if (b.status == Beneficiary.STATUS_PENDING
                    and not b.duplicate_review_required):
                b.status = Beneficiary.STATUS_ACTIVE
                b.save(update_fields=['status'])
            AuditLog.log(
                action=AuditLog.ACTION_RECORD_APPROVED,
                user=request.user,
                target_type='Beneficiary',
                target_id=b.id,
                details={
                    'event': 'namedob_override_approved',
                    'beneficiary_id': b.beneficiary_id,
                    'name': b.full_name,
                    'matched_beneficiary': (
                        req.existing_beneficiary.beneficiary_id
                        if req.existing_beneficiary else None
                    ),
                    'review_notes': notes,
                    'reason_excerpt': req.reason[:280],
                },
                request=request,
            )
            from logs.notifications import resolve_notification
            resolve_notification(f'namedob_override:{req.pk}')
            resolve_notification(f'approval_reminder_namedob:{req.pk}')
            messages.success(request, f'Override approved — {b.full_name} activated.')
        elif action == 'reject':
            req.status = DuplicateNameDobRequest.STATUS_REJECTED
            req.reviewed_by = request.user
            req.reviewed_at = timezone.now()
            req.review_notes = notes
            req.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'review_notes'])
            b = req.new_beneficiary
            b.status = Beneficiary.STATUS_DISAPPROVED
            b.deactivated_reason = f'Name/DOB override rejected: {notes}' if notes else 'Name/DOB override rejected.'
            b.save(update_fields=['status', 'deactivated_reason'])
            AuditLog.log(
                action=AuditLog.ACTION_RECORD_REJECTED,
                user=request.user,
                target_type='Beneficiary',
                target_id=b.id,
                details={
                    'event': 'namedob_override_rejected',
                    'beneficiary_id': b.beneficiary_id,
                    'name': b.full_name,
                    'review_notes': notes,
                },
                request=request,
            )
            from logs.notifications import resolve_notification
            resolve_notification(f'namedob_override:{req.pk}')
            resolve_notification(f'approval_reminder_namedob:{req.pk}')
            messages.warning(request, f'Override rejected — {b.full_name} disapproved.')
        else:
            messages.error(request, 'Invalid action.')
            return redirect('beneficiaries:namedob_review_detail', pk=pk)

        return redirect('beneficiaries:namedob_review_list')

    return render(request, 'beneficiaries/namedob_review_detail.html', {
        'req': req,
    })
