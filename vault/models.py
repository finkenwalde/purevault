from django.db import models
from django.utils import timezone

class VaultConfig(models.Model):
    password_hash = models.CharField(max_length=256)
    key_salt = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

class Entry(models.Model):
    title = models.CharField(max_length=160)
    username = models.CharField(max_length=254, blank=True)
    website = models.CharField(max_length=500, blank=True)
    notes = models.TextField(blank=True)
    secret_ciphertext = models.TextField()
    delay_seconds = models.PositiveBigIntegerField(default=86400)
    unlock_duration_seconds = models.PositiveBigIntegerField(default=900)
    unlock_requested_at = models.DateTimeField(null=True, blank=True)
    unlock_at = models.DateTimeField(null=True, blank=True)
    unlock_expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def unlock_pending(self):
        return self.unlock_requested_at is not None and self.unlock_at is not None and timezone.now() < self.unlock_at

    @property
    def unlocked(self):
        now = timezone.now()
        return self.unlock_at is not None and self.unlock_expires_at is not None and self.unlock_at <= now < self.unlock_expires_at

    @property
    def website_href(self):
        if not self.website:
            return ""
        if self.website.lower().startswith(("http://", "https://")):
            return self.website
        return f"https://{self.website}"

    @property
    def delay_display(self):
        for size, label in ((86400, "day"), (3600, "hour"), (60, "minute")):
            if self.delay_seconds % size == 0:
                amount = self.delay_seconds // size
                return f"{amount} {label}{'' if amount == 1 else 's'}"
        return f"{self.delay_seconds} second{'' if self.delay_seconds == 1 else 's'}"

    @property
    def unlock_duration_display(self):
        for size, label in ((86400, "day"), (3600, "hour"), (60, "minute")):
            if self.unlock_duration_seconds % size == 0:
                amount = self.unlock_duration_seconds // size
                return f"{amount} {label}{'' if amount == 1 else 's'}"
        return f"{self.unlock_duration_seconds} second{'' if self.unlock_duration_seconds == 1 else 's'}"
