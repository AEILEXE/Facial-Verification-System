from django.contrib import admin
from django.core.exceptions import PermissionDenied

from .models import FaceEmbedding, VerificationAttempt, ClaimRecord, SystemConfig


class _ReadOnlyAdmin(admin.ModelAdmin):
    """Base class for models that must never be written through Django admin.

    IT can inspect records for debugging; all write paths (add, change, delete,
    bulk-delete) are blocked here. Application views with proper SoD, audit
    logging, and transaction guards must be used instead.
    """

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        # Remove the default "Delete selected" bulk action inherited from ModelAdmin.
        actions = super().get_actions(request)
        actions.pop('delete_selected', None)
        return actions


@admin.register(FaceEmbedding)
class FaceEmbeddingAdmin(_ReadOnlyAdmin):
    """View-only. Biometric data must only be written through the registration flow."""
    list_display = ['beneficiary', 'embedding_version', 'created_at', 'created_by']
    readonly_fields = [
        'id', 'beneficiary', 'embedding_data', 'embedding_version',
        'created_at', 'updated_at', 'created_by',
    ]


@admin.register(VerificationAttempt)
class VerificationAttemptAdmin(_ReadOnlyAdmin):
    """View-only. VerificationAttempt is an immutable audit record.

    Editing fields like decision or overridden through Django admin bypasses
    admin_override() SoD rules, ACTION_OVERRIDE audit logging, and the
    two-step override + release workflow. All fields are read-only.
    """
    list_display = ['beneficiary', 'decision', 'similarity_score', 'liveness_passed', 'timestamp']
    list_filter = ['decision', 'liveness_passed', 'overridden']
    readonly_fields = [
        'id', 'timestamp', 'beneficiary', 'performed_by',
        'stipend_event', 'claimant_type', 'representative',
        'decision', 'decision_reason', 'similarity_score', 'threshold_used',
        'liveness_passed', 'liveness_score', 'anti_spoof_score', 'head_movement_completed',
        'face_quality_score', 'face_quality_ok',
        'matched_template', 'templates_checked',
        'attempt_number', 'session_id', 'demo_mode_active',
        'fallback_triggered', 'fallback_id_verified', 'fallback_id_type',
        'overridden', 'override_by', 'override_reason', 'override_at',
        'notes',
    ]


@admin.register(ClaimRecord)
class ClaimRecordAdmin(_ReadOnlyAdmin):
    """View-only. ClaimRecord must only be created through the verified payout workflow.

    Editing claim records through Django admin bypasses the duplicate guard,
    transaction.atomic() + select_for_update() protection, and ACTION_CLAIM
    audit logging. Only view access is permitted here.
    """
    list_display = ['beneficiary', 'stipend_event', 'status', 'amount', 'claimed_at', 'claimed_by']
    list_filter = ['status', 'verification_method']
    readonly_fields = [
        'id', 'beneficiary', 'stipend_event', 'claimant_type', 'representative',
        'status', 'amount', 'claimed_at', 'claimed_by',
        'approved_by', 'approved_at',
        'verification_method', 'verification_attempt',
        'reference_number', 'notes',
        'is_special_additional',
    ]


@admin.register(SystemConfig)
class SystemConfigAdmin(admin.ModelAdmin):
    """IT configuration tool — writable for ordinary operational toggles only.

    SystemConfig stores runtime toggles (e.g. auto-approval flags) that IT
    may need to adjust without redeploying. Changes here are visible in the
    Django admin log.

    Keys in SystemConfig.CONTROLLED_KEYS (biometric-acceptance thresholds and
    the auto_approve_* approval-workflow toggles) are managed through a
    dedicated, audited FANS-C application workflow and must not be freely
    editable here:
      - Existing controlled rows cannot be changed or deleted through this
        admin (has_change_permission / has_delete_permission below).
      - The `key` field is read-only once a row exists, so an uncontrolled
        row cannot be renamed into a controlled one.
      - Creating a new row with a controlled key is blocked in save_model.
    This only closes the Django Admin bypass around those existing audited
    workflows (verification.views.verify_config and
    beneficiaries.views.auto_approval_settings) -- it does not change how
    either workflow operates.
    """
    list_display = ['key', 'value', 'updated_at']

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            # Prevent renaming any existing key into a sensitive one (or out of it).
            readonly.append('key')
        return readonly

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.key in SystemConfig.CONTROLLED_KEYS:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.key in SystemConfig.CONTROLLED_KEYS:
            return False
        return super().has_delete_permission(request, obj)

    def get_actions(self, request):
        # Django's bulk "Delete selected" action calls queryset.delete()
        # directly, bypassing has_delete_permission() per object. Remove it
        # entirely so a sensitive row can never be bulk-deleted that way;
        # single-row delete (which does check has_delete_permission) still
        # works for non-sensitive rows.
        actions = super().get_actions(request)
        actions.pop('delete_selected', None)
        return actions

    def save_model(self, request, obj, form, change):
        if not change and obj.key in SystemConfig.CONTROLLED_KEYS:
            raise PermissionDenied(
                f"'{obj.key}' is a controlled configuration key and cannot "
                "be created through Django Admin."
            )
        super().save_model(request, obj, form, change)
