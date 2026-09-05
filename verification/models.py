from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from beneficiaries.models import Beneficiary
import uuid


class StipendEvent(models.Model):
    """
    Represents a stipend distribution schedule (e.g., monthly payout).
    Claims can be linked to a StipendEvent so logs show which payout period
    is being claimed.

    event_type:
      REGULAR        — standard monthly stipend; all active beneficiaries are eligible.
      BIRTHDAY_BONUS — birthday bonus; only beneficiaries whose birth month matches
                       the event month are eligible.

    Payout window:
      payout_start_date / payout_end_date define the date range during which
      beneficiaries may claim for this event. If these are not set, only the exact
      `date` is matched (single-day event).
    """
    EVENT_TYPE_REGULAR = 'regular'
    EVENT_TYPE_BIRTHDAY = 'birthday_bonus'
    EVENT_TYPE_CUSTOM = 'custom'
    EVENT_TYPE_CHOICES = [
        (EVENT_TYPE_REGULAR, 'Regular Monthly Stipend'),
        (EVENT_TYPE_BIRTHDAY, 'Birthday Bonus'),
        (EVENT_TYPE_CUSTOM, 'Other / Custom'),
    ]

    # ── Approval workflow constants (Issue 5) ────────────────────────────────
    # Admin-created schedules are pending approval and not visible/published for
    # claiming until the President approves. President-created schedules may
    # publish immediately (approval_status set at create time in the view).
    APPROVAL_PENDING  = 'pending_approval'
    APPROVAL_APPROVED = 'approved'
    APPROVAL_REJECTED = 'rejected'
    APPROVAL_CHOICES = [
        (APPROVAL_PENDING,  'Pending Approval'),
        (APPROVAL_APPROVED, 'Approved / Published'),
        (APPROVAL_REJECTED, 'Rejected'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=200)
    date = models.DateField(help_text='Main payout date or start of payout period.')
    event_type = models.CharField(
        max_length=30,
        choices=EVENT_TYPE_CHOICES,
        default=EVENT_TYPE_REGULAR,
    )
    custom_event_type = models.CharField(
        max_length=100,
        blank=True,
        help_text='Required when event_type is "custom". Free-text distribution name.',
    )
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    # Approval workflow (Issue 5) — Admin must wait for President approval
    # before the schedule is usable for claiming.
    approval_status = models.CharField(
        max_length=20,
        choices=APPROVAL_CHOICES,
        default=APPROVAL_APPROVED,
        help_text='Approval state for this schedule. Approved/Published events are usable for claiming.',
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='approved_stipend_events',
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)
    published_at = models.DateTimeField(null=True, blank=True)

    # v2.2.0 Phase 2: if a schedule is approved after its payout window has
    # already ended, the President must record why — this is never silent.
    # Blank for schedules approved on time.
    late_approval_reason = models.TextField(
        blank=True,
        help_text='Required when approval happens after the payout date has already passed.',
    )

    # Per-beneficiary payout amount for this event. ClaimRecord.amount is
    # snapshotted from this value at release time so events whose amount is
    # changed later do not retroactively rewrite past payouts.
    amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text='Standard payout amount per beneficiary for this event (PHP).',
    )

    # Optional payout window — if set, claims are accepted within start..end inclusive
    payout_start_date = models.DateField(
        null=True, blank=True,
        help_text='First day beneficiaries may claim. Defaults to date if not set.',
    )
    payout_end_date = models.DateField(
        null=True, blank=True,
        help_text='Last day beneficiaries may claim. Defaults to date if not set.',
    )
    # Optional daily time window (Asia/Manila). If blank, event is active all day.
    payout_start_time = models.TimeField(
        null=True, blank=True,
        help_text='Daily start time for claiming (Asia/Manila). Leave blank = all day.',
    )
    payout_end_time = models.TimeField(
        null=True, blank=True,
        help_text='Daily end time for claiming (Asia/Manila). Leave blank = all day.',
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_stipend_events',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'fans_stipend_events'
        ordering = ['date']

    def __str__(self):
        return f'{self.title} ({self.date})'

    def get_display_event_type(self):
        """Human-readable event type; for custom events returns the custom name."""
        if self.event_type == self.EVENT_TYPE_CUSTOM and self.custom_event_type:
            return self.custom_event_type
        return self.get_event_type_display()

    def get_claim_start(self):
        """First day this event accepts claims."""
        return self.payout_start_date or self.date

    def get_claim_end(self):
        """Last day this event accepts claims."""
        return self.payout_end_date or self.date

    @property
    def is_published(self) -> bool:
        """True only for schedules that the President has approved (Issue 5)."""
        return self.approval_status == self.APPROVAL_APPROVED

    def is_active_on_date(self, check_date) -> bool:
        """Returns True if check_date is within the date payout window (no time check)
        and the schedule has been approved/published."""
        if not self.is_active or not self.is_published:
            return False
        return self.get_claim_start() <= check_date <= self.get_claim_end()

    def is_within_time_window(self, check_time) -> bool:
        """Returns True if check_time falls within payout_start_time..payout_end_time.
        If neither time is set the event is active all day (returns True)."""
        if not self.payout_start_time or not self.payout_end_time:
            return True
        return self.payout_start_time <= check_time <= self.payout_end_time

    # Human-readable reasons for check_claim_eligible_now() — reused by every
    # financial-finalization call site (verify_submit, manual review approval,
    # override release, special claim approval) so the message shown to staff/
    # admins is worded identically everywhere a payout is blocked (Phase B.5).
    CLAIM_INELIGIBLE_REASONS = {
        'inactive': 'this stipend event is no longer active',
        'unapproved': 'this stipend event is no longer approved/published',
        'window_closed': "this event's claiming window has closed",
    }

    @classmethod
    def describe_claim_ineligible_reason(cls, reason_code: str) -> str:
        return cls.CLAIM_INELIGIBLE_REASONS.get(
            reason_code, 'this stipend event is no longer eligible for claiming',
        )

    def check_claim_eligible_now(self):
        """
        Phase B.5 finalization-within-window policy — answers "can THIS event
        accept a claim/payout finalization RIGHT NOW?". A selected event must
        still satisfy this at the moment a ClaimRecord is created, not merely
        when verification/review started, because approval state and the
        payout window can change while a review or override sits pending.

        This is a FINANCIAL/WORKFLOW gate only — callers must never use it to
        rewrite a VerificationAttempt's biometric decision. A VERIFIED
        identity result stays VERIFIED even when this returns ineligible;
        only ClaimRecord creation is blocked.

        Reuses is_active / is_published / get_claim_start / get_claim_end /
        is_within_time_window rather than duplicating their logic, so the
        finalization gate can never silently drift from the same-day
        claiming rules those already encode. On top of the event's own
        window, the global 07:00-20:00 same-day claiming hours also apply —
        this only has any effect for a blank-time ("all day") event, since an
        event with explicit times is already validated to fall within these
        bounds when created/edited.

        Returns (eligible: bool, reason: str) where reason is '' when
        eligible, else one of the CLAIM_INELIGIBLE_REASONS keys — pass it to
        describe_claim_ineligible_reason() for a user-facing sentence.
        """
        import datetime as _dt
        from django.utils import timezone as _tz
        from zoneinfo import ZoneInfo
        manila = ZoneInfo('Asia/Manila')
        now_manila = _tz.now().astimezone(manila)
        check_date = now_manila.date()
        check_time = now_manila.time()

        if not self.is_active:
            return False, 'inactive'
        if not self.is_published:
            return False, 'unapproved'
        if not (self.get_claim_start() <= check_date <= self.get_claim_end()):
            return False, 'window_closed'
        if not self.is_within_time_window(check_time):
            return False, 'window_closed'
        if not (_dt.time(7, 0) <= check_time <= _dt.time(20, 0)):
            return False, 'window_closed'
        return True, ''

    def is_beneficiary_eligible(self, beneficiary) -> bool:
        """
        Returns True if the beneficiary is eligible for this stipend event.
        For BIRTHDAY_BONUS events, the beneficiary's birth month must match the event month.
        """
        if self.event_type == self.EVENT_TYPE_BIRTHDAY:
            return beneficiary.date_of_birth.month == self.date.month
        return True

    def get_eligible_beneficiaries(self):
        """Returns a queryset of active beneficiaries eligible for this event."""
        from beneficiaries.models import Beneficiary
        qs = Beneficiary.objects.filter(
            status=Beneficiary.STATUS_ACTIVE,
            consent_given=True,
        )
        if self.event_type == self.EVENT_TYPE_BIRTHDAY:
            qs = qs.filter(date_of_birth__month=self.date.month)
        return qs

    @classmethod
    def get_active_event_now(cls):
        """
        Returns the active StipendEvent whose payout window contains the current
        Asia/Manila date and time, or None.  Respects payout_start_time /
        payout_end_time if both are set.  Use this for the claim workflow.

        Nothing in this model (or the create/edit forms) prevents two approved
        events from sharing an overlapping date window — e.g. a Regular Monthly
        Stipend and a Birthday Bonus both scheduled for the same day, each with
        its own daily time window. So every date-matching candidate is checked
        in turn (see get_active_events_for_date): a candidate whose own time
        window has already closed must never hide a different candidate whose
        window is still open (Phase B.2 fix — previously only the single
        date-window "first" candidate was tested, and the whole lookup gave up
        if that one candidate's time window had closed).
        """
        from django.utils import timezone as _tz
        from zoneinfo import ZoneInfo
        manila = ZoneInfo('Asia/Manila')
        now_manila = _tz.now().astimezone(manila)
        check_date = now_manila.date()
        check_time = now_manila.time()
        for event in cls.get_active_events_for_date(check_date):
            if event.is_within_time_window(check_time):
                return event
        return None

    @classmethod
    def get_open_events_now(cls):
        """
        Returns EVERY active StipendEvent that is valid right now (Asia/Manila
        date AND time window), not just one — for callers where silently
        picking a single event would be a financial-correctness risk (Phase
        B.3). ClaimRecord's uniqueness constraint is scoped to
        (beneficiary, stipend_event), not beneficiary alone, so a beneficiary
        can legitimately hold separate claimed payouts for two different
        events on the same day (e.g. a Regular Monthly Stipend and a Birthday
        Bonus both open at once) — nothing in the model treats that as a
        conflict to prevent. Callers that bind a verification/claim to a
        specific event MUST use this method and require an explicit choice
        when it returns more than one result; get_active_event_now() remains
        a single deterministic best-effort pick for informational/display use
        only (dashboard cards, analytics), where guessing wrong has no
        financial consequence.
        """
        from django.utils import timezone as _tz
        from zoneinfo import ZoneInfo
        manila = ZoneInfo('Asia/Manila')
        now_manila = _tz.now().astimezone(manila)
        check_date = now_manila.date()
        check_time = now_manila.time()
        return [
            event for event in cls.get_active_events_for_date(check_date)
            if event.is_within_time_window(check_time)
        ]

    @classmethod
    def get_active_events_for_date(cls, check_date):
        """
        Returns ALL active StipendEvents whose payout window contains check_date
        (no time check), ordered deterministically (date, then created_at) so
        that when more than one event is simultaneously valid the
        earliest-created one is checked first — no explicit event-type or
        other priority rule exists in the business requirements, so creation
        order is used only to make an otherwise-ambiguous choice reproducible,
        not as an intentional policy.

        Priority: events whose payout_start_date <= check_date <= payout_end_date.
        Falls back to events where date == check_date (legacy single-day events).
        """
        windowed = list(cls.objects.filter(
            is_active=True,
            approval_status=cls.APPROVAL_APPROVED,
            payout_start_date__lte=check_date,
            payout_end_date__gte=check_date,
        ).order_by('date', 'created_at'))
        if windowed:
            return windowed

        # Fallback: single-day events matching exactly
        return list(cls.objects.filter(
            is_active=True,
            approval_status=cls.APPROVAL_APPROVED,
            date=check_date,
            payout_start_date__isnull=True,
        ).order_by('date', 'created_at'))

    @classmethod
    def get_active_event_for_date(cls, check_date):
        """
        Returns the first active StipendEvent whose payout window contains
        check_date, or None if no such event exists. Date-only (no time
        check) — used for display purposes (dashboard cards, stipend list),
        not the claim workflow. See get_active_events_for_date() for the full
        candidate list and get_active_event_now() for the time-aware claim
        gate that must consider every candidate, not just this first one.
        """
        events = cls.get_active_events_for_date(check_date)
        return events[0] if events else None


class FaceEmbedding(models.Model):
    """
    Primary face embedding for a beneficiary (FaceNet, 512-d, encrypted).
    One per beneficiary. For additional templates, see the registration re-enroll flow.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    beneficiary = models.OneToOneField(
        Beneficiary,
        on_delete=models.CASCADE,
        related_name='face_embedding'
    )
    embedding_data = models.BinaryField()
    embedding_version = models.CharField(max_length=20, default='facenet-v1')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True
    )

    class Meta:
        db_table = 'fans_face_embeddings'

    def __str__(self):
        return f'Embedding for {self.beneficiary.full_name}'


class RepresentativeFaceEmbedding(models.Model):
    """
    Encrypted FaceNet embedding for a registered representative.
    A representative cannot be used for claiming until this record exists.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    representative = models.OneToOneField(
        'beneficiaries.Representative',
        on_delete=models.CASCADE,
        related_name='face_embedding',
    )
    embedding_data = models.BinaryField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
    )

    class Meta:
        db_table = 'fans_rep_face_embeddings'

    def __str__(self):
        return f'Embedding for {self.representative.full_name}'


