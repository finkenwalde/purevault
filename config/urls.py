from django.urls import path
from vault import views

urlpatterns = [
    path("", views.home, name="home"),
    path("setup/", views.setup, name="setup"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("entries/new/", views.create_entry, name="create_entry"),
    path("entries/<int:entry_id>/", views.entry_detail, name="entry_detail"),
    path("entries/<int:entry_id>/edit/", views.edit_entry, name="edit_entry"),
    path("entries/<int:entry_id>/delete/", views.delete_entry, name="delete_entry"),
    path("entries/<int:entry_id>/request-unlock/", views.request_unlock, name="request_unlock"),
    path("entries/<int:entry_id>/cancel-unlock/", views.cancel_unlock, name="cancel_unlock"),
    path("entries/<int:entry_id>/reveal/", views.reveal_entry, name="reveal_entry"),
]
