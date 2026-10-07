from django.conf import settings
from django.utils import timezone


def vault_session(request):
    now = timezone.now()
    context = {
        "purevault_now": timezone.localtime(now),
        "purevault_now_timestamp": int(now.timestamp() * 1000),
        "purevault_timezone": settings.TIME_ZONE,
    }
    if request.session.get("vault_authenticated"):
        context["vault_session_expires_at"] = int(request.session.get_expiry_date().timestamp() * 1000)
    return context
