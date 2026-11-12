from django.urls import include, path
from vault.views import app_shell, health


urlpatterns = [
    path("health/", health),
    path("api/", include("vault.urls")),
    path("", app_shell),
    path("<path:path>", app_shell),
]

