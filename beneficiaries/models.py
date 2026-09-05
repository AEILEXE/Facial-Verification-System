"""
Beneficiary data models for FANS-C.

Beneficiary        — core record for each senior citizen; includes personal info,
                     address, status, consent flag, and offline-sync tracking fields.
Representative     — authorised representative who may claim on behalf of a beneficiary.
                     Representatives have their own face embedding for biometric verification.

Sync-state fields (sync_status, sync_error, last_synced_at, offline_device,
sync_attempted_at) track the lifecycle of offline-created records as they move
through the central-server sync pipeline.  See beneficiaries/sync.py and the
`sync_beneficiaries` management command for full details.

Sync state machine:
  pending_sync  — record created on this device, not yet accepted by the central server
  synced        — central server accepted the record (HTTP 200/201)
  sync_conflict — central server returned 409 (conflicting data already on server)
  sync_rejected — central server returned 400/422 (invalid data, permanently rejected)

In centralized deployment (SYNC_API_URL not configured) the sync pipeline is
dormant and all records remain at pending_sync; this is harmless because no
sync is ever attempted.  Admin-facing sync UI is only shown when conflicts or
rejections are actually present.
"""
from django.db import models
from django.conf import settings
import uuid


class Beneficiary(models.Model):
    """
    Core record for a senior citizen enrolled in the Quezon City stipend programme.

    Status lifecycle:
      pending  → active  (after registration review and face enrollment)
      active   → inactive (suspended by admin)
      active   → deceased (recorded upon notification)

    is_eligible_to_claim returns True only for STATUS_ACTIVE beneficiaries with consent.
    Only active beneficiaries appear in verification search results.
    """
    STATUS_ACTIVE = 'active'
    STATUS_INACTIVE = 'inactive'
    STATUS_DECEASED = 'deceased'
    STATUS_PENDING = 'pending'
    # v2.2.0 Post-UAT Phase 4 — a registration that was never approved must
    # never be presented as a beneficiary that WAS active and later became
    # inactive; those are different facts about the record. Only set on a
    # STATUS_PENDING record being rejected during registration/duplicate
    # review — never on a beneficiary that was previously STATUS_ACTIVE.
    STATUS_DISAPPROVED = 'disapproved'
    STATUS_CHOICES = [
        (STATUS_ACTIVE, 'Active'),
        (STATUS_INACTIVE, 'Inactive'),
        (STATUS_DECEASED, 'Deceased'),
        (STATUS_PENDING, 'Pending'),
        (STATUS_DISAPPROVED, 'Disapproved'),
    ]

    # Sync state machine — managed exclusively by beneficiaries/sync.py
    SYNC_PENDING   = 'pending_sync'    # not yet accepted by central server
    SYNC_SYNCED    = 'synced'          # central server accepted (HTTP 200/201)
    SYNC_CONFLICT  = 'sync_conflict'   # server returned 409 (conflicting record)
    SYNC_REJECTED  = 'sync_rejected'   # server returned 400/422 (invalid data)
    SYNC_STATUS_CHOICES = [
        (SYNC_PENDING,  'Pending Sync'),
        (SYNC_SYNCED,   'Synced'),
        (SYNC_CONFLICT, 'Sync Conflict'),
        (SYNC_REJECTED, 'Sync Rejected'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    beneficiary_id = models.CharField(max_length=20, unique=True)

    # Personal info
    first_name = models.CharField(max_length=100)
    middle_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100)
    date_of_birth = models.DateField()
    gender = models.CharField(max_length=10, choices=[('M', 'Male'), ('F', 'Female'), ('O', 'Other')])
    house_no = models.CharField(max_length=100, blank=True, verbose_name='House No.')
    street = models.CharField(max_length=200, blank=True, verbose_name='Street')
    address = models.TextField(blank=True)
    barangay = models.CharField(max_length=100)
    municipality = models.CharField(max_length=100)
    province = models.CharField(max_length=100)
    contact_number = models.CharField(max_length=20, blank=True)

    # Government ID
    senior_citizen_id = models.CharField(max_length=50, blank=True, verbose_name='Senior Citizen ID Number')
    valid_id_type = models.CharField(max_length=50, blank=True, verbose_name='Valid ID Type')
    valid_id_number = models.CharField(max_length=50, blank=True, verbose_name='Valid ID Number')

    # Representative info (authorized to claim on behalf)
    has_representative = models.BooleanField(default=False)
    rep_first_name = models.CharField(max_length=100, blank=True)
    rep_last_name = models.CharField(max_length=100, blank=True)
    rep_relationship = models.CharField(max_length=100, blank=True)
    rep_contact = models.CharField(max_length=20, blank=True)
    rep_id_type = models.CharField(max_length=50, blank=True)
    rep_id_number = models.CharField(max_length=50, blank=True)

    # Consent
    consent_given = models.BooleanField(default=False)
    consent_date = models.DateTimeField(null=True, blank=True)

    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_PENDING)

    # Lifecycle deactivation tracking
    deactivated_at = models.DateTimeField(null=True, blank=True)
    deactivated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='deactivated_beneficiaries',
    )
    deactivated_reason = models.TextField(blank=True)

    # ── Offline sync tracking ────────────────────────────────────────────────
    # Managed exclusively by beneficiaries/sync.py — do not write these fields
    # from any other code path.
    sync_status = models.CharField(
        max_length=15,
        choices=SYNC_STATUS_CHOICES,
        default=SYNC_PENDING,
        db_index=True,
        help_text='Current sync state (see SYNC_* constants). Set by sync.py.',
    )
    sync_error = models.TextField(
        blank=True,
        help_text=(
            'Human-readable reason for the last sync failure, conflict, or rejection. '
            'Cleared on successful sync.'
        ),
    )
    last_synced_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Timestamp of the last successful sync to the central server.',
    )
    # Offline-device audit: which workstation created this record offline.
    # Empty for records created directly on the central server.
    offline_device = models.CharField(
        max_length=255,
        blank=True,
        help_text='Hostname of the offline workstation that created this record, if any.',
    )
    # Timestamp of the most recent sync attempt (success or failure).
    sync_attempted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Timestamp of the most recent sync attempt (any outcome).',
    )

    profile_picture = models.ImageField(
        upload_to='beneficiaries/profile_pics/',
        null=True,
        blank=True,
        help_text='Optional profile photo for identification.',
    )

    # ── Duplicate face review ────────────────────────────────────────────────
    # When a new registration matches an existing face embedding, the record is
    # saved as pending with duplicate_review_required=True instead of hard-blocking.
    # Admin must review and approve as a legitimate twin/lookalike or reject as fraud.
    duplicate_review_required = models.BooleanField(
        default=False,
        help_text='True when a face duplicate was detected at registration. Requires admin review.',
    )
    duplicate_match_beneficiary = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='duplicate_registrations',
        help_text='Existing beneficiary whose face matched this registration.',
    )
    duplicate_match_score = models.FloatField(
        null=True,
        blank=True,
        help_text='Face similarity score between this record and the matched beneficiary (0-1).',
    )
    duplicate_review_notes = models.TextField(
        blank=True,
        help_text='Admin notes recorded during duplicate review.',
    )
    duplicate_reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='duplicate_reviews_performed',
        help_text='Admin who reviewed and resolved the duplicate flag.',
    )
    duplicate_reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Timestamp when the duplicate review was completed.',
    )

    registered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='registered_beneficiaries'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def full_address(self) -> str:
        """Human-readable address: House No., Street, Barangay, City."""
        parts = []
        if self.house_no:
            parts.append(self.house_no)
        if self.street:
            parts.append(self.street)
        elif self.address:
            parts.append(self.address)
        if self.barangay:
            parts.append(f'Barangay {self.barangay}')
        if self.municipality:
            parts.append(self.municipality)
        return ', '.join(parts) if parts else ''

    class Meta:
        db_table = 'fans_beneficiaries'
        ordering = ['last_name', 'first_name']
        constraints = [
            # Enforce uniqueness only for non-empty Senior Citizen IDs.
            # This is a partial unique index: multiple beneficiaries may have
            # an empty senior_citizen_id (field is optional), but no two
            # beneficiaries may share the same non-empty value.
            # Prevents the app-level duplicate check from being bypassed by
            # concurrent registrations from multiple staff stations.
            models.UniqueConstraint(
                fields=['senior_citizen_id'],
                condition=models.Q(senior_citizen_id__gt=''),
                name='unique_nonempty_senior_citizen_id',
            ),
        ]

    @property
    def full_name(self):
        parts = [self.first_name, self.middle_name, self.last_name]
        return ' '.join(p for p in parts if p)

    @property
    def rep_full_name(self):
        """Full name of the authorized representative, or empty string."""
        parts = [self.rep_first_name, self.rep_last_name]
        return ' '.join(p for p in parts if p)

    @property
    def age(self):
        """Age in years as of today."""
        import datetime
        today = datetime.date.today()
        dob = self.date_of_birth
        return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

    @property
    def is_senior_citizen(self):
        """True if the beneficiary is 60 years old or older."""
        return self.age >= 60

    @property
    def is_eligible_to_claim(self):
        """
        Only active beneficiaries who have given consent, and have no
        unresolved duplicate-face conflict, may claim.
        Inactive, deceased, and pending beneficiaries are blocked.

        v2.2.0 Follow-up Issue 33: duplicate_review_required must gate claim
        eligibility here, at the single source of truth every claim-adjacent
        view checks, rather than relying only on the registration-approval
        gate (verification.views.registration_review) to prevent an
        ACTIVE-but-still-flagged record from existing. This is defense in
        depth — by design duplicate_review_required is only ever True
        alongside STATUS_PENDING, but a legacy/inconsistent record must never
        be able to verify or release a stipend through an unresolved
        ambiguous identity regardless of how it reached that state.
        """
        return (
            self.status == self.STATUS_ACTIVE
            and self.consent_given
            and not self.duplicate_review_required
        )

    @property
    def is_synced(self) -> bool:
        """Convenience property — True when sync_status == SYNC_SYNCED."""
        return self.sync_status == self.SYNC_SYNCED

    def is_eligible_for_event(self, stipend_event) -> bool:
        """
        Check if this beneficiary is eligible for the given stipend event.
        Combines lifecycle eligibility and event-specific rules.
        """
        if not self.is_eligible_to_claim:
            return False
        return stipend_event.is_beneficiary_eligible(self)

    def save(self, *args, **kwargs):
        if not self.beneficiary_id:
            import datetime
            from django.db import transaction
            year = datetime.date.today().year
            with transaction.atomic():
                # Lock existing IDs for this year so concurrent registrations
                # from multiple staff stations cannot read the same "last" value
                # and produce a duplicate beneficiary_id.
                # select_for_update() serializes writes on PostgreSQL (shared
                # central DB); it is a no-op on SQLite (local dev only).
                last = (
                    Beneficiary.objects
                    .select_for_update()
                    .filter(beneficiary_id__startswith=f'BEN-{year}-')
                    .order_by('-beneficiary_id')
                    .values_list('beneficiary_id', flat=True)
                    .first()
                )
                if last:
                    try:
                        last_num = int(last.split('-')[-1])
                    except (ValueError, IndexError):
                        last_num = 0
                else:
                    last_num = 0
                self.beneficiary_id = f'BEN-{year}-{last_num + 1:05d}'
                super().save(*args, **kwargs)
        else:
            super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.full_name} ({self.beneficiary_id})'


