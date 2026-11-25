import json
import smtplib
import socket
import ssl
import uuid
from datetime import timedelta
from email.message import EmailMessage
from email.headerregistry import Address
from email.utils import format_datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db.models import Q
from django.utils import timezone

from vault.crypto import decrypt_text, encrypt_text
from vault.models import EmailSettings, MailDelivery, UserProfile


MAX_PARTNERS = 20
MAX_REASON_LENGTH = 1000
MAIL_TIMEOUT_SECONDS = 15
MAIL_LEASE_SECONDS = 120
ACCEPTED_RETENTION_DAYS = 180


def clean_header(value, label, maximum):
    if not isinstance(value, str):
        raise ValidationError(f"{label} must be text.")
    value = value.strip()
    if "\r" in value or "\n" in value:
        raise ValidationError(f"{label} cannot contain line breaks.")
    if len(value) > maximum:
        raise ValidationError(f"{label} must be at most {maximum} characters.")
    return value


def normalize_email(value, label="Email address"):
    value = clean_header(value, label, 254).lower()
    if not value:
        raise ValidationError(f"{label} is required.")
    validate_email(value)
    return value


def clean_partner_name(value):
    value = clean_header(value, "Partner name", 100)
    if not value:
        raise ValidationError("Partner name is required.")
    return value


def clean_reason(value, required):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValidationError("Reason for unlock must be text.")
    value = value.strip()
    if len(value) > MAX_REASON_LENGTH:
        raise ValidationError(f"Reason for unlock must be at most {MAX_REASON_LENGTH} characters.")
    if required and not value:
        raise ValidationError("Reason for unlock is required while accountability partners are configured.")
    return value


def _encrypted_json(value):
    return encrypt_text(json.dumps(value, separators=(",", ":"), ensure_ascii=False))


def _decrypted_json(value):
    return json.loads(decrypt_text(value))


def transport_from_settings(configuration):
    if not configuration:
        return None
    password = decrypt_text(configuration.encrypted_password) if configuration.encrypted_password else ""
    return {
        "enabled": configuration.enabled,
        "host": configuration.host,
        "port": configuration.port,
        "security": configuration.security,
        "username": configuration.username,
        "password": password,
        "from_email": configuration.from_email,
        "from_name": configuration.from_name,
        "revision": configuration.revision,
    }


def transport_is_usable(value):
    return bool(value and value.get("enabled") and value.get("host") and value.get("from_email") and (not value.get("username") or value.get("password")))


def encrypted_transport_snapshot(configuration):
    value = transport_from_settings(configuration)
    return _encrypted_json(value) if transport_is_usable(value) else None


def queue_delivery(*, owner, event_id, event_type, recipient_name, recipient_email, subject, body, transport_snapshot=None, now=None):
    now = now or timezone.now()
    payload = {"subject": clean_header(subject, "Email subject", 180), "body": body}
    return MailDelivery.objects.create(
        owner=owner,
        event_id=event_id,
        event_type=event_type,
        recipient_name=clean_header(recipient_name or "", "Recipient name", 100),
        recipient_email=normalize_email(recipient_email, "Recipient email"),
        encrypted_payload=_encrypted_json(payload),
        encrypted_transport_snapshot=transport_snapshot,
        next_attempt_at=now,
    )


def display_timestamp(owner, value):
    profile = UserProfile.objects.filter(user=owner).first()
    zone_name = (profile.display_timezone if profile else "") or settings.PUREVAULT_DISPLAY_TIMEZONE
    try:
        zone = ZoneInfo(zone_name)
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("UTC")
    local = value.astimezone(zone)
    return local.strftime("%Y-%m-%d %H:%M:%S %Z (UTC%z)")


def readable_duration(seconds):
    units = ((86400, "day"), (3600, "hour"), (60, "minute"), (1, "second"))
    factor, label = next(((factor, label) for factor, label in units if seconds % factor == 0), units[-1])
    amount = seconds // factor
    return f"{amount} {label}{'' if amount == 1 else 's'}"


