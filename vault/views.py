import json
import os
import time
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from vault.crypto import encrypt_secret
from vault.models import RuntimeClock, UserProfile, VaultEntry
from vault.services import MAX_TRUSTED_CLOCK_GAP_MS
from vault.services import EntryNotAvailable, SetupAlreadyComplete, advance_runtime_clock, cancel_or_relock, create_initial_user, grant_status, release_secret, setup_is_complete, start_unlock, update_entry_if_available
from vault.validation import validate_entry_payload


def api_login_required(view):
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return _error("Authentication required.", 401)
        return view(request, *args, **kwargs)
    return wrapped


def _json_body(request):
    try:
        value = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValidationError("Request body must be valid JSON.")
    if not isinstance(value, dict):
        raise ValidationError("Request body must be a JSON object.")
    return value


def _error(message, status=400):
    if isinstance(message, ValidationError):
        message = "; ".join(message.messages)
    return JsonResponse({"error": str(message)}, status=status)


def _iso(value):
    return value.isoformat().replace("+00:00", "Z") if value else None


def _entry_metadata(entry, runtime_ms=None, wall_now=None):
    wall_now = wall_now or timezone.now()
    runtime_ms = advance_runtime_clock().elapsed_ms if runtime_ms is None else runtime_ms
    state, grant, remaining_ms = grant_status(entry, runtime_ms)
    remaining_seconds = None if remaining_ms is None else max(0, (remaining_ms + 999) // 1000)
    eligible_estimate = None
    access_estimate = None
    if grant and state == "waiting":
        eligible_estimate = wall_now + timedelta(milliseconds=remaining_ms)
        if entry.relock_policy == VaultEntry.RelockPolicy.AUTOMATIC:
            access_estimate = eligible_estimate + timedelta(seconds=entry.relock_after_seconds)
    elif grant and state == "available" and entry.relock_policy == VaultEntry.RelockPolicy.AUTOMATIC:
        eligible_estimate = wall_now
        access_estimate = wall_now + timedelta(milliseconds=remaining_ms)
    return {
        "id": entry.id,
        "title": entry.title,
        "unlock_delay_seconds": entry.unlock_delay_seconds,
        "relock_policy": entry.relock_policy,
        "relock_after_seconds": entry.relock_after_seconds,
        "status": state,
        "remaining_seconds": remaining_seconds,
        "requested_at": _iso(grant.requested_at) if grant and state != "locked" else None,
        "eligible_at": _iso(eligible_estimate),
        "access_until": _iso(access_estimate),
        "created_at": _iso(entry.created_at),
        "updated_at": _iso(entry.updated_at),
    }


def _owned_entry(request, entry_id):
    try:
        return VaultEntry.objects.get(pk=entry_id, owner=request.user)
    except VaultEntry.DoesNotExist as exc:
        raise Http404 from exc


@require_GET
def health(request):
    expected = os.environ.get("PUREVAULT_CLOCK_GENERATION", "")
    try:
        clock = RuntimeClock.objects.get(pk=1)
        heartbeat_age_ms = (time.monotonic_ns() - clock.checkpoint_monotonic_ns) // 1_000_000
        healthy = bool(expected) and clock.generation == expected and 0 <= heartbeat_age_ms <= MAX_TRUSTED_CLOCK_GAP_MS
    except RuntimeClock.DoesNotExist:
        healthy = False
    return JsonResponse({"status": "ok" if healthy else "unavailable"}, status=200 if healthy else 503)


@ensure_csrf_cookie
def app_shell(request, path=""):
    if request.path.startswith("/api/"):
        raise Http404
    return render(request, "index.html")


@ensure_csrf_cookie
@require_GET
def session_info(request):
    configured_timezone = ""
    if request.user.is_authenticated:
        configured_timezone = UserProfile.objects.get_or_create(user=request.user)[0].display_timezone
    candidate = configured_timezone or settings.PUREVAULT_DISPLAY_TIMEZONE
    try:
        ZoneInfo(candidate)
        display_timezone = candidate
    except ZoneInfoNotFoundError:
        display_timezone = "UTC"
    return JsonResponse({
        "authenticated": request.user.is_authenticated,
        "username": request.user.username if request.user.is_authenticated else None,
        "setup_required": not setup_is_complete(),
        "server_time": _iso(timezone.now()),
        "display_timezone": display_timezone,
        "display_timezone_is_default": not bool(configured_timezone),
    })


@require_POST
def setup(request):
    if setup_is_complete():
        return _error("Setup has already been completed.", 409)
    try:
        data = _json_body(request)
        if set(data) - {"username", "password", "password_confirmation"}:
            raise ValidationError("Unsupported setup field.")
        username = data.get("username", "")
        password = data.get("password", "")
        password_confirmation = data.get("password_confirmation", "")
        if not isinstance(username, str) or not username.strip() or len(username) > 150:
            raise ValidationError("Username is required and must be at most 150 characters.")
        if password != password_confirmation:
            raise ValidationError("Master password and confirmation do not match.")
        validate_password(password, user=User(username=username.strip()))
        try:
            user = create_initial_user(username.strip(), password)
        except SetupAlreadyComplete:
            return _error("Setup has already been completed.", 409)
        login(request, user)
        return JsonResponse({"ok": True}, status=201)
    except ValidationError as exc:
        return _error(exc)


@require_POST
def sign_in(request):
    failures = request.session.get("login_failures", [])
    cutoff = time.time() - settings.PUREVAULT_LOGIN_WINDOW_SECONDS
    failures = [stamp for stamp in failures if stamp >= cutoff]
    if len(failures) >= settings.PUREVAULT_LOGIN_ATTEMPTS:
        return _error("Too many login attempts. Try again later.", 429)
    try:
        data = _json_body(request)
        if set(data) - {"username", "password"}:
            raise ValidationError("Unsupported login field.")
        user = authenticate(request, username=data.get("username", ""), password=data.get("password", ""))
        if user is None:
            failures.append(time.time())
            request.session["login_failures"] = failures
            time.sleep(0.15)
            return _error("Invalid username or password.", 401)
        login(request, user)
        request.session.pop("login_failures", None)
        return JsonResponse({"ok": True})
    except ValidationError as exc:
        return _error(exc)


@require_POST
def sign_out(request):
    logout(request)
    return JsonResponse({"ok": True})


@api_login_required
@require_POST
def change_master_password(request):
    try:
        data = _json_body(request)
        if set(data) - {"current_password", "new_password"}:
            raise ValidationError("Unsupported password-change field.")
        if not request.user.check_password(data.get("current_password", "")):
            return _error("Current password is incorrect.", 403)
        validate_password(data.get("new_password", ""), user=request.user)
        request.user.set_password(data["new_password"])
        request.user.save(update_fields=["password"])
        update_session_auth_hash(request, request.user)
        return JsonResponse({"ok": True})
    except ValidationError as exc:
        return _error(exc)


@api_login_required
@require_POST
def display_timezone(request):
    try:
        data = _json_body(request)
        if set(data) - {"display_timezone"}:
            raise ValidationError("Unsupported timezone field.")
        name = data.get("display_timezone")
        if not isinstance(name, str) or not name or len(name) > 128:
            raise ValidationError("Enter a valid IANA timezone name.")
        try:
            ZoneInfo(name)
        except ZoneInfoNotFoundError as exc:
            raise ValidationError("Enter a valid IANA timezone name, such as UTC or America/New_York.") from exc
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        profile.display_timezone = name
        profile.save(update_fields=["display_timezone"])
        return JsonResponse({"display_timezone": name})
    except ValidationError as exc:
        return _error(exc)


@api_login_required
@require_http_methods(["GET", "POST"])
def entries(request):
    if request.method == "GET":
        runtime_ms = advance_runtime_clock().elapsed_ms
        now = timezone.now()
        return JsonResponse({"entries": [_entry_metadata(item, runtime_ms, now) for item in VaultEntry.objects.filter(owner=request.user)], "server_time": _iso(now)})
    try:
        values = validate_entry_payload(_json_body(request))
        secret = values.pop("password")
        entry = VaultEntry.objects.create(owner=request.user, encrypted_secret=encrypt_secret(secret), **values)
        return JsonResponse({"entry": _entry_metadata(entry)}, status=201)
    except ValidationError as exc:
        return _error(exc)


@api_login_required
@require_http_methods(["PUT"])
def entry_detail(request, entry_id):
    entry = _owned_entry(request, entry_id)
    try:
        values = validate_entry_payload(_json_body(request), editing=True)
        secret = values.pop("password")
        try:
            entry = update_entry_if_available(entry, request.user, encrypted_secret=encrypt_secret(secret), values=values)
        except EntryNotAvailable:
            return _error("The entry must be available before its secret or security policy can be changed.", 423)
        return JsonResponse({"entry": _entry_metadata(entry)})
    except ValidationError as exc:
        return _error(exc)


@api_login_required
@require_POST
def unlock_start(request, entry_id):
    entry = _owned_entry(request, entry_id)
    state, grant, created = start_unlock(entry)
    entry.refresh_from_db()
    return JsonResponse({"entry": _entry_metadata(entry), "created": created})


@api_login_required
@require_POST
def unlock_cancel(request, entry_id):
    entry = _owned_entry(request, entry_id)
    cancel_or_relock(entry)
    return JsonResponse({"entry": _entry_metadata(entry)})


@api_login_required
@require_POST
def relock(request, entry_id):
    entry = _owned_entry(request, entry_id)
    cancel_or_relock(entry)
    return JsonResponse({"entry": _entry_metadata(entry)})


@api_login_required
@require_POST
def reveal(request, entry_id):
    entry = _owned_entry(request, entry_id)
    secret, state = release_secret(entry)
    if secret is None:
        return _error("The entry is not currently available.", 423)
    return JsonResponse({"password": secret, "released_at": _iso(timezone.now())})