class UserFaceEmbedding(models.Model):
    """Encrypted FaceNet embedding for a logged-in user."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='user_face_embedding',
    )
    embedding_data = models.BinaryField()
    embedding_version = models.CharField(max_length=20, default='facenet-v1')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_user_face_embeddings',
    )

    class Meta:
        db_table = 'fans_user_face_embeddings'

    def __str__(self):
        return f'Face embedding for {self.user.username}'


class VerificationAttempt(models.Model):
    """
    Immutable audit record for a single face verification attempt.

    One record is created per submit regardless of outcome. Never deleted — the full
    history is preserved for audit and compliance purposes.

    Decision values:
      verified       — FaceNet similarity score >= threshold; ClaimRecord created.
      not_verified   — Score below threshold; retries may follow.
      manual_review  — Score in the review band, or a lookalike was detected.
                       Requires administrator action before stipend is released.
      denied         — Blocked by strict liveness failure, model not loaded,
                       or face processing error.

    Liveness fields (liveness_passed, liveness_score, anti_spoof_score,
    head_movement_completed) are always populated from client-reported values, even
    in Assisted Rollout Mode where a low score is non-blocking. This allows analysis
    of the liveness calibration data across real-world captures.

    demo_mode_active records whether Assisted Rollout Mode was active at the time
    of the attempt, so historical records remain interpretable after the mode changes.
    """
    DECISION_VERIFIED = 'verified'
    DECISION_NOT_VERIFIED = 'not_verified'
    DECISION_MANUAL_REVIEW = 'manual_review'
    DECISION_DENIED = 'denied'
    DECISION_CHOICES = [
        (DECISION_VERIFIED, 'Verified'),
        (DECISION_NOT_VERIFIED, 'Not Verified'),
        (DECISION_MANUAL_REVIEW, 'Manual Review'),
        (DECISION_DENIED, 'Denied'),
    ]

    CLAIMANT_BENEFICIARY = 'beneficiary'
    CLAIMANT_REPRESENTATIVE = 'representative'
    CLAIMANT_CHOICES = [
        (CLAIMANT_BENEFICIARY, 'Beneficiary'),
        (CLAIMANT_REPRESENTATIVE, 'Authorized Representative'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    beneficiary = models.ForeignKey(
        Beneficiary,
        on_delete=models.CASCADE,
        related_name='verification_attempts'
    )
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='performed_verifications'
    )

    # Linked stipend event (which payout period is being claimed)
    stipend_event = models.ForeignKey(
        StipendEvent,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='claims',
    )

    # Who is claiming
    claimant_type = models.CharField(
        max_length=20,
        choices=CLAIMANT_CHOICES,
        default=CLAIMANT_BENEFICIARY,
    )
    # Which representative was verified (null for beneficiary claimants)
    representative = models.ForeignKey(
        'beneficiaries.Representative',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='verification_attempts',
    )

    # Liveness result
    liveness_passed = models.BooleanField(null=True)
    liveness_score = models.FloatField(null=True, blank=True)
    anti_spoof_score = models.FloatField(null=True, blank=True)
    head_movement_completed = models.BooleanField(default=False)

    # Face matching
    similarity_score = models.FloatField(null=True, blank=True)
    threshold_used = models.FloatField(default=0.60)
    decision = models.CharField(max_length=20, choices=DECISION_CHOICES, null=True, blank=True, db_index=True)
    decision_reason = models.CharField(max_length=500, blank=True)

    # Template debug info (which stored template matched, how many were checked)
    matched_template = models.CharField(max_length=30, blank=True)
    templates_checked = models.PositiveSmallIntegerField(default=0)

    # Image quality at verification time
    face_quality_score = models.FloatField(null=True, blank=True)
    face_quality_ok = models.BooleanField(null=True, blank=True)

    # Retry tracking
    attempt_number = models.PositiveSmallIntegerField(default=1)
    session_id = models.UUIDField(default=uuid.uuid4)

    # Assisted Rollout Mode flag — clarifies in audit logs whether the assisted-rollout threshold was active
    demo_mode_active = models.BooleanField(
        default=False,
        help_text='True if Assisted Rollout Mode (DEMO_MODE) was active at the time of this verification (assisted-rollout threshold applied).'
    )

    # Fallback
    fallback_triggered = models.BooleanField(default=False)
    fallback_id_verified = models.BooleanField(null=True)
    fallback_id_type = models.CharField(max_length=50, blank=True)

    # Override
    overridden = models.BooleanField(default=False)
    override_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='overridden_verifications'
    )
    override_reason = models.TextField(blank=True)
    override_at = models.DateTimeField(null=True, blank=True)

    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    notes = models.TextField(blank=True)

    class Meta:
        db_table = 'fans_verification_attempts'
        ordering = ['-timestamp']
        indexes = [
            # Supports repeated-failure-per-beneficiary queries (Fraud Detection Phase 1).
            models.Index(fields=['beneficiary', 'decision', 'timestamp']),
            # Supports per-staff verification volume queries (Analytics + Fraud Detection).
            models.Index(fields=['performed_by', 'timestamp']),
        ]

    def __str__(self):
        return f'{self.beneficiary.full_name} - {self.decision} @ {self.timestamp}'


class AdditionalFaceEmbedding(models.Model):
    """
    Extra face templates for multi-shot matching.
    Added via the "Update Face Data" workflow when appearance changes or original
    registration quality was poor.  compare_with_all_embeddings() picks the BEST
    score across primary + all additional templates.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    beneficiary = models.ForeignKey(
        Beneficiary,
        on_delete=models.CASCADE,
        related_name='additional_embeddings',
    )
    embedding_data = models.BinaryField()
    embedding_version = models.CharField(max_length=20, default='facenet-v1')
    label = models.CharField(
        max_length=100, blank=True,
        help_text='Free-text label e.g. "update-2025-03"',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_additional_embeddings',
    )

    class Meta:
        db_table = 'fans_additional_face_embeddings'
        ordering = ['-created_at']

    def __str__(self):
        return f'Extra template for {self.beneficiary.full_name} ({self.created_at.date()})'