def unlock_email(owner, entry, reason, requested_at, eligible_estimate):
    subject = "Purevault: unlock requested"
    body = "\n".join([
        "Purevault accountability notice",
        "",
        f"Vault owner: {owner.username}",
        "Event: unlock requested (the password was not accessed)",
        f"Entry: {entry.title}",
        f"Reason: {reason}",
        f"Requested: {display_timestamp(owner, requested_at)}",
        f"Configured delay: {readable_duration(entry.unlock_delay_seconds)}",
        f"Estimated availability: {display_timestamp(owner, eligible_estimate)}",
        "",
        "Waiting time counts only while Purevault is running, so the calendar availability time is an estimate, not a guarantee.",
        "This message contains no vault password.",
    ])
    return subject, body


def partner_event_email(owner, event_type, partner_name, detail=""):
    labels = {
        MailDelivery.EventType.PARTNER_ADDED: ("Purevault: accountability partner added", "You were added as an accountability partner."),
        MailDelivery.EventType.PARTNER_REMOVED: ("Purevault: accountability partner removed", "You were removed as an accountability partner."),
        MailDelivery.EventType.PARTNER_ADDRESS_CHANGED: ("Purevault: accountability partner address changed", "Your previous email address was replaced for future accountability notices."),
        MailDelivery.EventType.NOTIFICATIONS_DISABLED: ("Purevault: email notifications disabled", "Email notifications were disabled for this vault."),
        MailDelivery.EventType.SMTP_CONFIGURATION_CHANGED: ("Purevault: email notification settings changed", "The vault's email notification configuration changed."),
    }
    subject, message = labels[event_type]
    body = "\n".join([
        "Purevault accountability notice",
        "",
        f"Vault owner: {owner.username}",
        f"Partner: {partner_name}",
        message,
        detail,
        "",
        "Accountability emails are notifications only; they do not provide access to the vault or approve an unlock.",
    ]).strip()
    return subject, body


def test_email(owner):
    return (
        "Purevault: test email",
        "\n".join([
            "Purevault email test",
            "",
            f"Vault owner: {owner.username}",
            f"Requested: {display_timestamp(owner, timezone.now())}",
            "",
            "Your mail server accepted this test when its delivery status changes to Accepted by mail server.",
            "Inbox delivery can still depend on the receiving provider.",
        ]),
    )


def queue_summary(owner):
    deliveries = MailDelivery.objects.filter(owner=owner)
    latest_problem = deliveries.filter(needs_attention=True).exclude(last_error="").order_by("-updated_at").first()
    return {
        "pending": deliveries.filter(status=MailDelivery.Status.PENDING).count(),
        "sending": deliveries.filter(status=MailDelivery.Status.SENDING).count(),
        "action_needed": deliveries.filter(needs_attention=True).exclude(status=MailDelivery.Status.ACCEPTED).count(),
        "accepted": deliveries.filter(status=MailDelivery.Status.ACCEPTED).count(),
        "latest_error": latest_problem.last_error if latest_problem else "",
    }


def claim_due_delivery(now=None):
    now = now or timezone.now()
    eligible = Q(status=MailDelivery.Status.PENDING) | Q(status=MailDelivery.Status.SENDING, lease_expires_at__lte=now)
    candidate_ids = list(MailDelivery.objects.filter(eligible, next_attempt_at__lte=now).order_by("next_attempt_at", "id").values_list("id", flat=True)[:20])
    for delivery_id in candidate_ids:
        token = uuid.uuid4()
        updated = MailDelivery.objects.filter(pk=delivery_id, next_attempt_at__lte=now).filter(eligible).update(
            status=MailDelivery.Status.SENDING,
            lease_token=token,
            lease_expires_at=now + timedelta(seconds=MAIL_LEASE_SECONDS),
        )
        if updated:
            return MailDelivery.objects.get(pk=delivery_id), token
    return None, None


def _current_transport(owner_id):
    configuration = EmailSettings.objects.filter(owner_id=owner_id).first()
    value = transport_from_settings(configuration)
    return value if transport_is_usable(value) else None


