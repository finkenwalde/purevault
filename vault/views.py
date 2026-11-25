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
from django.db import IntegrityError, transaction
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from vault.crypto import encrypt_secret
from vault.mail_service import (
    MAX_PARTNERS, clean_header, clean_partner_name, encrypted_transport_snapshot,
    normalize_email, partner_event_email, queue_delivery, queue_summary, test_email,
    transport_from_settings, transport_is_usable,
)
from vault.models import AccountabilityPartner, EmailSettings, MailDelivery, RuntimeClock, UserProfile, VaultEntry
from vault.services import MAX_TRUSTED_CLOCK_GAP_MS
from vault.services import ClockUnavailable, EntryNotAvailable, MailRateLimited, SetupAlreadyComplete, advance_runtime_clock, cancel_or_relock, create_initial_user, grant_status, release_secret, setup_is_complete, start_unlock, update_entry_if_available
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


def _reauthenticate(request, data):
    password = data.get("current_password", "")
    if not isinstance(password, str) or not request.user.check_password(password):
        raise ValidationError("Current master password is incorrect.")


def _recent_event_count(owner, event_types, minutes):
    return MailDelivery.objects.filter(
        owner=owner,
        event_type__in=event_types,
        created_at__gte=timezone.now() - timedelta(minutes=minutes),
    ).values("event_id").distinct().count()


def _partner_json(partner):
    return {"id": partner.id, "name": partner.name, "email": partner.email}


def _email_settings_json(configuration):
    if not configuration:
        return {
            "enabled": False, "host": "", "port": 587, "security": "starttls",
            "username": "", "password_set": False, "from_email": "", "from_name": "",
        }
    return {
        "enabled": configuration.enabled,
        "host": configuration.host,
        "port": configuration.port,
        "security": configuration.security,
        "username": configuration.username,
        "password_set": bool(configuration.encrypted_password),
        "from_email": configuration.from_email,
        "from_name": configuration.from_name,
    }


def _configuration_warning(partner_count, configuration, summary):
    if not partner_count:
        return ""
    if not transport_is_usable(transport_from_settings(configuration)):
        return "Accountability partners are configured, but email sending is disabled or incomplete. Unlock reasons are still required and notices remain queued."
    if summary["action_needed"]:
        return "Some accountability emails need attention. Correct the email settings and queued messages will retry automatically."
    return ""


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
@require_GET
def accountability_settings(request):
    partners = list(AccountabilityPartner.objects.filter(owner=request.user))
    configuration = EmailSettings.objects.filter(owner=request.user).first()
    summary = queue_summary(request.user)
    return JsonResponse({
        "partners": [_partner_json(partner) for partner in partners],
        "email": _email_settings_json(configuration),
        "queue": summary,
        "warning": _configuration_warning(len(partners), configuration, summary),
        "limits": {"partners": MAX_PARTNERS, "reason_characters": 1000},
    })


@api_login_required
@require_POST
def partner_create(request):
    try:
        data = _json_body(request)
        if set(data) - {"name", "email", "current_password"}:
            raise ValidationError("Unsupported partner field.")
        _reauthenticate(request, data)
        name = clean_partner_name(data.get("name", ""))
        email = normalize_email(data.get("email", ""), "Partner email")
        with transaction.atomic():
            User.objects.select_for_update().get(pk=request.user.pk)
            if AccountabilityPartner.objects.filter(owner=request.user).count() >= MAX_PARTNERS:
                raise ValidationError(f"At most {MAX_PARTNERS} accountability partners can be configured.")
            if _recent_event_count(request.user, [MailDelivery.EventType.PARTNER_ADDED, MailDelivery.EventType.PARTNER_REMOVED, MailDelivery.EventType.PARTNER_ADDRESS_CHANGED], 60) >= 20:
                return _error("Too many partner changes were requested. Try again later.", 429)
            try:
                partner = AccountabilityPartner.objects.create(owner=request.user, name=name, email=email)
            except IntegrityError as exc:
                raise ValidationError("That email address is already an accountability partner.") from exc
            subject, body = partner_event_email(request.user, MailDelivery.EventType.PARTNER_ADDED, partner.name)
            queue_delivery(owner=request.user, event_id=os.urandom(16).hex(), event_type=MailDelivery.EventType.PARTNER_ADDED, recipient_name=partner.name, recipient_email=partner.email, subject=subject, body=body)
        return JsonResponse({"partner": _partner_json(partner)}, status=201)
    except ValidationError as exc:
        return _error(exc, 403 if "master password" in str(exc).lower() else 400)