class FaceUpdateLog(models.Model):
    """
    Audit trail for every face re-enrollment event.
    Captures who triggered it, why, and whether it succeeded.
    """
    REASON_REPEATED_FAILURE  = 'repeated_failure'
    REASON_APPEARANCE_CHANGE = 'appearance_change'
    REASON_POOR_ORIGINAL     = 'poor_original'
    REASON_STAFF_DECISION    = 'staff_decision'
    REASON_CHOICES = [
        (REASON_REPEATED_FAILURE,  'Repeated Verification Failure'),
        (REASON_APPEARANCE_CHANGE, 'Major Appearance Change'),
        (REASON_POOR_ORIGINAL,     'Poor Original Registration'),
        (REASON_STAFF_DECISION,    'Staff Decision / Other'),
    ]

    ACTION_REPLACE  = 'replace'   # Replace primary embedding
    ACTION_AUGMENT  = 'augment'   # Add as additional template only
    ACTION_CHOICES = [
        (ACTION_REPLACE, 'Replace Primary Embedding'),
        (ACTION_AUGMENT, 'Add as Additional Template'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    beneficiary = models.ForeignKey(
        Beneficiary,
        on_delete=models.CASCADE,
        related_name='face_update_logs',
    )
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='face_updates_performed',
    )
    reason = models.CharField(max_length=30, choices=REASON_CHOICES)
    action = models.CharField(
        max_length=10,
        choices=ACTION_CHOICES,
        default=ACTION_REPLACE,
    )
    notes = models.TextField(blank=True)
    success = models.BooleanField(default=False)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'fans_face_update_logs'
        ordering = ['-timestamp']

    def __str__(self):
        status = 'OK' if self.success else 'FAILED'
        return (
            f'Face update [{status}] for {self.beneficiary.full_name} '
            f'by {self.performed_by} ({self.timestamp.date()})'
        )


class FaceUpdateRequest(models.Model):
    """
    Pending face re-enrollment request that must be approved by an admin before
    the new embedding replaces or augments the active face data.

    Workflow:
      Staff captures a new face on the Update Face page → a FaceUpdateRequest is
      created with status='pending' and the encrypted embedding stored here.
      Admin reviews and either approves (triggering the actual FaceEmbedding /
      AdditionalFaceEmbedding write) or rejects the request.
    """
    STATUS_PENDING  = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_CHOICES = [
        (STATUS_PENDING,  'Pending Review'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_REJECTED, 'Rejected'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    beneficiary = models.ForeignKey(
        Beneficiary,
        on_delete=models.CASCADE,
        related_name='face_update_requests',
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='submitted_face_update_requests',
    )

    # Same choices as FaceUpdateLog so we can create a FaceUpdateLog on approval
    reason = models.CharField(max_length=30, choices=FaceUpdateLog.REASON_CHOICES)
    action = models.CharField(max_length=10, choices=FaceUpdateLog.ACTION_CHOICES)
    notes = models.TextField(blank=True)

    # New embedding — stored encrypted, NOT applied until approved
    new_embedding_data = models.BinaryField()

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING)
    created_at = models.DateTimeField(auto_now_add=True)

    # Review fields (populated when admin acts)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='reviewed_face_update_requests',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)

    # ── Duplicate face check ─────────────────────────────────────────────────
    # The new capture is compared against every OTHER beneficiary's stored
    # embeddings before this request is created. A match doesn't block the
    # request — it's surfaced to the admin on the review screen so they decide,
    # same non-automatic pattern as Beneficiary.duplicate_match_beneficiary.
    duplicate_match_beneficiary = models.ForeignKey(
        Beneficiary,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='face_update_duplicate_flags',
        help_text='A different beneficiary whose face matched this new capture.',
    )
    duplicate_match_score = models.FloatField(
        null=True,
        blank=True,
        help_text='Face similarity score against duplicate_match_beneficiary (0-1).',
    )

    class Meta:
        db_table = 'fans_face_update_requests'
        ordering = ['-created_at']

    def __str__(self):
        return (
            f'FaceUpdateRequest [{self.status}] for {self.beneficiary.full_name} '
            f'by {self.requested_by} ({self.created_at.date()})'
        )


class ManualVerificationRequest(models.Model):
    """
    When a beneficiary's face scan fails and the fallback path is triggered, staff
    submits a ManualVerificationRequest instead of directly marking the attempt as
    verified.  An admin must approve before a stipend can be released.

    For REPRESENTATIVE claimants the ID-based path continues unchanged (no approval
    needed because face matching was never involved for representatives).
    """
    STATUS_PENDING  = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_CHOICES = [
        (STATUS_PENDING,  'Pending Review'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_REJECTED, 'Rejected'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    beneficiary = models.ForeignKey(
        Beneficiary,
        on_delete=models.CASCADE,
        related_name='manual_verification_requests',
    )
    claimant_type = models.CharField(
        max_length=20,
        choices=VerificationAttempt.CLAIMANT_CHOICES,
        default=VerificationAttempt.CLAIMANT_BENEFICIARY,
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='submitted_manual_verification_requests',
    )

    # Link to the failed verification attempt that triggered this request
    verification_attempt = models.ForeignKey(
        VerificationAttempt,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='manual_requests',
    )
    stipend_event = models.ForeignKey(
        StipendEvent,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='manual_verification_requests',
    )

    # What the staff member observed / verified offline
    reason = models.TextField(help_text='Why manual verification is being requested.')
    notes = models.TextField(blank=True)

    # Copy of key metrics from the failed attempt (for admin review context)
    similarity_score = models.FloatField(null=True, blank=True)
    liveness_passed = models.BooleanField(null=True)
    liveness_score = models.FloatField(null=True, blank=True)

    # ID check performed by staff during fallback
    id_type_checked = models.CharField(max_length=50, blank=True)
    id_verified = models.BooleanField(null=True)

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING)
    created_at = models.DateTimeField(auto_now_add=True)

    # Review fields (populated when admin acts)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='reviewed_manual_verification_requests',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)

    class Meta:
        db_table = 'fans_manual_verification_requests'
        ordering = ['-created_at']

    def __str__(self):
        return (
            f'ManualVerificationRequest [{self.status}] for {self.beneficiary.full_name} '
            f'by {self.requested_by} ({self.created_at.date()})'
        )


class ClaimRecord(models.Model):
    """
    Represents an actual completed stipend payout claim.
    Separate from VerificationAttempt (which is a raw attempt log).
    One claim per beneficiary per stipend event under normal circumstances;
    a second claim requires an approved SpecialClaimRequest.

    Payout lifecycle:
      pending_approval  → claimed (HB approves a queued no-event claim)
      claimed           → cancelled  (admin cancels — requires reason)
      claimed           → failed     (admin marks payout as failed — requires reason)
      pending_approval  → rejected   (HB rejects a queued no-event claim)

    Released/claimed records are *locked from normal staff edits* (see is_locked).
    Edits to a claimed record require an admin override with a written reason,
    captured in override_reason / override_by / override_at and audit-logged.
    """
    STATUS_CLAIMED          = 'claimed'
    STATUS_PENDING_APPROVAL = 'pending_approval'
    STATUS_REJECTED         = 'rejected'
    STATUS_CANCELLED        = 'cancelled'
    STATUS_FAILED           = 'failed'
    STATUS_CHOICES = [
        (STATUS_CLAIMED,          'Claimed / Released'),
        (STATUS_PENDING_APPROVAL, 'Pending Approval'),
        (STATUS_REJECTED,         'Rejected'),
        (STATUS_CANCELLED,        'Cancelled'),
        (STATUS_FAILED,           'Failed'),
    ]

    # How the beneficiary's identity was confirmed for this payout.
    VERIFY_FACE     = 'face_recognition'
    VERIFY_MANUAL   = 'manual_review'
    VERIFY_FALLBACK = 'fallback_id'
    VERIFY_OVERRIDE = 'admin_override'
    VERIFICATION_METHOD_CHOICES = [
        (VERIFY_FACE,     'Face Recognition'),
        (VERIFY_MANUAL,   'Manual Verification (Approved)'),
        (VERIFY_FALLBACK, 'Manual Verification (ID Check)'),
        (VERIFY_OVERRIDE, 'Admin Override'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    beneficiary = models.ForeignKey(
        Beneficiary,
        on_delete=models.CASCADE,
        related_name='claim_records',
    )
    stipend_event = models.ForeignKey(
        StipendEvent,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='claim_records',
    )
    claimant_type = models.CharField(
        max_length=20,
        choices=VerificationAttempt.CLAIMANT_CHOICES,
        default=VerificationAttempt.CLAIMANT_BENEFICIARY,
    )
    # Which representative was verified (null for beneficiary claimants)
    representative = models.ForeignKey(
        'beneficiaries.Representative',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='claim_records',
    )
    claimed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='processed_claims',
    )
    verification_attempt = models.ForeignKey(
        VerificationAttempt,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='claim_records',
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_CLAIMED)
    claimed_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='approved_claims',
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    is_special_additional = models.BooleanField(
        default=False,
        help_text='True if this is a second claim approved via SpecialClaimRequest.',
    )
    notes = models.TextField(blank=True)

    # ── Payout / Distribution fields ─────────────────────────────────────────
    amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text='PHP amount paid out for this claim (snapshot from StipendEvent.amount).',
    )
    reference_number = models.CharField(
        max_length=64, blank=True, db_index=True,
        help_text='Auto-generated reference / transaction number, e.g. RC-2026-05-00012.',
    )
    verification_method = models.CharField(
        max_length=30,
        choices=VERIFICATION_METHOD_CHOICES,
        default=VERIFY_FACE,
        help_text='How identity was confirmed for this payout.',
    )
    released_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='released_claims',
        help_text='Staff/admin who released the cash (defaults to claimed_by).',
    )
    released_at = models.DateTimeField(
        null=True, blank=True,
        help_text='Moment the payout was released to the beneficiary.',
    )
    payout_remarks = models.TextField(
        blank=True,
        help_text='Free-text remarks captured at release (kept distinct from the verification notes field).',
    )

    # Override audit — populated when an admin edits or changes status of a
    # claimed/released record. Required reason ensures non-blank rationale.
    override_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='claim_record_overrides',
    )
    override_reason = models.TextField(blank=True)
    override_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'fans_claim_records'
        ordering = ['-claimed_at']
        constraints = [
            # Prevent duplicate claimed payouts for the same beneficiary + event.
            # SpecialClaimRequest approvals set is_special_additional=True so
            # they are excluded from this constraint and may coexist.
            models.UniqueConstraint(
                fields=['beneficiary', 'stipend_event'],
                condition=models.Q(status='claimed', is_special_additional=False),
                name='unique_claimed_per_beneficiary_event',
            ),
        ]

    def __str__(self):
        event = self.stipend_event.title if self.stipend_event else 'No Event'
        return f'Claim [{self.status}] — {self.beneficiary.full_name} / {event}'

    @property
    def is_locked(self):
        """True when normal staff edits are forbidden — claimed/cancelled/failed/rejected
        are all terminal states that may only be modified via the admin override path."""
        return self.status in (
            self.STATUS_CLAIMED,
            self.STATUS_CANCELLED,
            self.STATUS_FAILED,
            self.STATUS_REJECTED,
        )

    @classmethod
    def generate_reference_number(cls, stipend_event=None, when=None):
        """Generate a stable RC-YYYY-MM-NNNNN reference. Race-safe via select_for_update
        if called inside an atomic block on PostgreSQL."""
        from django.utils import timezone as _tz
        when = when or _tz.now()
        prefix = f'RC-{when.year:04d}-{when.month:02d}-'
        last = (
            cls.objects
            .filter(reference_number__startswith=prefix)
            .order_by('-reference_number')
            .values_list('reference_number', flat=True)
            .first()
        )
        if last:
            try:
                last_num = int(last.split('-')[-1])
            except (ValueError, IndexError):
                last_num = 0
        else:
            last_num = 0
        return f'{prefix}{last_num + 1:05d}'


class SpecialClaimRequest(models.Model):
    """
    Request for a second (additional) claim for the same stipend event.
    Staff submits this; an admin must approve before the second ClaimRecord is created.
    """
    STATUS_PENDING  = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_CHOICES = [
        (STATUS_PENDING,  'Pending Review'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_REJECTED, 'Rejected'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    beneficiary = models.ForeignKey(
        Beneficiary,
        on_delete=models.CASCADE,
        related_name='special_claim_requests',
    )
    stipend_event = models.ForeignKey(
        StipendEvent,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='special_claim_requests',
    )
    original_claim = models.ForeignKey(
        ClaimRecord,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='special_requests',
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='submitted_special_claim_requests',
    )
    reason = models.TextField(help_text='Why a second claim is being requested.')
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING)
    created_at = models.DateTimeField(auto_now_add=True)

    # Review fields (populated when admin acts)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='reviewed_special_claim_requests',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)

    class Meta:
        db_table = 'fans_special_claim_requests'
        ordering = ['-created_at']

    def __str__(self):
        event = self.stipend_event.title if self.stipend_event else 'No Event'
        return (
            f'SpecialClaimRequest [{self.status}] for {self.beneficiary.full_name} '
            f'/ {event} by {self.requested_by} ({self.created_at.date()})'
        )


class LivenessTransaction(models.Model):
    """
    Server-issued liveness proof token.

    Created by verify_check_liveness when anti-spoofing and challenge validation
    pass.  The FaceNet embedding of the liveness-verified frame is stored here so
    verify_submit can use it directly — preventing face-switching between the
    liveness step and the final identity match.

    Security properties:
    - token is a random UUID (unguessable nonce)
    - expires_at enforces a short time window (default 120 s)
    - used_at / used_by_attempt: each token is single-use (replay prevention)
    - embedding_data: encrypted 512-d FaceNet vector from the liveness-approved frame
    - All frames sent to verify_submit must be consistent with this face via same-face check
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    token = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True)

    beneficiary = models.ForeignKey(
        Beneficiary,
        on_delete=models.CASCADE,
        related_name='liveness_transactions',
    )
    claimant_type = models.CharField(max_length=20, default='beneficiary')
    representative = models.ForeignKey(
        'beneficiaries.Representative',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='liveness_transactions',
    )
    stipend_event = models.ForeignKey(
        StipendEvent,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='liveness_transactions',
    )
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='liveness_transactions',
    )
    challenge_direction = models.CharField(max_length=20, blank=True)
    attempt_number = models.PositiveSmallIntegerField(default=1)

    # Liveness scores from the proof frame
    anti_spoof_score = models.FloatField(default=0.0)
    liveness_score = models.FloatField(default=0.0)
    pa_score = models.FloatField(default=0.0, help_text='Presentation attack heuristic score (0=clean, 1=suspicious).')
    pa_flags = models.JSONField(default=dict, blank=True)

    # Encrypted FaceNet embedding from the liveness-verified frame.
    # This is the face that WILL be used for identity matching in verify_submit.
    # Storing it here prevents a different face from being submitted after liveness.
    embedding_data = models.BinaryField(null=True, blank=True)

    # SHA-256 of the raw neutral+proof frame bytes that earned this TX
    # (v2.1.16 Security Hardening Round #4 — Blocker 1: replay defense).
    # A genuine live capture is essentially never byte-identical to any
    # other capture (sensor noise + JPEG re-encoding jitter differ every
    # time), so an exact match against a PRIOR transaction's evidence_hash
    # is treated as conclusive proof the same recorded frames are being
    # replayed rather than a fresh camera capture. Left blank for rows
    # created directly by tests/fixtures that bypass verify_check_liveness.
    evidence_hash = models.CharField(max_length=64, blank=True, default='', db_index=True)

    # SHA-256 of the DECODED pixel content of the neutral+proof frames (not
    # the raw file bytes) -- v2.1.16 Security Hardening Round #5 (Blocker 1).
    # Catches replays where only metadata was edited or the file was
    # re-saved losslessly: cases where the raw bytes differ (so evidence_hash
    # does not match) but the decoded image is pixel-for-pixel identical.
    evidence_pixel_hash = models.CharField(max_length=64, blank=True, default='', db_index=True)

    # Perceptual difference-hash (dHash, hex) of the neutral+proof frames —
    # v2.1.16 Security Hardening Round #5 (Blocker 1). Compared via Hamming
    # distance (see verification.liveness.hamming_distance_hex) against
    # recent transactions, not exact equality. Robust to JPEG recompression
    # and resizing, which change both the raw bytes and the exact pixel
    # values but leave the visual content effectively unchanged -- the one
    # replay variant neither evidence_hash nor evidence_pixel_hash can catch.
    evidence_phash = models.CharField(max_length=32, blank=True, default='', db_index=True)

    # The verification_session['session_id'] server-generated UUID (random,
    # unguessable, minted at verify_start, never client-suppliable) that was
    # active in the Django session when this transaction was issued —
    # v2.1.16 Security Hardening Round #5 (Blocker 1 nonce / Blocker 3
    # attempt binding). verify_submit requires this to match the CURRENT
    # session's session_id before honoring the token, so a token issued for
    # one verification attempt cannot be replayed against a later attempt
    # for the same beneficiary/claimant/event/operator. Left blank for rows
    # created directly by tests/fixtures that bypass verify_check_liveness —
    # those are not blocked by the session_id check (see verify_submit).
    session_id = models.CharField(max_length=64, blank=True, default='', db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)

    # Consumed fields — set when verify_submit uses this token
    used_at = models.DateTimeField(null=True, blank=True)
    used_by_attempt = models.ForeignKey(
        VerificationAttempt,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='liveness_transaction',
    )

    class Meta:
        db_table = 'fans_liveness_transactions'
        ordering = ['-created_at']
        constraints = [
            # v2.1.16 Security Hardening Round #5 (Blocker 2): DB-enforced
            # uniqueness is the FINAL authority against a replay race — an
            # exists()-then-create() check is two separate queries with no
            # lock between them, so two concurrent requests carrying
            # byte-identical (or pixel-identical) evidence can both pass the
            # pre-check and both attempt to create a row; only one INSERT
            # can satisfy a unique index, so the loser fails atomically with
            # IntegrityError (caught in verify_check_liveness) instead of
            # silently succeeding. Scoped to non-blank values only (via the
            # condition) so the many rows with '' evidence_hash/
            # evidence_pixel_hash (legacy data, LIVENESS_PROOF_REQUIRED=False
            # test/debug paths) never collide with each other.
            models.UniqueConstraint(
                fields=['evidence_hash'],
                condition=models.Q(evidence_hash__gt=''),
                name='unique_nonblank_liveness_evidence_hash',
            ),
            models.UniqueConstraint(
                fields=['evidence_pixel_hash'],
                condition=models.Q(evidence_pixel_hash__gt=''),
                name='unique_nonblank_liveness_evidence_pixel_hash',
            ),
        ]

    def __str__(self):
        return f'LivenessTX {self.token} [{self.beneficiary.full_name}] expires={self.expires_at}'

    @property
    def is_expired(self):
        from django.utils import timezone as _tz
        return _tz.now() >= self.expires_at

    @property
    def is_used(self):
        return self.used_at is not None

    @property
    def is_valid(self):
        return not self.is_expired and not self.is_used

    def claim(self):
        """
        Atomically mark this transaction as claimed (used_at set), WITHOUT
        requiring a VerificationAttempt row to exist yet.

        v2.1.16 (Security Hardening Round #3 — Blocker 2): verify_submit
        previously claimed token ownership only via consume(), called at the
        very end of the view after face comparison, decision logic, and
        ClaimRecord creation had already run. Two concurrent requests
        carrying the same tx_token would both pass the earlier
        is_used/is_expired checks, both perform that expensive work, and
        only then discover — via consume()'s return value — that one of them
        had lost the race. claim() is the same atomic conditional UPDATE as
        consume() (`used_at IS NULL` → now()), but is meant to be called
        FIRST, before any expensive verification work begins, so the losing
        request is rejected immediately instead of after doing the work.
        used_by_attempt is filled in afterwards via bind_attempt(), once a
        VerificationAttempt has been created and saved.

        Returns True if this call won the claim, False if the token was
        already used (by a prior request or a concurrent one that won the
        same race).
        """
        from django.utils import timezone as _tz
        now = _tz.now()
        updated = LivenessTransaction.objects.filter(
            pk=self.pk, used_at__isnull=True,
        ).update(used_at=now)
        if updated:
            self.used_at = now
        return bool(updated)

    def release_claim(self):
        """
        Undo an earlier claim() when verification could not proceed for a
        reason that has nothing to do with the proof itself (e.g. the face
        recognition model was unavailable) — so the operator can retry with
        the same liveness proof instead of repeating the whole challenge.

        Safe to call unconditionally: claim() is a compare-and-swap that only
        ever has one winner, so if this instance reached the point of calling
        release_claim() it is that winner, and no concurrent request could
        also be holding (or waiting to claim) this same token.
        """
        LivenessTransaction.objects.filter(pk=self.pk).update(used_at=None)
        self.used_at = None

    def bind_attempt(self, attempt):
        """
        Record which VerificationAttempt consumed this (already-claimed)
        transaction. Call only after claim() has succeeded and `attempt` has
        been saved (so it has a pk). Not itself concurrency-critical — claim()
        already excluded every other request from this token before this
        runs, so this is a plain unconditional update, not a compare-and-swap.
        """
        LivenessTransaction.objects.filter(pk=self.pk).update(used_by_attempt=attempt)
        self.used_by_attempt = attempt

    def consume(self, attempt):
        """
        Mark this transaction used-and-bound to `attempt` in one atomic step
        (claim() + bind_attempt() combined). Kept for callers that already
        have a saved attempt in hand and want single-step semantics.

        v2.1.16 (Security Hardening Round #2, H-03): this used to be a plain
        read-modify-write (`self.used_at = now(); self.save()`), which is not
        race-safe — two near-simultaneous requests holding the same tx_token
        could each check is_used=False before either wrote it, both
        proceeding as if the single-use proof were still theirs. The UPDATE
        below is a single atomic statement conditioned on `used_at__isnull=True`,
        so only one concurrent caller can ever win the claim regardless of DB
        backend (no long-held lock required).

        Returns True if this call won the claim, False if another request
        already consumed this token concurrently (the caller should treat
        that as a detected replay for audit/logging purposes).
        """
        from django.utils import timezone as _tz
        now = _tz.now()
        updated = LivenessTransaction.objects.filter(
            pk=self.pk, used_at__isnull=True,
        ).update(used_at=now, used_by_attempt=attempt)
        if updated:
            self.used_at = now
            self.used_by_attempt = attempt
        return bool(updated)


class LivenessEvidenceReservation(models.Model):
    """
    v2.1.16 Final Hardening Patch (Codex NO-GO #4): a short-lived, append-only
    "claim ticket" for a piece of replay-evidence (evidence_hash /
    evidence_pixel_hash / evidence_phash) written atomically — under
    verification.views._liveness_replay_lock — in the SAME critical section
    as the duplicate scan in verify_check_liveness, well before the eventual
    LivenessTransaction (which requires a resolved beneficiary, an expiry
    time, PAD/embedding results, etc. — all computed AFTER the scan) is
    created.

    Why this exists: LivenessTransaction.evidence_phash is compared by
    Hamming distance (near-duplicate), not exact equality, so it cannot
    carry a DB unique constraint the way evidence_hash/evidence_pixel_hash
    do. Without a reservation written immediately after the scan, two
    concurrent requests replaying the same (transformed/recompressed)
    evidence could both pass the scan — since neither has written anything
    yet — then both spend several seconds in the PAD/embedding pipeline and
    both successfully create a LivenessTransaction once they reach
    create(). Writing this row inside the same lock+scan step closes that
    window: whichever request reaches the lock first reserves the evidence
    before the other's scan can run.

    Rows are never deleted — see the same permanence rationale documented on
    LivenessTransaction.evidence_hash: a genuine live capture is never
    byte-identical, pixel-identical, nor perceptually near-identical to any
    earlier one, so a real duplicate here is always evidence, never noise.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    evidence_hash = models.CharField(max_length=64, blank=True, default='', db_index=True)
    evidence_pixel_hash = models.CharField(max_length=64, blank=True, default='', db_index=True)
    evidence_phash = models.CharField(max_length=32, blank=True, default='', db_index=True)
    reserved_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'fans_liveness_evidence_reservations'
        ordering = ['-reserved_at']
        constraints = [
            models.UniqueConstraint(
                fields=['evidence_hash'],
                condition=models.Q(evidence_hash__gt=''),
                name='unique_nonblank_reservation_evidence_hash',
            ),
            models.UniqueConstraint(
                fields=['evidence_pixel_hash'],
                condition=models.Q(evidence_pixel_hash__gt=''),
                name='unique_nonblank_reservation_evidence_pixel_hash',
            ),
        ]

    def __str__(self):
        return f'LivenessEvidenceReservation {self.id} reserved={self.reserved_at}'


class SystemConfig(models.Model):
    # Keys controlled through a dedicated, audited FANS-C application workflow
    # rather than free-form Django Admin editing (v2.1.16 correction, Codex
    # SystemConfig follow-up). Two categories:
    #   - verification_threshold / auto_verify_threshold: biometric acceptance /
    #     manual-review routing. verification.views.verify_config provides the
    #     audited workflow (admin-only, range-validated, ACTION_CONFIG_CHANGE
    #     logged) for verification_threshold; auto_verify_threshold has no
    #     dedicated UI workflow today, so Django Admin hardening is currently
    #     its only protection.
    #   - auto_approve_*: approval-workflow automation, controlled through
    #     beneficiaries.views.auto_approval_settings (admin-only, audit-logged
    #     via AuditLog.ACTION_CONFIG_CHANGE for every one of these four keys).
    # Blocking these in Django Admin does not affect either audited workflow --
    # both write to SystemConfig directly via set_value()/get_bool()/direct
    # get_or_create(), never through the admin interface. SystemConfigAdmin
    # blocks changing or deleting existing rows with these keys, blocks
    # creating new rows with these keys, and makes the `key` field read-only
    # on edit so a non-controlled row cannot be renamed into a controlled one.
    CONTROLLED_KEYS = frozenset({
        'verification_threshold',
        'auto_verify_threshold',
        'auto_approve_beneficiaries',
        'auto_approve_representatives',
        'auto_approve_face_enrollments',
        'auto_approve_user_accounts',
    })

    key = models.CharField(max_length=100, unique=True)
    value = models.CharField(max_length=500)
    description = models.TextField(blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'fans_system_config'

    def __str__(self):
        return f'{self.key} = {self.value}'

    @classmethod
    def get_bool(cls, key, default=False):
        """Return a SystemConfig value interpreted as a boolean."""
        try:
            return cls.objects.get(key=key).value.strip().lower() in ('true', '1', 'yes')
        except cls.DoesNotExist:
            return default

    @classmethod
    def set_value(cls, key, value, user=None, description=''):
        """Upsert a SystemConfig key."""
        obj, created = cls.objects.get_or_create(
            key=key,
            defaults={'value': str(value), 'description': description, 'updated_by': user},
        )
        if not created:
            obj.value = str(value)
            obj.updated_by = user
            obj.save()
        return obj

    @classmethod
    def get_threshold(cls):
        """
        Returns the active verification LOWER threshold (review-band ceiling).
        In Assisted Rollout Mode (DEMO_MODE=True), defaults to DEMO_THRESHOLD.
        Full enforcement mode defaults to VERIFICATION_THRESHOLD (0.75).
        Scores below this are denied; between this and AUTO_VERIFY threshold
        are routed to MANUAL_REVIEW (v2.1.13).
        """
        from django.conf import settings as django_settings
        demo_mode = getattr(django_settings, 'DEMO_MODE', False)  # Assisted Rollout Mode
        fallback = (
            getattr(django_settings, 'DEMO_THRESHOLD', 0.60) if demo_mode
            else getattr(django_settings, 'VERIFICATION_THRESHOLD', 0.75)
        )
        try:
            return float(cls.objects.get(key='verification_threshold').value)
        except cls.DoesNotExist:
            return fallback

    @classmethod
    def get_auto_verify_threshold(cls):
        """
        v2.1.13 (Issue 2) — Returns the AUTO-VERIFY threshold. A score must
        be at or above this value to mark the attempt as VERIFIED automatically
        and release the stipend. Scores below this (but at or above the lower
        threshold from get_threshold()) are routed to MANUAL_REVIEW so an
        administrator must approve before any release. This closes the
        wrong-person / baby-photo / low-quality false-accept window where
        FaceNet on webcam captures could land around 0.80-0.86.
        """
        from django.conf import settings as django_settings
        demo_mode = getattr(django_settings, 'DEMO_MODE', False)
        fallback = (
            getattr(django_settings, 'DEMO_AUTO_VERIFY_THRESHOLD', 0.80) if demo_mode
            else getattr(django_settings, 'AUTO_VERIFY_THRESHOLD', 0.88)
        )
        try:
            return float(cls.objects.get(key='auto_verify_threshold').value)
        except cls.DoesNotExist:
            return fallback


# ══════════════════════════════════════════════════════════════════════════
# BPA-1 — Controlled Biometric Evaluation Data Foundation
# ══════════════════════════════════════════════════════════════════════════
# These models are the DATA FOUNDATION for a future controlled biometric
# evaluation (Accuracy / FAR / FRR / TAR / manual-review-rate analysis).
# They intentionally do NOT compute or store any accuracy metric — no such
# metric exists yet. They exist to make GROUND TRUTH (what the research
# protocol independently knows) representable separately from SYSTEM
# DECISION (what FANS-C actually output), which ordinary VerificationAttempt
# rows cannot do: a NOT_VERIFIED result there is silent on whether the
# presenter was actually the enrolled person.
#
# These models are deliberately NOT wired into live Analytics
# (verification/analytics.py, template_analytics.py, the dashboard) and are
# NOT registered in Django Admin. See docs/BIOMETRIC-EVALUATION-METHODOLOGY.md
# for the full design rationale.

class EvaluationDataset(models.Model):
    """
    One controlled biometric evaluation study/dataset.

    Groups a set of EvaluationTrial rows collected under one protocol so
    future results can always be traced to which controlled study produced
    them, and so a partially-collected dataset is never presented as a
    finished evaluation result. Distinct from operational data — it has no
    relationship to live Analytics and does not affect any operational
    metric.

    Status semantics (data-collection lifecycle, not a workflow engine):
      DRAFT      — dataset defined, trial collection not yet started.
      COLLECTING — trials are actively being recorded.
      COMPLETED  — collection finished; dataset may be used for analysis.
      ARCHIVED   — retained for record-keeping, no longer active.

    Only COMPLETED datasets should ever be presented as a finished
    evaluation result once a future metrics checkpoint exists. DRAFT/
    COLLECTING datasets are incomplete by definition.

    A COMPLETED (or later ARCHIVED) dataset may still be amended by a
    post-finalization participant withdrawal (BPA-5.1) — status stays
    COMPLETED/ARCHIVED and completed_at is never rewritten, but
    has_post_finalization_amendment becomes true and must be visibly
    disclosed wherever this dataset's results are shown.
    """
    STATUS_DRAFT = 'draft'
    STATUS_COLLECTING = 'collecting'
    STATUS_COMPLETED = 'completed'
    STATUS_ARCHIVED = 'archived'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_COLLECTING, 'Collecting'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_ARCHIVED, 'Archived'),
    ]

    # ── Dataset purpose (BPA-5) ───────────────────────────────────────────
    # Distinguishes a real controlled research study from QA/synthetic/
    # developer/browser-test data and from pilot/calibration data used only
    # to tune a threshold. Every prior BPA checkpoint's own QA/browser-test
    # datasets were created and manually deleted afterward precisely because
    # no field existed to mark them as non-authoritative — see
    # docs/BIOMETRIC-EVALUATION-METHODOLOGY.md "BPA-5" for the audit finding
    # that motivated adding this field. PILOT_CALIBRATION and RESEARCH_STUDY
    # are kept as ONE field (not two) deliberately: a dataset's purpose and
    # its pilot/final status are the same research decision, not two
    # independent ones (see methodology doc, "Calibration vs. final
    # evaluation").
    PURPOSE_RESEARCH_STUDY = 'research_study'
    PURPOSE_PILOT_CALIBRATION = 'pilot_calibration'
    PURPOSE_QA_SYNTHETIC = 'qa_synthetic'
    PURPOSE_CHOICES = [
        (PURPOSE_RESEARCH_STUDY, 'Research Study (Final Evaluation)'),
        (PURPOSE_PILOT_CALIBRATION, 'Pilot / Calibration'),
        (PURPOSE_QA_SYNTHETIC, 'QA / Synthetic / Developer Test'),
    ]

    # v2.1.19 UX pass (section 21) — clearer user-facing labels/descriptions
    # without touching PURPOSE_CHOICES above (which is migration-relevant
    # field state; a wording-only change there would generate a needless
    # migration — same reasoning as CustomUser.get_role_display). Overrides
    # Django's auto-generated get_purpose_display() the same way that
    # precedent does.
    _PURPOSE_DISPLAY_OVERRIDE = {
        PURPOSE_RESEARCH_STUDY: 'Final Research Evaluation',
        PURPOSE_PILOT_CALIBRATION: 'Pilot / Calibration',
        PURPOSE_QA_SYNTHETIC: 'QA / Synthetic Test',
    }
    PURPOSE_DESCRIPTIONS = {
        PURPOSE_RESEARCH_STUDY: 'Controlled study intended for final research analysis.',
        PURPOSE_PILOT_CALIBRATION: 'Preliminary controlled testing used before final evaluation.',
        PURPOSE_QA_SYNTHETIC: (
            'Developer/system validation using synthetic or non-final test data. '
            'Not valid final research evidence.'
        ),
    }

    def get_purpose_display(self):
        return self._PURPOSE_DISPLAY_OVERRIDE.get(self.purpose, self.purpose)

    @property
    def purpose_description(self):
        return self.PURPOSE_DESCRIPTIONS.get(self.purpose, '')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200, help_text='Human-readable dataset/study title.')
    protocol_version = models.CharField(
        max_length=50,
        help_text='Identifier for the controlled-evaluation protocol/version this dataset follows.',
    )
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT, db_index=True)
    purpose = models.CharField(
        max_length=20, choices=PURPOSE_CHOICES, default=PURPOSE_QA_SYNTHETIC, db_index=True,
        help_text=(
            'Classifies this dataset so QA/synthetic/pilot data can never be mistaken for a '
            'real, final controlled study result. Defaults to QA_SYNTHETIC (the conservative '
            'choice for any dataset not explicitly marked otherwise) — a dataset is only ever '
            'treated as a final study result when it is BOTH purpose=RESEARCH_STUDY AND '
            'status=COMPLETED (see is_final_study_result).'
        ),
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='created_evaluation_datasets',
        help_text='Researcher/operator who defined this dataset.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(null=True, blank=True, help_text='When trial collection actually began.')
    completed_at = models.DateTimeField(
        null=True, blank=True,
        help_text=(
            'When the dataset was ORIGINALLY finalized. Immutable once set — never rewritten, '
            'even by a later post-finalization amendment (BPA-5.1), so the original finalization '
            'date always remains reconstructable.'
        ),
    )

    # ── Post-finalization amendment (BPA-5.1) ─────────────────────────────
    # A finalized (COMPLETED, and by extension ARCHIVED — the only status
    # reachable after COMPLETED) dataset's results must never appear to have
    # silently changed. Participant withdrawal must still be honored at any
    # time (research-integrity requirement — see evaluation_trial_withdraw),
    # so withdrawal after finalization is ALLOWED, not blocked; this field is
    # the disclosure mechanism instead of a workflow lock. `completed_at`
    # above is left untouched (original finalization date preserved);
    # `amended_after_finalization_at` records only the most recent
    # post-finalization withdrawal, and its mere presence (non-null) is the
    # durable signal — checked everywhere via `has_post_finalization_amendment`
    # — that whatever metrics are computed from this dataset now differ from
    # what was true at `completed_at`. Deliberately a single nullable
    # timestamp rather than a separate boolean flag: null/non-null already
    # answers "was this amended", and the timestamp itself answers "when
    # most recently" — a second field would duplicate that.
    amended_after_finalization_at = models.DateTimeField(
        null=True, blank=True,
        help_text=(
            'Set to the time of the most recent participant withdrawal recorded AFTER this '
            'dataset was originally finalized (completed_at). Null means the finalized result '
            'has never been amended. Never cleared once set — see '
            'has_post_finalization_amendment / evaluation_trial_withdraw.'
        ),
    )

    class Meta:
        db_table = 'fans_evaluation_datasets'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.name} [{self.status}] ({self.protocol_version})'

    @property
    def is_finalized(self) -> bool:
        """True only for COMPLETED datasets — the only status a future metrics
        checkpoint should treat as a finished evaluation result."""
        return self.status == self.STATUS_COMPLETED

    @property
    def can_accept_trials(self) -> bool:
        """True only while COLLECTING (BPA-2 workflow rule — see §6/§7 of the
        BPA-2 checkpoint instructions: DRAFT means configuration may still be
        edited but no trials exist yet; COMPLETED/ARCHIVED are closed)."""
        return self.status == self.STATUS_COLLECTING

    @property
    def is_final_study_result(self) -> bool:
        """True only when this dataset is BOTH finalized (status=COMPLETED)
        AND explicitly purposed as a real research study (BPA-5). A pilot/
        calibration dataset or a QA/synthetic dataset is never a final study
        result, no matter its status — this is the gate a future manuscript
        number must check before citing any rate from this dataset."""
        return self.is_finalized and self.purpose == self.PURPOSE_RESEARCH_STUDY

    @property
    def has_post_finalization_amendment(self) -> bool:
        """True once a participant withdrawal has been recorded AFTER this
        dataset was originally finalized (BPA-5.1). Does NOT change
        is_finalized/is_final_study_result — an amended finalized dataset is
        still a genuine finalized result, just one the UI must disclose as
        amended rather than presenting as an untouched original (see
        evaluation_dataset_detail.html / analytics_biometric.html banners)."""
        return self.amended_after_finalization_at is not None

    @property
    def active_trials(self):
        """This dataset's trials EXCLUDING participant-withdrawn rows (BPA-5).
        The biometric metric engine (verification/biometric_analytics.py)
        must use this — never `.trials` directly — for any rate/count
        computation, so a withdrawn trial can never silently contribute to a
        reported metric while its row is still retained for audit."""
        return self.trials.exclude(withdrawn=True)

    def clean(self):
        if self.status == self.STATUS_COMPLETED and not self.completed_at:
            raise ValidationError({'completed_at': 'completed_at is required once status is COMPLETED.'})


class EvaluationTrial(models.Model):
    """
    One controlled biometric evaluation trial.

    Keeps GROUND TRUTH (identity_ground_truth, presentation_ground_truth —
    what the research protocol independently knows about the presenter)
    strictly separate from SYSTEM OUTPUT (system_decision, similarity_score,
    liveness/anti-spoof snapshot — what FANS-C actually decided). Neither
    ground-truth field is ever derived from the system fields; the test
    operator/researcher sets them from the controlled protocol.

    This separation is what makes false-accept and false-reject
    representable at all:
      identity_ground_truth=GENUINE,  system_decision=NOT_VERIFIED  -> false reject
      identity_ground_truth=IMPOSTOR, system_decision=VERIFIED      -> false accept
      MANUAL_REVIEW is representable for either ground truth, so it is never
      silently folded into "failure".

    Privacy: stores only a pseudonymous participant_code and
    target_identity_code, never beneficiary PII (name/DOB/address/SC
    ID/phone/email) directly on this table. This is pseudonymization, not
    guaranteed anonymization — re-identification may remain possible via
    restricted application logic elsewhere. No raw face media (photo/video/
    frame) is stored here.

    verification_attempt is an optional link to the real operational
    VerificationAttempt this trial reused (rather than duplicating every
    field). on_delete=SET_NULL: deleting or nulling that operational record
    must never delete this research trial. Same reasoning for dataset's
    created_by user reference (SET_NULL) — a disabled/removed operator
    account must not take evaluation data with it. The dataset FK itself
    uses CASCADE because a trial has no independent meaning outside the
    dataset that defines its protocol; deleting a whole dataset is a
    deliberate research action, not an incidental one.
    """
    GROUND_TRUTH_GENUINE = 'genuine'
    GROUND_TRUTH_IMPOSTOR = 'impostor'
    IDENTITY_GROUND_TRUTH_CHOICES = [
        (GROUND_TRUTH_GENUINE, 'Genuine (enrolled presenter)'),
        (GROUND_TRUTH_IMPOSTOR, 'Impostor (different presenter)'),
    ]

    PRESENTATION_BONA_FIDE = 'bona_fide'
    PRESENTATION_PRINT_PHOTO = 'print_photo'
    PRESENTATION_SCREEN_REPLAY = 'screen_replay'
    PRESENTATION_OTHER_ATTACK = 'other_attack'
    PRESENTATION_NOT_TESTED = 'not_tested'
    PRESENTATION_GROUND_TRUTH_CHOICES = [
        (PRESENTATION_BONA_FIDE, 'Bona Fide / Live Presenter'),
        (PRESENTATION_PRINT_PHOTO, 'Print / Photo Attack'),
        (PRESENTATION_SCREEN_REPLAY, 'Screen Replay Attack'),
        (PRESENTATION_OTHER_ATTACK, 'Other Presentation Attack'),
        (PRESENTATION_NOT_TESTED, 'Not Tested / Not Applicable to This Trial'),
    ]

    TRIAL_STATUS_PENDING = 'pending'
    TRIAL_STATUS_COMPLETED = 'completed'
    TRIAL_STATUS_ABORTED = 'aborted'
    TRIAL_STATUS_CHOICES = [
        (TRIAL_STATUS_PENDING, 'Pending (attempt not yet run/recorded)'),
        (TRIAL_STATUS_COMPLETED, 'Completed (system output recorded)'),
        (TRIAL_STATUS_ABORTED, 'Aborted (technical failure — never reached a decision)'),
    ]

    HUMAN_REVIEW_CONFIRMED_GENUINE = 'confirmed_genuine'
    HUMAN_REVIEW_CONFIRMED_IMPOSTOR = 'confirmed_impostor'
    HUMAN_REVIEW_INCONCLUSIVE = 'inconclusive'
    HUMAN_REVIEW_CHOICES = [
        (HUMAN_REVIEW_CONFIRMED_GENUINE, 'Confirmed Genuine'),
        (HUMAN_REVIEW_CONFIRMED_IMPOSTOR, 'Confirmed Impostor'),
        (HUMAN_REVIEW_INCONCLUSIVE, 'Inconclusive'),
    ]

    id = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False,
        help_text='Unique trial identifier.',
    )
    dataset = models.ForeignKey(
        EvaluationDataset,
        on_delete=models.CASCADE,
        related_name='trials',
    )
    trial_status = models.CharField(
        max_length=20, choices=TRIAL_STATUS_CHOICES, default=TRIAL_STATUS_PENDING, db_index=True,
    )

    # ── Pseudonymous identifiers (no PII duplicated here) ────────────────────
    participant_code = models.CharField(
        max_length=100,
        help_text='Pseudonymous code identifying the presenter for this trial. Not a name/ID.',
    )
    target_identity_code = models.CharField(
        max_length=100,
        help_text='Pseudonymous code for the enrolled identity being claimed/compared against.',
    )

    # ── Restricted resolution of target_identity_code to a real enrolled
    # identity (BPA-2). NOT a duplication of PII — a relationship pointer,
    # the same pattern VerificationAttempt already uses. Needed so the
    # controlled trial can actually retrieve the real encrypted template(s)
    # to compare against. Access to these fields is restricted the same way
    # as any other Beneficiary/Representative FK in this codebase (staff
    # login required); see docs/BIOMETRIC-EVALUATION-METHODOLOGY.md §8 for
    # the privacy-wording correction this implies (pseudonymous, not
    # anonymous — these FKs are exactly the "restricted relationship" that
    # makes re-identification possible for authorized users). At most one of
    # the two should be set (validated in clean()).
    target_beneficiary = models.ForeignKey(
        Beneficiary,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='evaluation_trials_as_target',
        help_text='Real enrolled beneficiary this trial actually compares against, if applicable.',
    )
    target_representative = models.ForeignKey(
        'beneficiaries.Representative',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='evaluation_trials_as_target',
        help_text='Real enrolled representative this trial actually compares against, if applicable.',
    )

    # ── Ground truth (set by the research protocol, independent of system output) ──
    identity_ground_truth = models.CharField(max_length=20, choices=IDENTITY_GROUND_TRUTH_CHOICES, db_index=True)
    presentation_ground_truth = models.CharField(
        max_length=20, choices=PRESENTATION_GROUND_TRUTH_CHOICES, default=PRESENTATION_NOT_TESTED,
    )

    # ── System output snapshot ────────────────────────────────────────────────
    system_decision = models.CharField(
        max_length=20,
        choices=VerificationAttempt.DECISION_CHOICES,
        null=True, blank=True, db_index=True,
        help_text='FANS-C decision actually recorded for this trial. Null until the trial is run.',
    )
    similarity_score = models.FloatField(
        null=True, blank=True,
        validators=[MinValueValidator(-1.0), MaxValueValidator(1.0)],
        help_text=(
            'Raw FaceNet cosine similarity for this trial (legal domain [-1, 1] — see '
            'face_utils.cosine_similarity, never clamped). Null (not 0.0) when comparison '
            'never occurred, e.g. blocked by a liveness gate. Not to be confused with the '
            'positive-valued operating threshold configuration (BPA-3.2 §3).'
        ),
    )

    # ── Threshold snapshot (reproducible even if SystemConfig changes later) ──
    review_threshold_snapshot = models.FloatField(
        null=True, blank=True,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text='SystemConfig.get_threshold() value at trial time (manual-review floor).',
    )
    auto_verify_threshold_snapshot = models.FloatField(
        null=True, blank=True,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text='SystemConfig.get_auto_verify_threshold() value at trial time (auto-verify ceiling).',
    )

    # ── Liveness / anti-spoof / PAD snapshot ──────────────────────────────────
    liveness_passed = models.BooleanField(null=True, blank=True)
    liveness_score = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0.0), MaxValueValidator(1.0)])
    anti_spoof_passed = models.BooleanField(null=True, blank=True)
    anti_spoof_score = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0.0), MaxValueValidator(1.0)])
    pa_score = models.FloatField(
        null=True, blank=True, validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text='Presentation-attack heuristic score (0=clean, 1=suspicious), mirrors LivenessTransaction.pa_score.',
    )

    # ── Liveness proof / same-face binding (BPA-2.1) ──────────────────────────
    # Two-stage capture, mirroring production's LivenessTransaction pattern:
    # a liveness/neutral-frame embedding is captured and stored FIRST; the
    # final submission is only accepted once this proof exists, and identity
    # matching always uses THIS embedding (never the final frame's own
    # embedding) — exactly like verify_submit using liveness_tx.embedding_data.
    # The final frame is compared against this proof via cosine_similarity
    # (same-face check) before any target comparison runs. Encrypted vector
    # only — never a raw image (see docs/BIOMETRIC-EVALUATION-METHODOLOGY.md).
    liveness_proof_embedding = models.BinaryField(
        null=True, blank=True,
        help_text='Encrypted FaceNet embedding from the neutral/liveness-proof frame (stage 1). Used for both identity matching and the same-face check against the final frame.',
    )
    liveness_captured_at = models.DateTimeField(
        null=True, blank=True,
        help_text='When stage-1 liveness processing completed successfully (proof embedding stored).',
    )

    LIVENESS_PATHWAY_PASSIVE_ONLY = 'passive_only'
    LIVENESS_PATHWAY_ACTIVE_CHALLENGE = 'active_challenge'
    LIVENESS_PATHWAY_FAILED_PASSIVE = 'failed_passive'
    LIVENESS_PATHWAY_FAILED_ACTIVE = 'failed_active'
    LIVENESS_PATHWAY_CHOICES = [
        (LIVENESS_PATHWAY_PASSIVE_ONLY, 'Passive Only (anti-spoof + PAD, no active challenge attested)'),
        (LIVENESS_PATHWAY_ACTIVE_CHALLENGE, 'Active Challenge (head-movement challenge attested and combined into the liveness score)'),
        (LIVENESS_PATHWAY_FAILED_PASSIVE, 'Failed — passive anti-spoof/PAD gate rejected before any challenge was considered'),
        (LIVENESS_PATHWAY_FAILED_ACTIVE, 'Failed — active challenge was attempted but liveness validation still failed'),
    ]
    liveness_pathway = models.CharField(
        max_length=20, choices=LIVENESS_PATHWAY_CHOICES, blank=True,
        help_text=(
            'Which production liveness pathway this trial actually exercised. Production is '
            'risk-based (the interactive challenge is not shown on every real attempt); BPA-2.1 '
            'makes the researcher choose per trial and records which pathway ran, rather than '
            'silently forcing one policy — see BIOMETRIC-EVALUATION-METHODOLOGY.md.'
        ),
    )
    challenge_direction = models.CharField(
        max_length=20, blank=True,
        help_text='Challenge direction shown to the researcher for this trial (liveness.get_random_challenge()), if an active challenge was attempted.',
    )
    head_movement_completed = models.BooleanField(
        default=False,
        help_text='Researcher-attested: whether the head-movement challenge was actually performed. Mirrors VerificationAttempt.head_movement_completed.',
    )

    # ── Post-score decision audit (BPA-2.1) ───────────────────────────────────
    # decide_base_outcome() gives the raw matcher-only decision; production
    # (verify_submit) may then apply quality-override / lookalike-escalation /
    # representative-fallback-block before the FINAL decision is persisted.
    # system_decision is always the FINAL decision (parity with what
    # VerificationAttempt.decision would actually be). matcher_base_decision
    # preserves the raw pre-override value so BPA-3 can distinguish "matcher
    # performance" from "final automated FANS-C system performance" — see
    # BIOMETRIC-EVALUATION-METHODOLOGY.md §"Decision levels".
    matcher_base_decision = models.CharField(
        max_length=20, choices=VerificationAttempt.DECISION_CHOICES, null=True, blank=True,
        help_text='Raw decide_base_outcome() result before any post-score override. Null when no comparison was reached (pre-comparison denial).',
    )
    quality_override_applied = models.BooleanField(
        default=False,
        help_text='Whether the low-quality-forces-manual-review override changed the decision for this trial.',
    )
    lookalike_escalation_applied = models.BooleanField(
        default=False,
        help_text='Whether lookalike/duplicate-face escalation changed the decision for this trial.',
    )
    representative_fallback_blocked = models.BooleanField(
        default=False,
        help_text='True if a representative-target trial was denied because the live face matched the BENEFICIARY instead of the representative (never a valid match).',
    )

    # ── Timing placeholder (BPA-2) ────────────────────────────────────────────
    verification_duration_ms = models.PositiveIntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(0)],
        help_text=(
            'Placeholder for end-to-end verification timing in milliseconds. '
            'Not populated by BPA-1 — no timing instrumentation exists yet. '
            'Intended to be filled by BPA-2 once real capture-to-decision timing is measured. '
            'Must never be fabricated; leave null until a real measurement exists.'
        ),
    )

    # ── Optional link to the real operational attempt this trial reused ──────
    verification_attempt = models.ForeignKey(
        VerificationAttempt,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='evaluation_trials',
        help_text='Optional link to the real VerificationAttempt this trial reused, if the controlled workflow ran a live attempt rather than a fully separate test.',
    )

    # ── Optional supplementary human review (not part of the primary 3-outcome evaluation) ──
    human_review_outcome = models.CharField(max_length=20, choices=HUMAN_REVIEW_CHOICES, blank=True)
    human_review_notes = models.TextField(blank=True)
    human_review_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='human_reviewed_evaluation_trials',
    )
    human_review_at = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='created_evaluation_trials',
        help_text='Researcher/operator who administered this trial.',
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    # ── Server-authoritative timing (BPA-2) ───────────────────────────────────
    # See docs/BIOMETRIC-EVALUATION-METHODOLOGY.md §"Controlled Verification
    # Elapsed Time" for the exact metric definition. evaluation_started_at is
    # stamped when the runner view authorizes the trial to begin (server
    # clock); verification_duration_ms = evaluated_at - evaluation_started_at,
    # computed server-side, never client-supplied.
    evaluation_started_at = models.DateTimeField(
        null=True, blank=True,
        help_text='Server-authoritative start of the evaluation verification attempt (set when the trial runner is authorized to begin).',
    )
    evaluation_session_token = models.UUIDField(
        null=True, blank=True, unique=True,
        help_text=(
            'Server-issued token binding the capture/submit step to this specific '
            'trial (same pattern as LivenessTransaction.token) so a final submission '
            'cannot be accidentally attached to a different trial. Checked, not reused, '
            'once trial_status leaves PENDING.'
        ),
    )

    evaluated_at = models.DateTimeField(
        null=True, blank=True,
        help_text='When the system output for this trial was actually recorded (distinct from created_at).',
    )
    notes = models.TextField(blank=True)

    # ── Participant withdrawal (BPA-5) ────────────────────────────────────
    # Withdrawal is represented as an exclusion flag, never a physical
    # delete: the row (and its audit trail) is retained for research
    # integrity/auditability, but verification/biometric_analytics.py MUST
    # exclude withdrawn=True trials from every rate/count it computes (see
    # EvaluationDataset.active_trials). A trial may be withdrawn regardless
    # of trial_status — a participant may withdraw before their trial ever
    # ran, or after a result was already recorded.
    withdrawn = models.BooleanField(
        default=False, db_index=True,
        help_text='True once this trial has been withdrawn/excluded by participant request. Excluded from all biometric metric calculations; the row itself is never deleted.',
    )
    withdrawal_reason = models.TextField(
        blank=True,
        help_text='Reason recorded for withdrawal (e.g. "participant withdrew consent"). Required whenever withdrawn=True.',
    )
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    withdrawn_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='withdrawn_evaluation_trials',
        help_text='Staff member (President-tier — see views._evaluation_admin_required/is_president) who recorded the withdrawal.',
    )

    class Meta:
        db_table = 'fans_evaluation_trials'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['dataset', 'identity_ground_truth']),
            models.Index(fields=['dataset', 'system_decision']),
            models.Index(fields=['dataset', 'withdrawn']),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(verification_duration_ms__isnull=True) | models.Q(verification_duration_ms__gte=0),
                name='evaluationtrial_duration_non_negative',
            ),
        ]

    def __str__(self):
        return f'Trial {self.id} [{self.identity_ground_truth} / {self.system_decision or "pending"}] ({self.dataset.name})'

    def clean(self):
        errors = {}
        if not self.participant_code or not self.participant_code.strip():
            errors['participant_code'] = 'participant_code is required and must be non-empty.'
        if not self.target_identity_code or not self.target_identity_code.strip():
            errors['target_identity_code'] = 'target_identity_code is required and must be non-empty.'
        if self.trial_status == self.TRIAL_STATUS_COMPLETED and not self.system_decision:
            errors['system_decision'] = 'system_decision is required once trial_status is COMPLETED.'
        # ABORTED = a technical failure that never reached a decision — distinct
        # from a legitimate COMPLETED+DENIED security decision. Neither a
        # system_decision nor a fabricated duration belongs on an aborted trial.
        if self.trial_status == self.TRIAL_STATUS_ABORTED:
            if self.system_decision:
                errors['system_decision'] = 'system_decision must be blank for an ABORTED trial (it never reached a decision).'
            if self.verification_duration_ms is not None:
                errors['verification_duration_ms'] = 'verification_duration_ms must be null for an ABORTED trial.'
        # A duration is only meaningful once the trial actually completed.
        if self.trial_status != self.TRIAL_STATUS_COMPLETED and self.verification_duration_ms is not None:
            errors['verification_duration_ms'] = 'verification_duration_ms may only be set once trial_status is COMPLETED.'
        if self.target_beneficiary_id and self.target_representative_id:
            errors['target_representative'] = 'A trial may target a beneficiary or a representative, not both.'
        if self.withdrawn and not (self.withdrawal_reason or '').strip():
            errors['withdrawal_reason'] = 'withdrawal_reason is required whenever withdrawn is True.'
        if errors:
            raise ValidationError(errors)
