import json
import os
import time
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ImproperlyConfigured, RequestDataTooBig, ValidationError
from django.db import OperationalError
from django.http import Http404, JsonResponse
from django.http.multipartparser import MultiPartParserError
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from vault.backup_format import MAX_FILE_BYTES, InvalidBackup, unpack_backup
from vault.crypto import encrypt_secret
from vault.models import RuntimeClock, UserProfile, VaultEntry
from vault.services import MAX_TRUSTED_CLOCK_GAP_MS
from vault.services import ClockUnavailable, EntryNotAvailable, SetupAlreadyComplete, advance_runtime_clock, cancel_or_relock, create_initial_user, grant_status, release_secret, restore_entries, setup_is_complete, start_unlock, update_entry_if_available
from vault.session_activity import session_activity_is_valid, stamp_session_activity
from vault.validation import validate_entry_payload


def api_login_required(view):
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({"error": "Authentication required.", "code": "authentication_required"}, status=401)
        try:
            active = session_activity_is_valid(request)
        except (ClockUnavailable, RuntimeError):
            active = False
        if not active:
            logout(request)
            return JsonResponse({"error": "Signed out after inactivity.", "code": "inactivity_timeout"}, status=401)
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
        if grant.access_until_runtime_ms is not None:
            access_estimate = eligible_estimate + timedelta(milliseconds=grant.access_until_runtime_ms - grant.eligible_runtime_ms)
    elif grant and state == "available" and grant.access_until_runtime_ms is not None:
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
    if request.path_info == "/api" or request.path_info.startswith("/api/"):
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
        "signed_out_reason": "inactivity" if getattr(request, "purevault_inactivity_expired", False) else None,
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
        stamp_session_activity(request)
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
        stamp_session_activity(request)
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
def acknowledge_activity(request):
    try:
        data = _json_body(request)
        if data:
            raise ValidationError("Activity acknowledgment does not accept fields.")
        stamp_session_activity(request)
        return JsonResponse({"ok": True})
    except ValidationError as exc:
        return _error(exc)


@api_login_required
@require_POST
def change_master_password(request):
    try:
        data = _json_body(request)
        if set(data) - {"current_password", "new_password", "new_password_confirmation"}:
            raise ValidationError("Unsupported password-change field.")
        current = data.get("current_password")
        new = data.get("new_password")
        fields = (current, new)
        if "new_password_confirmation" in data:
            fields += (data["new_password_confirmation"],)
        if any(not isinstance(value, str) or not 1 <= len(value) <= 4096 for value in fields):
            raise ValidationError("Enter passwords of 1 to 4096 characters.")
        try:
            for value in fields:
                value.encode("utf-8")
        except UnicodeError as exc:
            raise ValidationError("Enter valid text for each password.") from exc

        original_hash = request.user.password
        if not check_password(current, original_hash):
            return _error("Current password is incorrect.", 403)
        if "new_password_confirmation" in data and data["new_password_confirmation"] != new:
            raise ValidationError("The new passwords do not match.")
        validate_password(new, user=request.user)
        replacement_hash = make_password(new)
        # Only the request that still holds the verified hash can replace it.
        changed = User.objects.filter(pk=request.user.pk, password=original_hash).update(password=replacement_hash)
        if not changed:
            return _error("The master password was changed in another session. Sign in again.", 409)
        request.user.password = replacement_hash
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
        data = _json_body(request)
        relock_after_save = data.pop("relock", False)
        if not isinstance(relock_after_save, bool):
            raise ValidationError("Relock must be true or false.")
        values = validate_entry_payload(data, editing=True)
        secret = values.pop("password")
        try:
            entry = update_entry_if_available(
                entry,
                request.user,
                encrypted_secret=encrypt_secret(secret) if secret is not None else None,
                values=values,
                relock=relock_after_save,
            )
        except EntryNotAvailable:
            return _error("The entry must be available before its secret or security policy can be changed.", 423)
        return JsonResponse({"entry": _entry_metadata(entry)})
    except ValidationError as exc:
        return _error(exc)


@api_login_required
@require_POST
def unlock_start(request, entry_id):
    entry = _owned_entry(request, entry_id)
    try:
        data = _json_body(request)
        if data:
            raise ValidationError("Unsupported unlock field.")
        _, _, created = start_unlock(entry)
        entry.refresh_from_db()
        return JsonResponse({"entry": _entry_metadata(entry), "created": created})
    except ValidationError as exc:
        return _error(exc)


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


BACKUP_BUSY = "Another vault operation is in progress. Try again shortly."
RESTORE_EMPTY = "Restore requires an empty vault."


def _correct_master_password(user, value):
    return isinstance(value, str) and len(value) <= 4096 and user.check_password(value)


@api_login_required
@require_POST
def backup_restore(request):
    try:
        if set(request.POST) != {"passphrase", "current_password"} or set(request.FILES) != {"backup"}:
            return _error("Choose a backup file and enter both passwords.")
        if any(len(request.POST.getlist(field)) != 1 for field in request.POST) or len(request.FILES.getlist("backup")) != 1:
            return _error("Submit one file and one value per field.")
        if not _correct_master_password(request.user, request.POST["current_password"]):
            return _error("Current master password is incorrect.", 403)
        if VaultEntry.objects.filter(owner=request.user).exists():
            return _error(RESTORE_EMPTY, 409)
        upload = request.FILES["backup"]
        if upload.size > MAX_FILE_BYTES:
            return _error("Upload a Purevault backup file up to 12 MiB.", 413)
        zone, entries = unpack_backup(upload.read(MAX_FILE_BYTES + 1), request.POST["passphrase"])
        if not restore_entries(request.user.pk, zone, entries):
            return _error(RESTORE_EMPTY, 409)
        return JsonResponse({"restored": len(entries)})
    except (InvalidBackup, RequestDataTooBig, MultiPartParserError):
        return _error("The backup could not be opened. Check the recovery passphrase and file.")
    except OperationalError:
        return _error(BACKUP_BUSY, 409)
    except (OSError, ImproperlyConfigured):
        return _error("Restore could not write to vault storage.", 503)

