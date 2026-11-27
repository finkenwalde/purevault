import signal
from threading import Event

from django.core.management.base import BaseCommand
from django.db import OperationalError, close_old_connections
from django.db.models import Q
from django.utils import timezone

from vault.backup_format import InvalidBackup
from vault.backups import run_backup
from vault.models import BackupSettings


class Command(BaseCommand):
    help = "Run due local encrypted backups."

    def handle(self, *args, **options):
        stopping = Event()
        signal.signal(signal.SIGTERM, lambda *_: stopping.set())
        signal.signal(signal.SIGINT, lambda *_: stopping.set())
        while not stopping.is_set():
            close_old_connections()
            try:
                now = timezone.now()
                due = BackupSettings.objects.filter(
                    interval_days__gt=0, next_run_at__lte=now,
                ).filter(Q(lease_until__isnull=True) | Q(lease_until__lte=now))
                for owner_id in due.values_list("owner_id", flat=True):
                    if stopping.is_set():
                        break
                    try:
                        run_backup(owner_id, scheduled=True)
                    except InvalidBackup:
                        pass  # The status endpoint exposes a sanitized failure.
            except OperationalError:
                pass  # Retry transient database contention at the next poll.
            stopping.wait(30)
