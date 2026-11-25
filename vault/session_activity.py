import os

from django.conf import settings

from vault.services import advance_runtime_clock


SESSION_ACTIVITY_RUNTIME_KEY = "purevault_last_activity_runtime_ms"
SESSION_ACTIVITY_GENERATION_KEY = "purevault_activity_generation"


def stamp_session_activity(request):
    generation = os.environ.get("PUREVAULT_CLOCK_GENERATION", "")
    if not generation:
        raise RuntimeError("The application clock generation is unavailable.")
    snapshot = advance_runtime_clock(expected_generation=generation)
    request.session[SESSION_ACTIVITY_RUNTIME_KEY] = snapshot.elapsed_ms
    request.session[SESSION_ACTIVITY_GENERATION_KEY] = generation
    request.session.modified = True


def session_activity_is_valid(request):
    generation = os.environ.get("PUREVAULT_CLOCK_GENERATION", "")
    stored_generation = request.session.get(SESSION_ACTIVITY_GENERATION_KEY)
    last_activity = request.session.get(SESSION_ACTIVITY_RUNTIME_KEY)
    if not generation or stored_generation != generation:
        return False
    if isinstance(last_activity, bool) or not isinstance(last_activity, int) or last_activity < 0:
        return False
    snapshot = advance_runtime_clock(expected_generation=generation)
    if snapshot.elapsed_ms < last_activity:
        return False
    inactivity_ms = snapshot.elapsed_ms - last_activity
    return inactivity_ms < settings.PUREVAULT_SESSION_INACTIVITY_SECONDS * 1000
