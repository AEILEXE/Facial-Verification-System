from django.db import models
from django.conf import settings
import uuid


class AuditLog(models.Model):
    ACTION_LOGIN = 'login'
    ACTION_LOGOUT = 'logout'
    ACTION_LOGIN_FAILED = 'login_failed'
    ACTION_REGISTER = 'register'
    ACTION_VERIFY = 'verify'
    ACTION_OVERRIDE = 'override'
    ACTION_USER_CREATE = 'user_create'
    ACTION_USER_UPDATE = 'user_update'
    ACTION_USER_DEACTIVATE = 'user_deactivate'
    ACTION_CONFIG_CHANGE = 'config_change'
    ACTION_FALLBACK = 'fallback'
    ACTION_UPDATE = 'update'
    ACTION_FACE_UPDATE_REQUEST  = 'face_update_request'
    ACTION_FACE_UPDATE_APPROVED = 'face_update_approved'
    ACTION_FACE_UPDATE_REJECTED = 'face_update_rejected'
    ACTION_MANUAL_VERIFY_REQUEST  = 'manual_verify_request'
    ACTION_MANUAL_VERIFY_APPROVED = 'manual_verify_approved'
    ACTION_MANUAL_VERIFY_REJECTED = 'manual_verify_rejected'
    ACTION_CLAIM                       = 'claim'
    ACTION_SPECIAL_CLAIM_REQUEST       = 'special_claim_request'
    ACTION_SPECIAL_CLAIM_APPROVED      = 'special_claim_approved'
    ACTION_SPECIAL_CLAIM_REJECTED      = 'special_claim_rejected'
    ACTION_REGISTER_APPROVED           = 'register_approved'
    ACTION_REGISTER_REJECTED           = 'register_rejected'
    ACTION_DUPLICATE_FACE              = 'duplicate_face'
    # Offline-sync audit actions — recorded by beneficiaries/sync.py
    ACTION_SYNC_ACCEPTED               = 'sync_accepted'   # central server accepted (HTTP 200/201)
    ACTION_SYNC_CONFLICT               = 'sync_conflict'   # central server returned 409
    ACTION_SYNC_REJECTED               = 'sync_rejected'   # central server returned 400/422

    # Password management
    ACTION_PASSWORD_CHANGE = 'password_change'   # user changed own password
    ACTION_PASSWORD_RESET  = 'password_reset'    # admin reset another user's password

    # Report/export
    ACTION_REPORT_EXPORT = 'report_export'        # admin exported a report

    # Pending claim (no active payout event)
    ACTION_CLAIM_PENDING          = 'claim_pending'           # claim queued for approval (no event)
    ACTION_CLAIM_PENDING_APPROVED = 'claim_pending_approved'  # President approved pending claim
    ACTION_CLAIM_PENDING_REJECTED = 'claim_pending_rejected'  # President rejected pending claim

    # Auto-approval workflow
    ACTION_AUTO_APPROVED    = 'auto_approved'    # record auto-approved by system
    ACTION_RECORD_APPROVED  = 'record_approved'  # admin manually approved a pending record
    ACTION_RECORD_REJECTED  = 'record_rejected'  # admin manually rejected a pending record

    # Payout / distribution lifecycle (post-release events). ACTION_CLAIM still
    # marks the moment the ClaimRecord is created; the actions below cover what
    # happens AFTER release: cancel, mark failed, admin override edits, and
    # security signals such as duplicate / suspicious payout attempts.
    ACTION_PAYOUT_CANCELLED          = 'payout_cancelled'
    ACTION_PAYOUT_FAILED             = 'payout_failed'
    ACTION_PAYOUT_OVERRIDE           = 'payout_override'           # admin edited a released payout
    ACTION_PAYOUT_FALLBACK_RELEASED  = 'payout_fallback_released'  # released via fallback ID path
    ACTION_DUPLICATE_PAYOUT_ATTEMPT  = 'duplicate_payout_attempt'  # blocked second-claim attempt

    # Submit-frame integrity (added 2026-05-28). These DENY the claim outright
    # — they never escalate to manual review. Logged separately from generic
    # ACTION_VERIFY so audit filters can target them precisely.
    ACTION_VERIFY_NO_FACE            = 'verify_no_face'             # zero faces in final frame
    ACTION_VERIFY_MULTIPLE_FACES     = 'verify_multiple_faces'      # >1 faces in final frame
    ACTION_VERIFY_SUBJECT_CHANGED    = 'verify_subject_changed'     # face differs from liveness frame
    ACTION_VERIFY_TX_STALE           = 'verify_tx_stale'            # missing/expired/reused liveness TX
    ACTION_VERIFY_FRAME_INVALID      = 'verify_frame_invalid'       # final frame cannot be processed

    # Shared-representative review (added 2026-05-28).
    ACTION_SHARED_REP_FLAGGED        = 'shared_rep_flagged'         # auto-queued at registration
    ACTION_SHARED_REP_APPROVED       = 'shared_rep_approved'        # admin allowed shared linkage
    ACTION_SHARED_REP_REJECTED       = 'shared_rep_rejected'        # admin denied linkage
    ACTION_SHARED_REP_DOCS_REQUIRED  = 'shared_rep_docs_required'   # admin requested authorization doc
    ACTION_SHARED_REP_BLOCKED        = 'shared_rep_blocked'         # admin marked as suspicious/blocked

    # Backup status mirrored from scripts/admin/daily-backup.ps1's manifests
    # (added by accounts.management.commands.sync_backup_audit — see fans/backup_status.py).
    ACTION_BACKUP_COMPLETED  = 'backup_completed'    # backup directory passed the restore-ready check
    ACTION_BACKUP_INCOMPLETE = 'backup_incomplete'   # backup directory failed the restore-ready check

    # Self-service password reset request (added for Phase 4 password recovery).
    ACTION_PASSWORD_RESET_REQUEST          = 'password_reset_request'
    ACTION_PASSWORD_RESET_REQUEST_REJECTED = 'password_reset_request_rejected'

    # Self-service email-OTP password reset (v2.2.0 Post-UAT Phase 6). Distinct
    # from ACTION_PASSWORD_RESET_REQUEST above, which is the admin-mediated
    # "I can't access my email" fallback (Phase 7) — these track the OTP
    # flow's own lifecycle end-to-end for audit/compliance.
    ACTION_OTP_REQUESTED  = 'otp_requested'   # user submitted username/email
    ACTION_OTP_SENT       = 'otp_sent'        # OTP generated + emailed (or generation attempted)
    ACTION_OTP_VERIFIED   = 'otp_verified'    # correct code entered
    ACTION_OTP_RESET_DONE = 'otp_reset_done'  # password actually changed via OTP flow
    ACTION_OTP_FAILED     = 'otp_failed'      # wrong code / expired / max attempts reached
    ACTION_OTP_RATE_LIMITED = 'otp_rate_limited'  # request throttled

    # Controlled biometric evaluation (BPA-2). These are RESEARCH data-collection
    # actions, intentionally distinct from ACTION_VERIFY/ACTION_CLAIM — an
    # evaluation trial never represents a live stipend verification or claim,
    # and must never be counted as one by any fraud/analytics/security query.
    ACTION_EVALUATION_DATASET_CREATED   = 'evaluation_dataset_created'
    ACTION_EVALUATION_DATASET_STARTED   = 'evaluation_dataset_started'
    ACTION_EVALUATION_DATASET_FINALIZED = 'evaluation_dataset_finalized'
    ACTION_EVALUATION_TRIAL_CREATED     = 'evaluation_trial_created'
    ACTION_EVALUATION_TRIAL_COMPLETED   = 'evaluation_trial_completed'
    ACTION_EVALUATION_TRIAL_ABORTED     = 'evaluation_trial_aborted'
    ACTION_EVALUATION_TRIAL_WITHDRAWN   = 'evaluation_trial_withdrawn'  # BPA-5 participant withdrawal
    ACTION_EVALUATION_DATASET_AMENDED   = 'evaluation_dataset_amended'  # BPA-5.1: post-finalization withdrawal

    ACTION_CHOICES = [
        (ACTION_LOGIN, 'Login'),
        (ACTION_LOGOUT, 'Logout'),
        (ACTION_LOGIN_FAILED, 'Login Failed'),
        (ACTION_REGISTER, 'Registration'),
        (ACTION_VERIFY, 'Verification'),
        (ACTION_OVERRIDE, 'Override'),
        (ACTION_USER_CREATE, 'User Created'),
        (ACTION_USER_UPDATE, 'User Updated'),
        (ACTION_USER_DEACTIVATE, 'User Deactivated'),
        (ACTION_CONFIG_CHANGE, 'Config Changed'),
        (ACTION_FALLBACK, 'Manual Verification Triggered'),
        (ACTION_UPDATE, 'Record Updated'),
        (ACTION_FACE_UPDATE_REQUEST,  'Face Update Requested'),
        (ACTION_FACE_UPDATE_APPROVED, 'Face Update Approved'),
        (ACTION_FACE_UPDATE_REJECTED, 'Face Update Rejected'),
        (ACTION_MANUAL_VERIFY_REQUEST,  'Manual Verification Requested'),
        (ACTION_MANUAL_VERIFY_APPROVED, 'Manual Verification Approved'),
        (ACTION_MANUAL_VERIFY_REJECTED, 'Manual Verification Rejected'),
        (ACTION_CLAIM,                  'Claim Recorded'),
        (ACTION_SPECIAL_CLAIM_REQUEST,  'Special Claim Requested'),
        (ACTION_SPECIAL_CLAIM_APPROVED, 'Special Claim Approved'),
        (ACTION_SPECIAL_CLAIM_REJECTED, 'Special Claim Rejected'),
        (ACTION_REGISTER_APPROVED,      'Registration Approved'),
        (ACTION_REGISTER_REJECTED,      'Registration Rejected'),
        (ACTION_DUPLICATE_FACE,         'Duplicate Face Detected'),
        (ACTION_SYNC_ACCEPTED,          'Sync Accepted'),
        (ACTION_SYNC_CONFLICT,          'Sync Conflict'),
        (ACTION_SYNC_REJECTED,          'Sync Rejected'),
        (ACTION_PASSWORD_CHANGE,        'Password Changed'),
        (ACTION_PASSWORD_RESET,         'Password Reset by Admin'),
        (ACTION_REPORT_EXPORT,          'Report Exported'),
        (ACTION_CLAIM_PENDING,          'Claim Queued (No Event)'),
        (ACTION_CLAIM_PENDING_APPROVED, 'Pending Claim Approved'),
        (ACTION_CLAIM_PENDING_REJECTED, 'Pending Claim Rejected'),
        (ACTION_AUTO_APPROVED,    'Auto-Approved by System'),
        (ACTION_RECORD_APPROVED,  'Record Manually Approved'),
        (ACTION_RECORD_REJECTED,  'Record Rejected'),
        (ACTION_PAYOUT_CANCELLED,         'Payout Cancelled'),
        (ACTION_PAYOUT_FAILED,            'Payout Marked Failed'),
        (ACTION_PAYOUT_OVERRIDE,          'Payout Edited (Admin Override)'),
        (ACTION_PAYOUT_FALLBACK_RELEASED, 'Fallback Payout Released'),
        (ACTION_DUPLICATE_PAYOUT_ATTEMPT, 'Duplicate Payout Attempt Blocked'),
        (ACTION_VERIFY_NO_FACE,           'Verify Denied: No Face in Frame'),
        (ACTION_VERIFY_MULTIPLE_FACES,    'Verify Denied: Multiple Faces in Frame'),
        (ACTION_VERIFY_SUBJECT_CHANGED,   'Verify Denied: Subject Changed After Liveness'),
        (ACTION_VERIFY_TX_STALE,          'Verify Denied: Liveness Proof Stale/Reused'),
        (ACTION_VERIFY_FRAME_INVALID,     'Verify Denied: Final Frame Invalid'),
        (ACTION_SHARED_REP_FLAGGED,       'Shared Representative Flagged for Review'),
        (ACTION_SHARED_REP_APPROVED,      'Shared Representative Approved'),
        (ACTION_SHARED_REP_REJECTED,      'Shared Representative Rejected'),
        (ACTION_SHARED_REP_DOCS_REQUIRED, 'Shared Representative: Document Requested'),
        (ACTION_SHARED_REP_BLOCKED,       'Shared Representative Blocked (Suspicious)'),
        (ACTION_BACKUP_COMPLETED,         'Backup Completed'),
        (ACTION_BACKUP_INCOMPLETE,        'Backup Incomplete/Failed'),
        (ACTION_PASSWORD_RESET_REQUEST,          'Password Reset Requested (Self-Service)'),
        (ACTION_PASSWORD_RESET_REQUEST_REJECTED, 'Password Reset Request Rejected'),
        (ACTION_OTP_REQUESTED,   'Password Reset OTP Requested'),
        (ACTION_OTP_SENT,        'Password Reset OTP Sent'),
        (ACTION_OTP_VERIFIED,    'Password Reset OTP Verified'),
        (ACTION_OTP_RESET_DONE,  'Password Reset Completed via OTP'),
        (ACTION_OTP_FAILED,      'Password Reset OTP Failed'),
        (ACTION_OTP_RATE_LIMITED, 'Password Reset OTP Rate Limited'),
        (ACTION_EVALUATION_DATASET_CREATED,   'Evaluation Dataset Created'),
        (ACTION_EVALUATION_DATASET_STARTED,   'Evaluation Dataset Collection Started'),
        (ACTION_EVALUATION_DATASET_FINALIZED, 'Evaluation Dataset Finalized'),
        (ACTION_EVALUATION_TRIAL_CREATED,     'Evaluation Trial Created'),
        (ACTION_EVALUATION_TRIAL_COMPLETED,   'Evaluation Trial Completed'),
        (ACTION_EVALUATION_TRIAL_ABORTED,     'Evaluation Trial Aborted'),
        (ACTION_EVALUATION_TRIAL_WITHDRAWN,   'Evaluation Trial Withdrawn (Participant Request)'),
        (ACTION_EVALUATION_DATASET_AMENDED,   'Evaluation Dataset Amended (Post-Finalization Withdrawal)'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_logs'
    )
    action = models.CharField(max_length=40, choices=ACTION_CHOICES, db_index=True)
    target_type = models.CharField(max_length=50, blank=True)
    target_id = models.CharField(max_length=100, blank=True)
    details = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'fans_audit_logs'
        ordering = ['-timestamp']
        indexes = [
            # Supports per-staff activity/mass-edit queries (Analytics + Fraud Detection Phase 1).
            models.Index(fields=['user', 'action', 'timestamp']),
            # Supports dashboard filter-by-action-over-date-range queries.
            models.Index(fields=['action', 'timestamp']),
        ]

    def __str__(self):
        user_str = self.user.username if self.user else 'Anonymous'
        return f'[{self.timestamp}] {user_str} - {self.action}'

    @classmethod
    def log(cls, action, user=None, target_type='', target_id='', details=None, request=None):
        ip = None
        ua = ''
        if request:
            ip = get_client_ip(request)
            ua = request.META.get('HTTP_USER_AGENT', '')[:500]
        return cls.objects.create(
            user=user,
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id else '',
            details=details or {},
            ip_address=ip,
            user_agent=ua,
        )


class Notification(models.Model):
    """
    Per-user notification-center inbox item. Created explicitly alongside
    AuditLog.log() calls at the points where something needs an admin's
    attention (approval reminders, fraud alerts, verification review alerts)
    — see logs/notifications.py for the notify_admins() helper and dedupe_key
    idempotency pattern (mirrors sync_backup_audit's target_id dedup).

    `url` is the resolved absolute path at creation time (not a URL name to
    re-resolve later) — clicking a notification navigates straight there.
    """
    # v2.2.0: expanded from 4 to the 7 categories the notification system is
    # meant to distinguish. CATEGORY_APPROVAL_REQUIRED fires once when
    # something new needs action; CATEGORY_APPROVAL_REMINDER fires only if
    # that item is still pending after a while (see logs/notifications.py
    # sync_approval_reminders) — the two were conflated under one
    # "approval_reminder" category before this version.
    CATEGORY_APPROVAL_REQUIRED   = 'approval_required'
    CATEGORY_APPROVAL_REMINDER   = 'approval_reminder'
    CATEGORY_VERIFICATION_REVIEW = 'verification_review'
    CATEGORY_FRAUD_ALERT         = 'fraud_alert'
    CATEGORY_SECURITY_ALERT      = 'security_alert'
    CATEGORY_PASSWORD_RESET      = 'password_reset_request'
    CATEGORY_SYSTEM_ALERT        = 'system_alert'
    CATEGORY_CHOICES = [
        (CATEGORY_APPROVAL_REQUIRED,   'Approval Required'),
        (CATEGORY_APPROVAL_REMINDER,   'Approval Reminder'),
        (CATEGORY_VERIFICATION_REVIEW, 'Verification Review'),
        (CATEGORY_FRAUD_ALERT,         'Fraud Alert'),
        (CATEGORY_SECURITY_ALERT,      'Security Alert'),
        (CATEGORY_PASSWORD_RESET,      'Password Reset Request'),
        (CATEGORY_SYSTEM_ALERT,        'System Alert'),
    ]

    PRIORITY_LOW    = 'LOW'
    PRIORITY_MEDIUM = 'MEDIUM'
    PRIORITY_HIGH   = 'HIGH'
    PRIORITY_CHOICES = [
        (PRIORITY_LOW,    'Low'),
        (PRIORITY_MEDIUM, 'Medium'),
        (PRIORITY_HIGH,   'High'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notifications',
    )
    category = models.CharField(
        max_length=25, choices=CATEGORY_CHOICES, default=CATEGORY_SYSTEM_ALERT, db_index=True,
    )
    priority = models.CharField(
        max_length=10, choices=PRIORITY_CHOICES, default=PRIORITY_MEDIUM, db_index=True,
        help_text='Same LOW/MEDIUM/HIGH scale as fraud risk scoring, for visual consistency.',
    )
    title = models.CharField(max_length=200)
    message = models.CharField(max_length=500, blank=True)
    url = models.CharField(max_length=500, blank=True, help_text='Resolved path to navigate to on click.')

    # Idempotency key so the same underlying event (e.g. one pending request,
    # one beneficiary crossing a fraud threshold) never creates duplicate
    # notifications for the same recipient. Mirrors AuditLog.target_id.
    dedupe_key = models.CharField(max_length=200, blank=True, db_index=True)

    is_read = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'fans_notifications'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['recipient', 'is_read', 'created_at']),
        ]

    def __str__(self):
        return f'[{self.category}] {self.title} -> {self.recipient.username}'

    def mark_read(self):
        if not self.is_read:
            from django.utils import timezone as _tz
            self.is_read = True
            self.read_at = _tz.now()
            self.save(update_fields=['is_read', 'read_at'])


def get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')
