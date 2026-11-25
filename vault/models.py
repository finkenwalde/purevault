from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
import uuid


MIN_DURATION_SECONDS = 1
MAX_UNLOCK_DELAY_SECONDS = 365 * 24 * 60 * 60
MAX_RELOCK_SECONDS = 30 * 24 * 60 * 60


class InstallationState(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    configured = models.BooleanField(default=False)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name="single_installation_state")]


class RuntimeClock(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    generation = models.CharField(max_length=36, blank=True, default="")
    elapsed_milliseconds = models.PositiveBigIntegerField(default=0)
    checkpoint_monotonic_ns = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name="single_runtime_clock")]


class UserProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="purevault_profile")
    display_timezone = models.CharField(max_length=128, blank=True, default="")


class VaultEntry(models.Model):
    class RelockPolicy(models.TextChoices):
        AUTOMATIC = "automatic", "Automatic"
        MANUAL = "manual", "Manual"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="vault_entries")
    title = models.CharField(max_length=200)
    encrypted_secret = models.BinaryField(editable=False)
    unlock_delay_seconds = models.PositiveIntegerField(validators=[MinValueValidator(MIN_DURATION_SECONDS), MaxValueValidator(MAX_UNLOCK_DELAY_SECONDS)])
    relock_policy = models.CharField(max_length=16, choices=RelockPolicy.choices, default=RelockPolicy.AUTOMATIC)
    relock_after_seconds = models.PositiveIntegerField(null=True, blank=True, validators=[MinValueValidator(MIN_DURATION_SECONDS), MaxValueValidator(MAX_RELOCK_SECONDS)])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title", "id"]
        constraints = [models.CheckConstraint(condition=(models.Q(relock_policy="manual", relock_after_seconds__isnull=True) | models.Q(relock_policy="automatic", relock_after_seconds__isnull=False)), name="valid_relock_configuration")]


class UnlockGrant(models.Model):
    entry = models.OneToOneField(VaultEntry, on_delete=models.CASCADE, related_name="unlock_grant")
    requested_at = models.DateTimeField()
    eligible_at = models.DateTimeField()
    access_until = models.DateTimeField(null=True, blank=True)
    requested_runtime_ms = models.PositiveBigIntegerField(null=True, blank=True)
    eligible_runtime_ms = models.PositiveBigIntegerField(null=True, blank=True)
    access_until_runtime_ms = models.PositiveBigIntegerField(null=True, blank=True)
    release_count = models.PositiveIntegerField(default=0)
    last_released_at = models.DateTimeField(null=True, blank=True)


class AccountabilityPartner(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="accountability_partners")
    name = models.CharField(max_length=100)
    email = models.EmailField(max_length=254)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]
        constraints = [models.UniqueConstraint(fields=["owner", "email"], name="unique_partner_email_per_owner")]


class EmailSettings(models.Model):
    class Security(models.TextChoices):
        STARTTLS = "starttls", "STARTTLS"
        SSL = "ssl", "SSL/TLS"
        NONE = "none", "Unencrypted"

    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="email_settings")
    enabled = models.BooleanField(default=False)
    host = models.CharField(max_length=255, blank=True, default="")
    port = models.PositiveIntegerField(default=587, validators=[MinValueValidator(1), MaxValueValidator(65535)])
    security = models.CharField(max_length=16, choices=Security.choices, default=Security.STARTTLS)
    username = models.CharField(max_length=255, blank=True, default="")
    encrypted_password = models.BinaryField(null=True, blank=True, editable=False)
    from_email = models.EmailField(max_length=254, blank=True, default="")
    from_name = models.CharField(max_length=100, blank=True, default="")
    revision = models.PositiveBigIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)


class MailDelivery(models.Model):
    class EventType(models.TextChoices):
        UNLOCK_REQUESTED = "unlock_requested", "Unlock requested"
        PARTNER_ADDED = "partner_added", "Partner added"
        PARTNER_REMOVED = "partner_removed", "Partner removed"
        PARTNER_ADDRESS_CHANGED = "partner_address_changed", "Partner address changed"
        NOTIFICATIONS_DISABLED = "notifications_disabled", "Notifications disabled"
        SMTP_CONFIGURATION_CHANGED = "smtp_configuration_changed", "SMTP configuration changed"
        TEST = "test", "Test email"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENDING = "sending", "Sending"
        ACCEPTED = "accepted", "Accepted by mail server"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mail_deliveries")
    event_id = models.UUIDField(default=uuid.uuid4, editable=False)
    delivery_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    event_type = models.CharField(max_length=40, choices=EventType.choices)
    recipient_name = models.CharField(max_length=100, blank=True, default="")
    recipient_email = models.EmailField(max_length=254)
    encrypted_payload = models.BinaryField(editable=False)
    encrypted_transport_snapshot = models.BinaryField(null=True, blank=True, editable=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    attempt_count = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField()
    lease_token = models.UUIDField(null=True, blank=True, editable=False)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    needs_attention = models.BooleanField(default=False)
    last_error = models.CharField(max_length=240, blank=True, default="")
    accepted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [models.UniqueConstraint(fields=["event_id", "recipient_email"], name="unique_recipient_per_mail_event")]
        indexes = [models.Index(fields=["status", "next_attempt_at"], name="mail_delivery_due_idx")]

