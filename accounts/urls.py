from django.urls import path
from . import views
from .views import UserLoginView, logout_view

app_name = 'accounts'

urlpatterns = [
    path('login/', UserLoginView.as_view(), name='login'),
    path('logout/', logout_view, name='logout'),
    # Initial setup — only accessible when no admin-tier user exists yet
    path('setup/', views.create_admin, name='create_admin'),
    # Initial President bootstrap — Technical Administrator only, only while no President exists
    path('setup/president/', views.bootstrap_president, name='bootstrap_president'),
    # Self-service profile editing (own name/email/phone only — role/status read-only)
    path('profile/', views.my_profile, name='my_profile'),
    # Password management
    path('password/change/', views.change_password, name='change_password'),
    path('password/reset/<int:user_id>/', views.admin_reset_password, name='admin_reset_password'),
    path('password/reset-request/', views.password_reset_request_create, name='password_reset_request_create'),
    path('password/reset-requests/', views.password_reset_request_list, name='password_reset_request_list'),
    path('password/reset-requests/<uuid:request_id>/reject/', views.password_reset_request_reject, name='password_reset_request_reject'),
    # Self-service email-OTP password reset (Phase 6)
    path('password/forgot/', views.otp_forgot_password, name='otp_forgot_password'),
    path('password/forgot/verify/', views.otp_verify, name='otp_verify'),
    path('password/forgot/reset/', views.otp_reset_password, name='otp_reset_password'),
    # User management
    path('users/', views.user_list_full, name='user_list'),
    path('users/create/', views.user_create_full, name='user_create'),
    path('users/<int:pk>/edit/', views.user_edit_full, name='user_edit'),
    path('users/<int:pk>/status/', views.user_set_status, name='user_set_status'),
    path('users/<int:pk>/reset-password/', views.user_reset_password, name='user_reset_password_full'),
    # Officer positions
    path('officers/positions/', views.officer_position_list, name='officer_position_list'),
    path('officers/positions/create/', views.officer_position_create, name='officer_position_create'),
    path('officers/positions/<int:pk>/edit/', views.officer_position_edit, name='officer_position_edit'),
    # Officer assignments
    path('officers/assignments/', views.officer_assignment_list, name='officer_assignment_list'),
    path('officers/assignments/create/', views.officer_assignment_create, name='officer_assignment_create'),
    path('officers/assignments/<int:pk>/close/', views.officer_assignment_close, name='officer_assignment_close'),
    # Org chart
    path('officers/org-chart/', views.org_chart, name='org_chart'),
]