def _owned_partner(request, partner_id):
    try:
        return AccountabilityPartner.objects.get(pk=partner_id, owner=request.user)
    except AccountabilityPartner.DoesNotExist as exc:
        raise Http404 from exc


@api_login_required
@require_http_methods(["PUT", "DELETE"])
def partner_detail(request, partner_id):
    try:
        data = _json_body(request)
        allowed = {"current_password"} if request.method == "DELETE" else {"name", "email", "current_password"}
        if set(data) - allowed:
            raise ValidationError("Unsupported partner field.")
        _reauthenticate(request, data)
        with transaction.atomic():
            partner = AccountabilityPartner.objects.select_for_update().get(pk=_owned_partner(request, partner_id).pk)
            if _recent_event_count(request.user, [MailDelivery.EventType.PARTNER_ADDED, MailDelivery.EventType.PARTNER_REMOVED, MailDelivery.EventType.PARTNER_ADDRESS_CHANGED], 60) >= 20:
                return _error("Too many partner changes were requested. Try again later.", 429)
            if request.method == "DELETE":
                subject, body = partner_event_email(request.user, MailDelivery.EventType.PARTNER_REMOVED, partner.name)
                queue_delivery(owner=request.user, event_id=os.urandom(16).hex(), event_type=MailDelivery.EventType.PARTNER_REMOVED, recipient_name=partner.name, recipient_email=partner.email, subject=subject, body=body)
                partner.delete()
                return JsonResponse({"ok": True})
            name = clean_partner_name(data.get("name", ""))
            email = normalize_email(data.get("email", ""), "Partner email")
            old_name, old_email = partner.name, partner.email
            if email != old_email and AccountabilityPartner.objects.filter(owner=request.user, email=email).exclude(pk=partner.pk).exists():
                raise ValidationError("That email address is already an accountability partner.")
            partner.name, partner.email = name, email
            partner.save(update_fields=["name", "email", "updated_at"])
            if email != old_email:
                subject, body = partner_event_email(request.user, MailDelivery.EventType.PARTNER_ADDRESS_CHANGED, old_name, f"Future notices will use {email}.")
                queue_delivery(owner=request.user, event_id=os.urandom(16).hex(), event_type=MailDelivery.EventType.PARTNER_ADDRESS_CHANGED, recipient_name=old_name, recipient_email=old_email, subject=subject, body=body)
                subject, body = partner_event_email(request.user, MailDelivery.EventType.PARTNER_ADDED, name, "This address will receive future notices.")
                queue_delivery(owner=request.user, event_id=os.urandom(16).hex(), event_type=MailDelivery.EventType.PARTNER_ADDED, recipient_name=name, recipient_email=email, subject=subject, body=body)
        return JsonResponse({"partner": _partner_json(partner)})
    except ValidationError as exc:
        return _error(exc, 403 if "master password" in str(exc).lower() else 400)


def _validated_email_configuration(data, existing):
    enabled = data.get("enabled")
    if not isinstance(enabled, bool):
        raise ValidationError("Enabled must be true or false.")
    host = clean_header(data.get("host", ""), "SMTP server", 255)
    try:
        port = int(data.get("port"))
    except (TypeError, ValueError) as exc:
        raise ValidationError("SMTP port must be a whole number from 1 to 65535.") from exc
    if not 1 <= port <= 65535:
        raise ValidationError("SMTP port must be from 1 to 65535.")
    security = data.get("security")
    if security not in EmailSettings.Security.values:
        raise ValidationError("Choose STARTTLS, SSL/TLS, or Unencrypted.")
    username = clean_header(data.get("username", ""), "SMTP username", 255)
    from_name = clean_header(data.get("from_name", ""), "From name", 100)
    from_email_value = data.get("from_email", "")
    from_email = normalize_email(from_email_value, "From email") if from_email_value else ""
    action = data.get("password_action", "keep")
    if action not in {"keep", "replace", "clear"}:
        raise ValidationError("Choose whether to keep, replace, or clear the SMTP password.")
    encrypted_password = existing.encrypted_password if existing and action == "keep" else None
    if action == "replace":
        password = data.get("password", "")
        if not isinstance(password, str) or not password or len(password) > 1024:
            raise ValidationError("SMTP password is required and must be at most 1024 characters.")
        from vault.crypto import encrypt_text
        encrypted_password = encrypt_text(password)
    password_present = bool(encrypted_password)
    if bool(username) != password_present:
        raise ValidationError("Enter both an SMTP username and password, or clear both for an unauthenticated relay.")
    if enabled and (not host or not from_email):
        raise ValidationError("SMTP server and From email are required when email notifications are enabled.")
    return {"enabled": enabled, "host": host, "port": port, "security": security, "username": username, "encrypted_password": encrypted_password, "from_email": from_email, "from_name": from_name}


