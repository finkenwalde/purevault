from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


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

