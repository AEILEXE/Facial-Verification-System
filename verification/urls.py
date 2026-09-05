from django.urls import path
from . import views

app_name = 'verification'

urlpatterns = [
    # ── Core verification flow ────────────────────────────────────────────────
    path('', views.verify_select, name='verify_select'),
    # verify-face/ is disabled — FaceVerificationMiddleware was removed.
    # Re-enable when staff face enrollment (UserFaceEmbedding) is populated
    # and the view is updated to use webcam capture instead of file upload.
    # path('verify-face/', views.face_verify, name='face_verify'),
    path('start/<uuid:pk>/', views.verify_start, name='verify_start'),
    path('check-liveness/', views.verify_check_liveness, name='verify_check_liveness'),
    path('submit/', views.verify_submit, name='verify_submit'),
    path('result/<uuid:attempt_id>/', views.verify_result, name='verify_result'),
    path('fallback/<uuid:attempt_id>/', views.verify_fallback, name='verify_fallback'),
    path('override/<uuid:attempt_id>/', views.admin_override, name='admin_override'),
    path('override-release/<uuid:attempt_id>/', views.override_release_payout, name='override_release_payout'),

    # ── Admin approval queue ──────────────────────────────────────────────────
    path('manual-review/', views.manual_review_list, name='manual_review'),
    path('manual-review/verify/<uuid:request_id>/', views.manual_verify_review, name='manual_verify_review'),
    path('manual-review/face-update/<uuid:request_id>/', views.face_update_review, name='face_update_review'),
    path('manual-review/special-claim/<uuid:request_id>/', views.special_claim_review, name='special_claim_review'),
    path('manual-review/pending-claim/<uuid:claim_id>/', views.pending_claim_review, name='pending_claim_review'),

    # ── Special / pending claims ──────────────────────────────────────────────
    path('special-claim-request/<uuid:pk>/', views.special_claim_request, name='special_claim_request'),

    # ── System config ─────────────────────────────────────────────────────────
    path('config/', views.verify_config, name='config'),

    # ── Stipend events ────────────────────────────────────────────────────────
    path('stipend/', views.stipend_list, name='stipend_list'),
    path('stipend/create/', views.stipend_create, name='stipend_create'),
    path('stipend/<uuid:event_id>/edit/', views.stipend_edit, name='stipend_edit'),
    path('stipend/<uuid:event_id>/delete/', views.stipend_delete, name='stipend_delete'),
    # Schedule approval workflow (Issue 5) — President only
    path('stipend/<uuid:event_id>/approve/', views.stipend_approve, name='stipend_approve'),
    path('stipend/<uuid:event_id>/reject/', views.stipend_reject, name='stipend_reject'),

    # ── Face update / re-enrollment ───────────────────────────────────────────
    path('update-face/<uuid:pk>/', views.update_face_data, name='update_face_data'),
    path('update-face/<uuid:pk>/submit/', views.update_face_submit, name='update_face_submit'),

    # ── Registration approval ─────────────────────────────────────────────────
    path('registration-review/', views.registration_review_list, name='registration_review_list'),
    path('registration-review/<uuid:pk>/', views.registration_review, name='registration_review'),

    # ── Representative face registration ─────────────────────────────────────
    path('register-rep-face/<uuid:pk>/<uuid:rep_pk>/', views.register_rep_face, name='register_rep_face'),
    path('register-rep-face/<uuid:pk>/<uuid:rep_pk>/submit/', views.register_rep_face_submit, name='register_rep_face_submit'),

    # ── Payouts ───────────────────────────────────────────────────────────────
    path('payouts/<uuid:claim_id>/', views.payout_detail, name='payout_detail'),
    path('payouts/<uuid:claim_id>/action/', views.payout_action, name='payout_action'),

    # ── Shared-Representative Review (added 2026-05-28) ─────────────────────
    path('shared-rep-review/', views.shared_rep_review_list, name='shared_rep_review_list'),
    path('shared-rep-review/<uuid:review_id>/', views.shared_rep_review_detail, name='shared_rep_review_detail'),

    # ── Reports / Export ──────────────────────────────────────────────────────
    path('reports/claims/', views.report_claims, name='report_claims'),
    path('reports/event-summary/', views.report_event_summary, name='report_event_summary'),
    path('reports/staff-performance/', views.report_staff_performance, name='report_staff_performance'),
    path('reports/override-fallback/', views.report_override_fallback, name='report_override_fallback'),
    path('reports/suspicious-attempts/', views.report_suspicious_attempts, name='report_suspicious_attempts'),
    path('reports/beneficiary/<uuid:beneficiary_id>/', views.report_beneficiary_history, name='report_beneficiary_history'),

    # ── Analytics Dashboard ───────────────────────────────────────────────────
    path('analytics/executive/', views.analytics_executive, name='analytics_executive'),
    path('analytics/operational/', views.analytics_operational, name='analytics_operational'),
    path('analytics/biometric-performance/', views.analytics_biometric_performance, name='analytics_biometric_performance'),
    path('analytics/security/', views.analytics_security, name='analytics_security'),

    # ── Fraud Detection Phase 1 ───────────────────────────────────────────────
    path('reports/fraud-signals/', views.fraud_signals_report, name='fraud_signals_report'),

    # ── Per-Template Match Analytics ──────────────────────────────────────────
    path('reports/template-match/', views.template_match_report, name='template_match_report'),

    # ── Controlled Biometric Evaluation (BPA-2) — admin-tier only ────────────
    path('evaluation/datasets/', views.evaluation_dataset_list, name='evaluation_dataset_list'),
    path('evaluation/datasets/create/', views.evaluation_dataset_create, name='evaluation_dataset_create'),
    path('evaluation/datasets/<uuid:pk>/', views.evaluation_dataset_detail, name='evaluation_dataset_detail'),
    path('evaluation/datasets/<uuid:pk>/start/', views.evaluation_dataset_start, name='evaluation_dataset_start'),
    path('evaluation/datasets/<uuid:pk>/finalize/', views.evaluation_dataset_finalize, name='evaluation_dataset_finalize'),
    path('evaluation/datasets/<uuid:pk>/archive/', views.evaluation_dataset_archive, name='evaluation_dataset_archive'),
    path('evaluation/datasets/<uuid:dataset_pk>/trials/new/', views.evaluation_trial_setup, name='evaluation_trial_setup'),
    path('evaluation/trials/<uuid:pk>/', views.evaluation_trial_detail, name='evaluation_trial_detail'),
    path('evaluation/trials/<uuid:pk>/liveness/', views.evaluation_trial_liveness, name='evaluation_trial_liveness'),
    path('evaluation/trials/<uuid:pk>/run/', views.evaluation_trial_run, name='evaluation_trial_run'),
    path('evaluation/trials/<uuid:pk>/abort/', views.evaluation_trial_abort, name='evaluation_trial_abort'),
    path('evaluation/trials/<uuid:pk>/withdraw/', views.evaluation_trial_withdraw, name='evaluation_trial_withdraw'),
]
