from django.contrib.auth import logout
from django.http import JsonResponse

from vault.services import ClockUnavailable
from vault.session_activity import session_activity_is_valid


class SessionInactivityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/api/") and request.user.is_authenticated:
            try:
                active = session_activity_is_valid(request)
            except (ClockUnavailable, RuntimeError):
                active = False
            if not active:
                logout(request)
                request.purevault_inactivity_expired = True
                if request.path != "/api/session/":
                    return JsonResponse(
                        {"error": "Signed out after inactivity.", "code": "inactivity_timeout"},
                        status=401,
                    )
        return self.get_response(request)


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        response["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
        if request.path.startswith("/api/"):
            response["Cache-Control"] = "no-store, max-age=0"
            response["Pragma"] = "no-cache"
        return response

