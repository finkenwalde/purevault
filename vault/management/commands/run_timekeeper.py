import signal
import time

from django.core.management.base import BaseCommand
from django.db import OperationalError

from vault.services import CLOCK_CHECKPOINT_INTERVAL_SECONDS, advance_runtime_clock


class Command(BaseCommand):
    help = "Continuously checkpoint verified application runtime."

    def handle(self, *args, **options):
        running = True

        def stop(*_args):
            nonlocal running
            running = False

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        failures = 0
        while running:
            try:
                advance_runtime_clock(timekeeper=True)
                failures = 0
            except OperationalError:
                failures += 1
                if failures >= 8:
                    raise
            time.sleep(CLOCK_CHECKPOINT_INTERVAL_SECONDS)
        try:
            advance_runtime_clock(timekeeper=True)
        except OperationalError:
            pass
