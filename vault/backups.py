import os
import re
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cryptography.fernet import InvalidToken
from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from vault.backup_format import MAX_ENTRIES, InvalidBackup, pack_backup
from vault.crypto import vault_cipher
from vault.models import BackupSettings, UserProfile, VaultEntry

FILE_ID = re.compile(r"[0-9]{8}T[0-9]{12}Z-[a-f0-9]{32}\.pvbackup")
BACKUP_FAILURE = "Backup failed. Check available storage and the vault encryption key, then try again."


def backup_directory(owner_id):
    return Path(settings.DATA_DIR) / "backups" / str(owner_id)


def backup_path(owner_id, name):
    if not FILE_ID.fullmatch(name):
        return None
    path = backup_directory(owner_id) / name
    return path if not path.is_symlink() and path.is_file() else None


def backup_files(owner_id):
    folder = backup_directory(owner_id)
    if not folder.is_dir():
        return []
    return sorted(
        (path for path in folder.iterdir() if FILE_ID.fullmatch(path.name) and not path.is_symlink() and path.is_file()),
        reverse=True,
    )


def _payload(owner_id):
    zone = UserProfile.objects.filter(user_id=owner_id).values_list("display_timezone", flat=True).first()
    zone = zone or settings.PUREVAULT_DISPLAY_TIMEZONE
    try:
        ZoneInfo(zone)
    except (ValueError, ZoneInfoNotFoundError):
        zone = "UTC"
    rows = list(VaultEntry.objects.filter(owner_id=owner_id).order_by("id")[:MAX_ENTRIES + 1])
    if len(rows) > MAX_ENTRIES:
        raise InvalidBackup
    cipher = vault_cipher() if rows else None
    return {
        "display_timezone": zone,
        "entries": [{
            "title": row.title,
            "password": cipher.decrypt(bytes(row.encrypted_secret)).decode("utf-8"),
            "delay_seconds": row.unlock_delay_seconds,
            "relock_policy": row.relock_policy,
            "relock_seconds": row.relock_after_seconds,
        } for row in rows],
    }


def configure(owner_id, interval, retention, bundle=None):
    with transaction.atomic():
        # A SQLite write first serializes initial profile creation and key rotation.
        User.objects.filter(pk=owner_id).update(last_login=F("last_login"))
        profile = BackupSettings.objects.filter(owner_id=owner_id).first()
        if profile is None:
            if bundle is None:
                return False
            profile = BackupSettings(owner_id=owner_id)
        if bundle is not None:
            profile.public_key, profile.recovery_bundle = bundle
            profile.run_token = None
            profile.lease_until = None
        interval_changed = profile.interval_days != interval
        profile.interval_days = interval
        profile.retention = retention
        if interval_changed or bundle is not None:
            profile.next_run_at = timezone.now() + timedelta(days=interval) if interval else None
        profile.save()
        return True


def _sync_directory(folder):
    if os.name == "nt":
        return  # Windows cannot open a directory with os.open for fsync.
    descriptor = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish(owner_id, token, temporary, name, scheduled):
    with transaction.atomic():
        claimed = BackupSettings.objects.filter(owner_id=owner_id, run_token=token)
        if not claimed.update(lease_until=F("lease_until")):
            return False
        profile = claimed.get()
        folder = backup_directory(owner_id)
        os.replace(temporary, folder / name)
        try:
            _sync_directory(folder)
        except OSError:
            (folder / name).unlink(missing_ok=True)
            raise
        try:
            prior_files = (path for path in backup_files(owner_id) if path.name != name)
            for index, path in enumerate(prior_files, start=1):
                if index >= profile.retention:
                    path.unlink(missing_ok=True)
        except OSError:
            profile.last_error = "Backup saved, but some older copies could not be removed."
        else:
            profile.last_error = ""
        profile.last_success_at = timezone.now()
        if scheduled and profile.interval_days:
            profile.next_run_at = profile.last_success_at + timedelta(days=profile.interval_days)
        profile.run_token = None
        profile.lease_until = None
        profile.save(update_fields=["last_error", "last_success_at", "next_run_at", "run_token", "lease_until"])
        return True


def run_backup(owner_id, *, scheduled=False):
    now = timezone.now()
    token = uuid.uuid4()
    eligible = BackupSettings.objects.filter(owner_id=owner_id).filter(
        Q(lease_until__isnull=True) | Q(lease_until__lte=now),
    )
    if scheduled:
        eligible = eligible.filter(interval_days__gt=0, next_run_at__lte=now)
    with transaction.atomic():
        if not eligible.update(run_token=token, lease_until=now + timedelta(minutes=10)):
            return False
        profile = BackupSettings.objects.get(owner_id=owner_id, run_token=token)
    temporary = None
    try:
        encrypted = pack_backup(_payload(owner_id), bytes(profile.public_key), bytes(profile.recovery_bundle))
        folder = backup_directory(owner_id)
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        name = f"{now:%Y%m%dT%H%M%S%fZ}-{token.hex}.pvbackup"
        with tempfile.NamedTemporaryFile(dir=folder, prefix=".pending-", delete=False) as output:
            temporary = Path(output.name)
            output.write(encrypted)
            output.flush()
            os.fsync(output.fileno())
        return _publish(owner_id, token, temporary, name, scheduled)
    except (OSError, InvalidBackup, InvalidToken, ImproperlyConfigured, ValueError):
        with transaction.atomic():
            claimed = BackupSettings.objects.filter(owner_id=owner_id, run_token=token)
            if claimed.update(run_token=None, lease_until=None, last_error=BACKUP_FAILURE) and scheduled:
                profile = BackupSettings.objects.get(owner_id=owner_id)
                profile.next_run_at = timezone.now() + timedelta(days=profile.interval_days) if profile.interval_days else None
                profile.save(update_fields=["next_run_at"])
        raise InvalidBackup from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def restore_entries(owner_id, zone, entries):
    cipher = vault_cipher() if entries else None
    rows = []
    for entry in entries:
        values = dict(entry)
        encrypted = cipher.encrypt(values.pop("password").encode("utf-8"))
        rows.append(VaultEntry(owner_id=owner_id, encrypted_secret=encrypted, **values))
    with transaction.atomic():
        User.objects.filter(pk=owner_id).update(last_login=F("last_login"))
        if VaultEntry.objects.filter(owner_id=owner_id).exists():
            return False
        VaultEntry.objects.bulk_create(rows)
        UserProfile.objects.update_or_create(user_id=owner_id, defaults={"display_timezone": zone})
        return True
