from django.urls import path
from vault import views


urlpatterns = [
    path("session/", views.session_info),
    path("setup/", views.setup),
    path("login/", views.sign_in),
    path("logout/", views.sign_out),
    path("session/activity/", views.acknowledge_activity),
    path("account/password/", views.change_master_password),
    path("account/timezone/", views.display_timezone),
    path("accountability/", views.accountability_settings),
    path("accountability/partners/", views.partner_create),
    path("accountability/partners/<int:partner_id>/", views.partner_detail),
    path("accountability/email/", views.email_settings_update),
    path("accountability/email/test/", views.email_test),
    path("entries/", views.entries),
    path("entries/<int:entry_id>/", views.entry_detail),
    path("entries/<int:entry_id>/unlock/", views.unlock_start),
    path("entries/<int:entry_id>/cancel/", views.unlock_cancel),
    path("entries/<int:entry_id>/relock/", views.relock),
    path("entries/<int:entry_id>/reveal/", views.reveal),
]