def _transport_for_delivery(delivery):
    if delivery.encrypted_transport_snapshot and delivery.attempt_count == 0:
        return _decrypted_json(delivery.encrypted_transport_snapshot), True
    return _current_transport(delivery.owner_id), False


def _send_smtp(delivery, transport):
    payload = _decrypted_json(delivery.encrypted_payload)
    message = EmailMessage()
    message["Subject"] = payload["subject"]
    message["From"] = Address(display_name=transport.get("from_name", ""), addr_spec=transport["from_email"])
    message["To"] = Address(display_name=delivery.recipient_name, addr_spec=delivery.recipient_email)
    message["Date"] = format_datetime(timezone.now())
    message["Message-ID"] = f"<{delivery.delivery_id}@purevault.invalid>"
    message.set_content(payload["body"])
    context = ssl.create_default_context()
    if transport["security"] == EmailSettings.Security.SSL:
        client = smtplib.SMTP_SSL(transport["host"], transport["port"], timeout=MAIL_TIMEOUT_SECONDS, context=context)
    else:
        client = smtplib.SMTP(transport["host"], transport["port"], timeout=MAIL_TIMEOUT_SECONDS)
    with client:
        client.ehlo()
        if transport["security"] == EmailSettings.Security.STARTTLS:
            client.starttls(context=context)
            client.ehlo()
        if transport.get("username"):
            client.login(transport["username"], transport["password"])
        client.send_message(message)


def _failure_details(exc):
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "SMTP authentication failed. Check email settings.", True, 3600
    if isinstance(exc, (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError)):
        return "The mail server rejected the message. Check addresses and email settings.", True, 3600
    if isinstance(exc, ssl.SSLError):
        return "The secure mail connection failed. Check server and security settings.", True, 3600
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "The mail server timed out. Purevault will retry.", False, None
    if isinstance(exc, (OSError, smtplib.SMTPException)):
        return "Purevault could not complete the mail connection. It will retry.", False, None
    return "Email delivery failed safely. Purevault will retry.", False, None


def process_claimed_delivery(delivery, token, sender=None):
    transport, used_snapshot = _transport_for_delivery(delivery)
    if not transport:
        exc = RuntimeError("not configured")
        message, attention, fixed_delay = "Email sending is disabled or incomplete. Configure it to resume delivery.", True, 3600
    else:
        try:
            (sender or _send_smtp)(delivery, transport)
        except Exception as caught:  # errors are sanitized before persistence or logs
            exc = caught
            message, attention, fixed_delay = _failure_details(caught)
        else:
            now = timezone.now()
            return bool(MailDelivery.objects.filter(pk=delivery.pk, lease_token=token, status=MailDelivery.Status.SENDING).update(
                status=MailDelivery.Status.ACCEPTED,
                accepted_at=now,
                attempt_count=delivery.attempt_count + 1,
                lease_token=None,
                lease_expires_at=None,
                needs_attention=False,
                last_error="",
                encrypted_transport_snapshot=None,
            ))
    attempts = delivery.attempt_count + 1
    delay = fixed_delay or min(3600, 30 * (2 ** min(attempts - 1, 7)))
    return bool(MailDelivery.objects.filter(pk=delivery.pk, lease_token=token, status=MailDelivery.Status.SENDING).update(
        status=MailDelivery.Status.PENDING,
        attempt_count=attempts,
        next_attempt_at=timezone.now() + timedelta(seconds=delay),
        lease_token=None,
        lease_expires_at=None,
        needs_attention=attention,
        last_error=message,
        encrypted_transport_snapshot=None if used_snapshot else delivery.encrypted_transport_snapshot,
    ))


def prune_accepted_history(now=None):
    cutoff = (now or timezone.now()) - timedelta(days=ACCEPTED_RETENTION_DAYS)
    return MailDelivery.objects.filter(status=MailDelivery.Status.ACCEPTED, accepted_at__lt=cutoff).delete()[0]
