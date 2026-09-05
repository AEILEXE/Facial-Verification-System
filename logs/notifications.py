"""
Notification-center creation helpers.

Called explicitly alongside AuditLog.log() at the points where something
needs an admin's attention — kept as sibling calls rather than folded into
AuditLog.log() itself, so audit logging (compliance record) and notifications
(UI inbox) stay separate concerns that can evolve independently.

notify_admins() is idempotent per dedupe_key: calling it twice with the same
key never creates a second notification for the same recipient, so callers
can call it freely from request-handling code without needing their own
duplicate-guard (mirrors accounts.management.commands.sync_backup_audit's
target_id-based idempotency).
"""
from .models import Notification

# Sensible default priority per category, used when a caller doesn't pass one
# explicitly. Same LOW/MEDIUM/HIGH scale fraud risk scoring uses.
_DEFAULT_PRIORITY = {
    Notification.CATEGORY_APPROVAL_REQUIRED:   Notification.PRIORITY_MEDIUM,
    Notification.CATEGORY_APPROVAL_REMINDER:   Notification.PRIORITY_MEDIUM,
    Notification.CATEGORY_VERIFICATION_REVIEW: Notification.PRIORITY_MEDIUM,
    Notification.CATEGORY_FRAUD_ALERT:         Notification.PRIORITY_HIGH,
    Notification.CATEGORY_SECURITY_ALERT:      Notification.PRIORITY_HIGH,
    Notification.CATEGORY_PASSWORD_RESET:      Notification.PRIORITY_MEDIUM,
    Notification.CATEGORY_SYSTEM_ALERT:        Notification.PRIORITY_MEDIUM,
}


def notify_admins(category, title, message='', url='', dedupe_key='', priority=None):
    """
    Create a Notification for every active admin-level user (President/Admin/IT),
    skipping any recipient who already has one with the same dedupe_key.
    Returns the list of newly-created Notification rows (empty if all recipients
    already had one, or if there are no active admins).
    """
    from accounts.models import CustomUser

    recipients = CustomUser.objects.filter(
        role__in=[CustomUser.ROLE_PRESIDENT, CustomUser.ROLE_ADMIN, CustomUser.ROLE_IT],
        is_active=True,
        account_status=CustomUser.STATUS_ACTIVE,
    )
    return _notify(recipients, category, title, message, url, dedupe_key, priority)


def notify_user(user, category, title, message='', url='', dedupe_key='', priority=None):
    """Create a Notification for a single specific user (e.g. the requester of
    something that was just approved/rejected), with the same dedupe guard."""
    created = _notify([user], category, title, message, url, dedupe_key, priority)
    return created[0] if created else None


def resolve_notification(dedupe_key):
    """
    Mark every still-unread Notification created with this dedupe_key as read.

    Call this when the underlying pending item (duplicate face review, shared
    representative review, name/DOB override, payout approval, ...) is
    approved or rejected, so the notification-center bell never keeps
    showing a stale "Pending Review" item after the case has actually been
    resolved (v2.2.0 Post-UAT Phase 3 / Phase 5).
    """
    from django.utils import timezone as _tz
    if not dedupe_key:
        return 0
    return Notification.objects.filter(dedupe_key=dedupe_key, is_read=False).update(
        is_read=True, read_at=_tz.now(),
    )


def _notify(recipients, category, title, message, url, dedupe_key, priority):
    resolved_priority = priority or _DEFAULT_PRIORITY.get(category, Notification.PRIORITY_MEDIUM)
    created = []
    for recipient in recipients:
        if dedupe_key:
            if Notification.objects.filter(recipient=recipient, dedupe_key=dedupe_key).exists():
                continue
        created.append(Notification.objects.create(
            recipient=recipient,
            category=category,
            priority=resolved_priority,
            title=title,
            message=message,
            url=url,
            dedupe_key=dedupe_key,
        ))
    return created


# How long a pending approval-type request can sit before it earns a
# once-only reminder notification (distinct from the initial "required"
# notification fired at creation time). Kept as a plain constant rather than
# a settings.py entry — this is a UX nicety, not a security/compliance knob
# an admin would need to tune per deployment.
_REMINDER_AFTER_HOURS = 48