class DuplicateNameDobRequest(models.Model):
    """
    Audit + approval record for a registration where a same-name + same-DOB
    duplicate was detected but staff confirmed the new applicant is a different
    person. The new beneficiary is created in STATUS_PENDING and remains
    pending until reviewed by President/Admin.

    Workflow:
      Staff fills registration → name+DOB collision detected →
      staff clicks "Different Person / Submit Override Request" →
      written reason + distinguishing info captured → registration proceeds in
      PENDING state with a DuplicateNameDobRequest row attached →
      President/Admin reviews on the Duplicate Override Review page → approves
      (activate beneficiary) or rejects (mark deactivated).
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
    new_beneficiary = models.OneToOneField(
        Beneficiary,
        on_delete=models.CASCADE,
        related_name='duplicate_namedob_request',
        help_text='The newly-created beneficiary record awaiting review.',
    )
    existing_beneficiary = models.ForeignKey(
        Beneficiary,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='namedob_collisions_as_existing',
        help_text='The existing beneficiary that shares the same name and DOB.',
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='submitted_namedob_override_requests',
    )
    reason = models.TextField(
        help_text='Written reason from staff: why this is a different person.',
    )
    distinguishing_info = models.TextField(
        blank=True,
        help_text='Optional: address / contact / ID details that distinguish the two records.',
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='reviewed_namedob_override_requests',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)

    class Meta:
        db_table = 'fans_duplicate_namedob_requests'
        ordering = ['-created_at']

    def __str__(self):
        return (
            f'DuplicateNameDobRequest [{self.status}] for '
            f'{self.new_beneficiary.full_name} '
            f'(matches {self.existing_beneficiary.beneficiary_id if self.existing_beneficiary else "?"})'
        )


class Representative(models.Model):
    """
    A biometrically-registered representative authorized to claim on behalf of a beneficiary.
    Each representative must have face data captured before they can be used in verification.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    beneficiary = models.ForeignKey(
        Beneficiary,
        on_delete=models.CASCADE,
        related_name='representatives',
    )
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    relationship = models.CharField(max_length=100)
    contact_number = models.CharField(max_length=20)
    valid_id_type = models.CharField(max_length=50)
    valid_id_number = models.CharField(max_length=50)
    is_active = models.BooleanField(default=True)
    registered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='registered_representatives',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'fans_representatives'
        ordering = ['last_name', 'first_name']

    # ── Shared-representative review (added 2026-05-28) ───────────────────────
    # When the same person is enrolled as a representative for more than one
    # beneficiary, the registration is flagged for admin review instead of being
    # silently blocked. A representative may legitimately claim for multiple
    # senior citizens (child, spouse, caregiver, authorised family member).
    # Until an admin approves the shared linkage, verification using this
    # representative is blocked.
    SHARED_PENDING       = 'pending_review'
    SHARED_APPROVED      = 'approved'
    SHARED_REJECTED      = 'rejected'
    SHARED_DOCS_REQUIRED = 'docs_required'
    SHARED_BLOCKED       = 'suspicious_blocked'
    SHARED_NONE          = 'none'
    SHARED_REVIEW_CHOICES = [
        (SHARED_NONE,          'Not Shared'),
        (SHARED_PENDING,       'Pending Representative Review'),
        (SHARED_APPROVED,      'Shared Representative Approved'),
        (SHARED_REJECTED,      'Shared Representative Rejected'),
        (SHARED_DOCS_REQUIRED, 'Authorization Document Required'),
        (SHARED_BLOCKED,       'Suspicious Representative Blocked'),
    ]
    shared_review_status = models.CharField(
        max_length=20,
        choices=SHARED_REVIEW_CHOICES,
        default=SHARED_NONE,
        db_index=True,
        help_text=(
            'Set automatically when this representative\'s face matches an existing '
            'representative for a different beneficiary. Verification is blocked '
            'while status is pending/blocked/docs_required.'
        ),
    )

    @property
    def full_name(self):
        return f'{self.first_name} {self.last_name}'.strip()

    @property
    def has_face_data(self):
        return hasattr(self, 'face_embedding')

    @property
    def is_blocked_for_review(self):
        """True when verification using this rep must be denied pending admin action."""
        return self.shared_review_status in (
            self.SHARED_PENDING, self.SHARED_BLOCKED, self.SHARED_DOCS_REQUIRED,
        )

    @property
    def is_shared_approved(self):
        return self.shared_review_status == self.SHARED_APPROVED

    def __str__(self):
        return f'{self.full_name} (Rep for {self.beneficiary.beneficiary_id})'


