from django.urls import path
from vault import views


urlpatterns = [
    path("session/", views.session_info),
    path("setup/", views.setup),
    path("login/", views.sign_in),
    path("logout/", views.sign_out),
    path("account/password/", views.change_master_password),
    path("account/timezone/", views.display_timezone),
    path("entries/", views.entries),
    path("entries/<int:entry_id>/", views.entry_detail),
    path("entries/<int:entry_id>/unlock/", views.unlock_start),
    path("entries/<int:entry_id>/cancel/", views.unlock_cancel),
    path("entries/<int:entry_id>/relock/", views.relock),
    path("entries/<int:entry_id>/reveal/", views.reveal),
]

