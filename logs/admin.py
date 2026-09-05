from django.contrib import admin
from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """View-only. AuditLog is a tamper-evident, append-only audit trail.

    All write operations are blocked: no entries may be added, changed,
    or deleted through Django admin. The bulk "Delete selected" action is
    also removed. The AuditLog.log() API is the only authorised write path.
    """
    list_display = ['timestamp', 'user', 'action', 'target_type', 'ip_address']
    list_filter = ['action']
    search_fields = ['user__username', 'target_id']
    readonly_fields = ['id', 'timestamp', 'ip_address', 'user_agent',
                       'user', 'action', 'target_type', 'target_id', 'details']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop('delete_selected', None)
        return actions