@api_login_required
@require_http_methods(["PUT"])
def email_settings_update(request):
    try:
        data = _json_body(request)
        if set(data) - {"enabled", "host", "port", "security", "username", "password_action", "password", "from_email", "from_name", "current_password"}:
            raise ValidationError("Unsupported email setting.")
        _reauthenticate(request, data)
        with transaction.atomic():
            existing = EmailSettings.objects.select_for_update().filter(owner=request.user).first()
            previous_snapshot = encrypted_transport_snapshot(existing)
            values = _validated_email_configuration(data, existing)
            before = _email_settings_json(existing)
            if existing:
                for field, value in values.items():
                    setattr(existing, field, value)
                existing.revision += 1
                existing.save()
                configuration = existing
            else:
                configuration = EmailSettings.objects.create(owner=request.user, **values)
            if transport_is_usable(transport_from_settings(configuration)):
                MailDelivery.objects.filter(owner=request.user, status=MailDelivery.Status.PENDING).update(
                    next_attempt_at=timezone.now(), needs_attention=False, last_error="",
                )
            after = _email_settings_json(configuration)
            changed = before != after or data.get("password_action") in {"replace", "clear"}
            if changed:
                event_type = MailDelivery.EventType.NOTIFICATIONS_DISABLED if before["enabled"] and not after["enabled"] else MailDelivery.EventType.SMTP_CONFIGURATION_CHANGED
                event_id = os.urandom(16).hex()
                for partner in AccountabilityPartner.objects.filter(owner=request.user):
                    subject, body = partner_event_email(request.user, event_type, partner.name)
                    queue_delivery(owner=request.user, event_id=event_id, event_type=event_type, recipient_name=partner.name, recipient_email=partner.email, subject=subject, body=body, transport_snapshot=previous_snapshot)
        return JsonResponse({"email": _email_settings_json(configuration), "queue": queue_summary(request.user)})
    except ValidationError as exc:
        return _error(exc, 403 if "master password" in str(exc).lower() else 400)


@api_login_required
@require_POST
def email_test(request):
    try:
        data = _json_body(request)
        if set(data) - {"destination", "current_password"}:
            raise ValidationError("Unsupported test-email field.")
        _reauthenticate(request, data)
        destination = normalize_email(data.get("destination", ""), "Test destination")
        configuration = EmailSettings.objects.filter(owner=request.user).first()
        if not transport_is_usable(transport_from_settings(configuration)):
            raise ValidationError("Enable and complete email settings before sending a test.")
        if _recent_event_count(request.user, [MailDelivery.EventType.TEST], 15) >= 3:
            return _error("Too many test emails were requested. Try again later.", 429)
        subject, body = test_email(request.user)
        queue_delivery(owner=request.user, event_id=os.urandom(16).hex(), event_type=MailDelivery.EventType.TEST, recipient_name="", recipient_email=destination, subject=subject, body=body)
        return JsonResponse({"message": "Test email queued. Check delivery status; Accepted by mail server does not guarantee inbox delivery."}, status=202)
    except ValidationError as exc:
        return _error(exc, 403 if "master password" in str(exc).lower() else 400)


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
    try:
        data = _json_body(request)
        if set(data) - {"reason"}:
            raise ValidationError("Unsupported unlock field.")
        state, grant, created = start_unlock(entry, data.get("reason"))
        entry.refresh_from_db()
        return JsonResponse({"entry": _entry_metadata(entry), "created": created})
    except MailRateLimited as exc:
        return _error(exc, 429)
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

