from django.urls import path
from . import views

app_name = 'beneficiaries'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('beneficiaries/', views.beneficiary_list, name='beneficiary_list'),
    path('beneficiaries/reports/master-list/', views.beneficiary_master_list_report, name='beneficiary_master_list_report'),
    path('beneficiaries/<uuid:pk>/', views.beneficiary_detail, name='beneficiary_detail'),
    path('beneficiaries/<uuid:pk>/edit/', views.beneficiary_edit, name='beneficiary_edit'),
    path('beneficiaries/<uuid:pk>/correct-dob/', views.beneficiary_correct_dob, name='beneficiary_correct_dob'),
    path('beneficiaries/<uuid:pk>/deactivate/', views.beneficiary_deactivate, name='beneficiary_deactivate'),
    path('beneficiaries/<uuid:pk>/reactivate/', views.beneficiary_reactivate, name='beneficiary_reactivate'),
    path('register/step1/', views.register_step1, name='register_step1'),
    path('register/step2/', views.register_step2, name='register_step2'),
    path('register/step3/', views.register_step3, name='register_step3'),
    path('register/face/', views.register_face, name='register_face'),
    path('register/submit-face/', views.register_submit_face, name='register_submit_face'),
    path('beneficiaries/<uuid:pk>/representative/add/', views.add_representative, name='add_representative'),
    path('beneficiaries/<uuid:pk>/representative/<uuid:rep_pk>/deactivate/', views.deactivate_representative, name='deactivate_representative'),
    path('api/municipalities/', views.address_municipalities, name='address_municipalities'),
    path('api/barangays/', views.address_barangays, name='address_barangays'),
    path('users/', views.user_list, name='user_list'),
    path('users/create/', views.user_create, name='user_create'),
    path('users/<int:pk>/edit/', views.user_edit, name='user_edit'),
    # Offline sync conflict review (admin only)
    path('sync/conflicts/', views.sync_conflict_list, name='sync_conflict_list'),
    path('sync/conflicts/<uuid:pk>/review/', views.sync_conflict_review, name='sync_conflict_review'),
    # Auto-approval settings and pending approval queue (admin only)
    path('settings/auto-approval/', views.auto_approval_settings, name='auto_approval_settings'),
    path('pending-approvals/', views.pending_approvals, name='pending_approvals'),
    path('pending-approvals/approve/', views.approve_record, name='approve_record'),
    path('pending-approvals/reject/', views.reject_record, name='reject_record'),
    path('pending-approvals/bulk-approve/', views.bulk_approve, name='bulk_approve'),
    # Duplicate face review queue (admin only)
    path('duplicate-review/', views.duplicate_review_list, name='duplicate_review_list'),
    path('duplicate-review/<uuid:pk>/', views.duplicate_review_detail, name='duplicate_review_detail'),
    # Duplicate name+DOB override review queue (admin only) — Issue 1
    path('namedob-override/', views.namedob_review_list, name='namedob_review_list'),
    path('namedob-override/<uuid:pk>/', views.namedob_review_detail, name='namedob_review_detail'),
]