class SharedRepresentativeReview(models.Model):
    """
    Admin review record created when a representative's face is detected as
    already enrolled for a different beneficiary. Holds the evidence for an
    admin to approve a legitimate shared linkage (child claiming for both
    parents, caregiver for multiple seniors) or reject a suspicious one.
    """
    STATUS_PENDING       = Representative.SHARED_PENDING
    STATUS_APPROVED      = Representative.SHARED_APPROVED
    STATUS_REJECTED      = Representative.SHARED_REJECTED
    STATUS_DOCS_REQUIRED = Representative.SHARED_DOCS_REQUIRED
    STATUS_BLOCKED       = Representative.SHARED_BLOCKED
    STATUS_CHOICES = [
        (STATUS_PENDING,       'Pending Representative Review'),
        (STATUS_APPROVED,      'Shared Representative Approved'),
        (STATUS_REJECTED,      'Shared Representative Rejected'),
        (STATUS_DOCS_REQUIRED, 'Authorization Document Required'),
        (STATUS_BLOCKED,       'Suspicious Representative Blocked'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    representative = models.ForeignKey(
        Representative,
        on_delete=models.CASCADE,
        related_name='shared_reviews',
        help_text='The newly-registered representative whose face matched an existing one.',
    )
    matched_beneficiary_id = models.CharField(
        max_length=50,
        help_text='Public beneficiary_id of the existing beneficiary already represented.',
    )
    matched_beneficiary_name = models.CharField(max_length=200, blank=True)
    matched_score = models.FloatField(default=0.0)
    matched_threshold = models.FloatField(default=0.0)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )
    flag_reason = models.TextField(
        blank=True,
        help_text='Why the system flagged this case (auto-generated).',
    )
    flagged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='shared_rep_flags',
    )
    flagged_at = models.DateTimeField(auto_now_add=True)

    decision_notes = models.TextField(
        blank=True,
        help_text='Admin notes recorded with the decision.',
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='shared_rep_decisions',
    )
    decided_at = models.DateTimeField(null=True, blank=True)

    # Optional uploaded authorization document (legal proof the same person
    # may represent multiple beneficiaries — e.g. authorization letter,
    # legal guardianship document).
    authorization_document = models.FileField(
        upload_to='representatives/authorization/',
        null=True, blank=True,
    )

    class Meta:
        db_table = 'fans_shared_representative_reviews'
        ordering = ['-flagged_at']

    def __str__(self):
        return f'{self.representative.full_name} ↔ {self.matched_beneficiary_id} [{self.get_status_display()}]'