def sync_approval_reminders():
    """
    Best-effort, idempotent: for every still-pending request type that
    already notifies on creation (face update, manual verification, special
    claim, name/DOB override, no-event claim, password reset), fire ONE
    additional CATEGORY_APPROVAL_REMINDER notification if it has been
    pending longer than _REMINDER_AFTER_HOURS. Dedupe key includes the
    pending item's id (not a date bucket) so each item reminds at most once
    total, not once per day — avoids the "useless notification" spam this
    system explicitly wants to avoid.

    Called opportunistically from the manual review queue and the admin
    dashboard view (same pattern as sync_backup_audit / sync_fraud_notifications)
    — there is no background scheduler in this app.
    """
    from django.urls import reverse
    from django.utils import timezone

    cutoff = timezone.now() - timezone.timedelta(hours=_REMINDER_AFTER_HOURS)
    created = []

    from verification.models import (
        FaceUpdateRequest, ManualVerificationRequest, SpecialClaimRequest, ClaimRecord,
        StipendEvent,
    )
    from beneficiaries.models import DuplicateNameDobRequest
    from accounts.models import PasswordResetRequest

    def _remind(qs, url_name, url_kwarg, title_fn, dedupe_prefix):
        filtered = qs.filter(created_at__lt=cutoff) if hasattr(qs.model, 'created_at') else qs.filter(claimed_at__lt=cutoff)
        for obj in filtered:
            url = reverse(url_name) if url_kwarg is None else reverse(url_name, args=[getattr(obj, url_kwarg)])
            created.extend(notify_admins(
                category=Notification.CATEGORY_APPROVAL_REMINDER,
                title=title_fn(obj),
                message=f'Still pending after {_REMINDER_AFTER_HOURS}+ hours.',
                url=url,
                dedupe_key=f'{dedupe_prefix}:{obj.pk}',
                priority=Notification.PRIORITY_HIGH,
            ))

    _remind(
        FaceUpdateRequest.objects.filter(status=FaceUpdateRequest.STATUS_PENDING),
        'verification:face_update_review', 'pk',
        lambda o: f'Reminder: face update pending for {o.beneficiary.full_name}',
        'approval_reminder_face_update',
    )
    _remind(
        ManualVerificationRequest.objects.filter(status=ManualVerificationRequest.STATUS_PENDING),
        'verification:manual_verify_review', 'pk',
        lambda o: f'Reminder: manual verification pending for {o.beneficiary.full_name}',
        'approval_reminder_manual_verify',
    )
    _remind(
        SpecialClaimRequest.objects.filter(status=SpecialClaimRequest.STATUS_PENDING),
        'verification:special_claim_review', 'pk',
        lambda o: f'Reminder: special claim request pending for {o.beneficiary.full_name}',
        'approval_reminder_special_claim',
    )
    _remind(
        DuplicateNameDobRequest.objects.filter(status=DuplicateNameDobRequest.STATUS_PENDING),
        'beneficiaries:namedob_review_detail', 'pk',
        lambda o: f'Reminder: name/DOB override pending for {o.new_beneficiary.full_name}',
        'approval_reminder_namedob',
    )
    _remind(
        ClaimRecord.objects.filter(status=ClaimRecord.STATUS_PENDING_APPROVAL),
        'verification:pending_claim_review', 'pk',
        lambda o: f'Reminder: claim awaiting approval for {o.beneficiary.full_name}',
        'approval_reminder_claim_pending',
    )
    _remind(
        PasswordResetRequest.objects.filter(status=PasswordResetRequest.STATUS_PENDING),
        'accounts:password_reset_request_list', None,
        lambda o: f'Reminder: password reset request pending for "{o.username_entered}"',
        'approval_reminder_password_reset',
    )
    # Payout schedules awaiting President approval — without this, a schedule
    # the President forgot about sits silently on the Payout Schedule page
    # with no follow-up (Phase 6: "if president forgets approval, create a
    # notification/reminder").
    _remind(
        StipendEvent.objects.filter(approval_status=StipendEvent.APPROVAL_PENDING),
        'verification:stipend_list', None,
        lambda o: f'Reminder: payout schedule "{o.title}" still awaiting President approval',
        'approval_reminder_stipend',
    )

    return created
