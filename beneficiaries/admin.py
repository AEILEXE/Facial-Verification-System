from django.contrib import admin
from .models import Beneficiary


@admin.register(Beneficiary)
class BeneficiaryAdmin(admin.ModelAdmin):
    list_display = ['beneficiary_id', 'full_name', 'barangay', 'municipality', 'status', 'created_at']
    list_filter = ['status', 'municipality', 'province', 'gender']
    search_fields = ['first_name', 'last_name', 'beneficiary_id']
    readonly_fields = ['beneficiary_id', 'created_at', 'updated_at']
