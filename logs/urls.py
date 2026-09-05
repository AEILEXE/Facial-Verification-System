from django.urls import path
from . import views

app_name = 'logs'

urlpatterns = [
    path('audit/', views.audit_log_list, name='audit_logs'),
    path('verification/', views.verification_log_list, name='verification_logs'),
    path('notifications/', views.notification_center, name='notification_center'),
    path('notifications/<uuid:pk>/open/', views.notification_open, name='notification_open'),
    path('notifications/mark-all-read/', views.notification_mark_all_read, name='notification_mark_all_read'),
]
